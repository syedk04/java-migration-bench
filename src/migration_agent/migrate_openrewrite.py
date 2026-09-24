"""S11: OpenRewrite T1 migration — UpgradeToJava17 recipe.

Runs the OpenRewrite rewrite-maven-plugin with the UpgradeToJava17 recipe
inside the sandbox container, then runs the full verifier + tamper gate.

Expected calibration target (paper Section 5, n=300):
  minimal: ~16.33%
  maximal:  ~2.00%

This is a HARD GATE: run on all 50, check both numbers before proceeding
to the LLM tracks. If the numbers are materially off, the harness is wrong
and every later number is worthless.

CLI (single repo):
    uv run python -m migration_agent.migrate_openrewrite \\
        --repo owner/name --base-commit <sha>

CLI (batch):
    uv run python -m migration_agent.migrate_openrewrite \\
        --batch --manifest reporting_50.json
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from migration_agent.maximal import check_maximal_effective, load_version_index
from migration_agent.runner import IMAGE, M2_VOLUME, WORKDIR, clone_at_commit
from migration_agent.tamper import check_tamper, snapshot_tests
from migration_agent.verifier import verify

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
RESULTS_DIR = REPO_ROOT / "workdir" / "_logs"

# Pinned to the versions the reporting T1 run resolved (2026-09-14, the only
# versions in the .m2 volume). RELEASE + -U drifts between runs, so a rerun
# could apply a different recipe than the one that produced the T1 numbers.
_REWRITE_PLUGIN = "org.openrewrite.maven:rewrite-maven-plugin:6.46.1"
_REWRITE_RECIPE_COORDS = (
    "org.openrewrite.recipe:rewrite-migrate-java:3.42.1"
)
_ACTIVE_RECIPE = "org.openrewrite.java.migrate.UpgradeToJava17"


def run_openrewrite(repo_dir: Path) -> subprocess.CompletedProcess:
    """Run OpenRewrite UpgradeToJava17 in the sandbox container.

    Modifies repo_dir in place (the container writes back through the
    volume mount). Plugin and recipe versions are pinned for reproducibility.
    """
    command = (
        "cd /workspace && "
        f"mvn -B {_REWRITE_PLUGIN}:run "
        f"-Drewrite.recipeArtifactCoordinates={_REWRITE_RECIPE_COORDS} "
        f"-Drewrite.activeRecipes={_ACTIVE_RECIPE}"
    )
    # 1800 s (30 min) hard wall: OpenRewrite on large multi-module projects can
    # take 30+ minutes parsing Kotlin/Groovy/Java sources before applying the
    # recipe. subprocess.TimeoutExpired is caught in run_batch() and recorded
    # as an error, not a hang.
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{repo_dir.resolve()}:/workspace",
            "-v", f"{M2_VOLUME}:/root/.m2",
            IMAGE, "bash", "-c", command,
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )


def run_one(repo: str, base_commit: str) -> dict:
    """Clone, apply OpenRewrite, verify, return record dict."""
    dest = WORKDIR / repo.replace("/", "__")
    record: dict = {"repo": repo, "base_commit": base_commit, "track": "T1"}

    clone_at_commit(repo, base_commit, dest)
    snap = snapshot_tests(dest)

    or_result = run_openrewrite(dest)
    record["openrewrite_exit"] = or_result.returncode
    if or_result.returncode != 0:
        record["openrewrite_tail"] = (
            or_result.stdout + or_result.stderr
        )[-1000:]

    vr = verify(dest)
    record["r1"] = vr.r1_build
    record["r2"] = vr.r2_bytecode
    record["r2_detail"] = vr.r2_detail

    if vr.r1_build and vr.r2_bytecode:
        tr = check_tamper(snap, dest)
        record["tampered"] = tr.tampered
        record["tamper_violations"] = tr.violations
        # r5 on resolved versions (mvn dependency:tree) vs the paper reference
        index = load_version_index()
        mr = check_maximal_effective(dest, index)
        record["r5_maximal"] = mr.passed
        record["maximal_detail"] = mr.detail
    else:
        record["tampered"] = False
        record["r5_maximal"] = False
        if not vr.r1_build:
            record["tail"] = vr.maven_tail[-1500:]

    record["minimal"] = (
        vr.r1_build and vr.r2_bytecode and not record.get("tampered", False)
    )
    record["maximal"] = record["minimal"] and record.get("r5_maximal", False)
    return record


def run_batch(manifest_path: Path, out: Path) -> list[dict]:
    """Run batch, writing each result immediately so crashes don't lose progress.

    If *out* already exists and contains valid JSON, repos already recorded
    are skipped (resume mode).
    """
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Load any already-completed results for resumption.
    done: dict[str, dict] = {}
    if out.exists():
        try:
            for r in json.loads(out.read_text(encoding="utf-8")):
                done[r["repo"]] = r
        except Exception:  # noqa: BLE001
            pass  # corrupt partial file — start fresh

    results: list[dict] = list(done.values())

    for i, entry in enumerate(entries, start=1):
        repo, base_commit = entry["repo"], entry["base_commit"]
        if repo in done:
            print(f"[{i}/{len(entries)}] {repo} SKIP (already done)")
            sys.stdout.flush()
            continue
        print(f"[{i}/{len(entries)}] {repo}@{base_commit[:12]}", flush=True)
        start = time.monotonic()
        try:
            record = run_one(repo, base_commit)
        except Exception as exc:  # noqa: BLE001
            record = {
                "repo": repo, "base_commit": base_commit, "track": "T1",
                "r1": False, "r2": False, "minimal": False, "maximal": False,
                "error": str(exc),
            }
        record["seconds"] = round(time.monotonic() - start, 1)
        status = "PASS" if record.get("minimal") else "FAIL"
        print(f"  -> {status} in {record['seconds']}s", flush=True)
        results.append(record)
        # Write incrementally so a crash doesn't lose all progress.
        out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T1: OpenRewrite UpgradeToJava17, single repo or batch."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--repo", help="owner/name (single-repo mode)")
    mode.add_argument("--batch", action="store_true")
    parser.add_argument("--base-commit", help="required with --repo")
    parser.add_argument("--manifest", default="reporting_50.json")
    args = parser.parse_args()

    if args.batch:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFEST_DIR / args.manifest
        out = RESULTS_DIR / f"t1_{args.manifest}"
        results = run_batch(manifest_path, out)
        minimal = sum(1 for r in results if r.get("minimal"))
        maximal = sum(1 for r in results if r.get("maximal"))
        n = len(results)
        print(f"\nT1 results ({n} repos):")
        print(f"  minimal: {minimal}/{n} = {100*minimal/n:.2f}%")
        print(f"  maximal: {maximal}/{n} = {100*maximal/n:.2f}%")
        print("  (paper calibration target: ~16.33% minimal, ~2.00% maximal)")
        print(f"results written to {out}", flush=True)
    else:
        if not args.base_commit:
            parser.error("--base-commit required with --repo")
        start = time.monotonic()
        record = run_one(args.repo, args.base_commit)
        elapsed = round(time.monotonic() - start, 1)
        print(f"r1={record['r1']} r2={record['r2']} minimal={record['minimal']} "
              f"maximal={record['maximal']} ({elapsed}s)")


if __name__ == "__main__":
    main()
