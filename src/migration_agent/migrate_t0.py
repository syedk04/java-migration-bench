"""T0 migration: seed-only — bump maven.compiler.source/target to 17.

This is the simplest possible migration: no LLM, no OpenRewrite, just
set the compiler version in every pom.xml. Expected to achieve low minimal
success (repos that were already Java-17-compatible at the API level) and
zero maximal success (no dependency bumps).

Running this establishes the floor, and its pass rate through the verifier
provides a sanity check that the harness is wired correctly.

CLI (single repo):
    uv run python -m migration_agent.migrate_t0 --repo owner/name \\
        --base-commit <sha>

CLI (batch, all 50):
    uv run python -m migration_agent.migrate_t0 --batch \\
        --manifest reporting_50.json
"""

import argparse
import json
import re
import time
from pathlib import Path

from migration_agent.maximal import check_maximal_effective, load_version_index
from migration_agent.runner import WORKDIR, clone_at_commit
from migration_agent.verifier import verify

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
RESULTS_DIR = REPO_ROOT / "workdir" / "_logs"

# Patterns to find existing compiler source/target settings
_SOURCE_RE = re.compile(
    r"(<maven\.compiler\.source\s*>)\s*[\d.]+\s*(</maven\.compiler\.source>)",
    re.IGNORECASE,
)
_TARGET_RE = re.compile(
    r"(<maven\.compiler\.target\s*>)\s*[\d.]+\s*(</maven\.compiler\.target>)",
    re.IGNORECASE,
)
# Also handle <release> property
_RELEASE_RE = re.compile(
    r"(<maven\.compiler\.release\s*>)\s*[\d.]+\s*(</maven\.compiler\.release>)",
    re.IGNORECASE,
)
# Compiler plugin <source> / <target> / <release> configuration tags
_PLUGIN_SOURCE_RE = re.compile(
    r"(<source\s*>)\s*[\d.]+\s*(</source>)",
)
_PLUGIN_TARGET_RE = re.compile(
    r"(<target\s*>)\s*[\d.]+\s*(</target>)",
)
_PLUGIN_RELEASE_RE = re.compile(
    r"(<release\s*>)\s*[\d.]+\s*(</release>)",
)

_TARGET_VERSION = "17"


def _patch_pom(pom_path: Path) -> bool:
    """Bump all compiler source/target/release references to 17 in one pom.xml.

    Returns True if any change was made.
    """
    original = pom_path.read_text(encoding="utf-8", errors="replace")
    text = original

    for pattern in (
        _SOURCE_RE, _TARGET_RE, _RELEASE_RE,
        _PLUGIN_SOURCE_RE, _PLUGIN_TARGET_RE, _PLUGIN_RELEASE_RE,
    ):
        text = pattern.sub(rf"\g<1>{_TARGET_VERSION}\g<2>", text)

    if text == original:
        return False
    pom_path.write_text(text, encoding="utf-8")
    return True


def _inject_compiler_properties(pom_path: Path) -> bool:
    """If no compiler settings found, inject source/target/release into <properties>.

    Returns True if any change was made.
    """
    text = pom_path.read_text(encoding="utf-8", errors="replace")
    # Check if this pom already has any compiler setting after our patch pass
    has_setting = any(
        p.search(text)
        for p in (_SOURCE_RE, _TARGET_RE, _RELEASE_RE,
                  _PLUGIN_SOURCE_RE, _PLUGIN_TARGET_RE, _PLUGIN_RELEASE_RE)
    )
    if has_setting:
        return False

    # Inject into <properties> block if it exists
    inject = (
        f"\n    <maven.compiler.source>{_TARGET_VERSION}</maven.compiler.source>"
        f"\n    <maven.compiler.target>{_TARGET_VERSION}</maven.compiler.target>"
        f"\n    <maven.compiler.release>{_TARGET_VERSION}</maven.compiler.release>"
    )
    if "<properties>" in text:
        text = text.replace("<properties>", "<properties>" + inject, 1)
    elif "</project>" in text:
        # No <properties> block — add one before </project>
        props = (
            f"\n  <properties>{inject}\n  </properties>"
        )
        text = text.replace("</project>", props + "\n</project>", 1)
    else:
        return False

    pom_path.write_text(text, encoding="utf-8")
    return True


def apply_t0(repo_dir: Path) -> dict:
    """Apply the T0 migration (compiler bump to 17) to all poms in repo_dir.

    Returns a summary dict with counts of poms changed/injected/unchanged.
    """
    patched = injected = unchanged = 0
    for pom in repo_dir.rglob("pom.xml"):
        if _patch_pom(pom):
            patched += 1
        elif _inject_compiler_properties(pom):
            injected += 1
        else:
            unchanged += 1
    return {"patched": patched, "injected": injected, "unchanged": unchanged}


def run_batch(manifest_path: Path) -> list[dict]:
    """Run T0 on every repo in manifest_path and return per-repo records."""
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    for i, entry in enumerate(entries, start=1):
        repo, base_commit = entry["repo"], entry["base_commit"]
        dest = WORKDIR / repo.replace("/", "__")
        print(f"[{i}/{len(entries)}] {repo}@{base_commit[:12]}")
        start = time.monotonic()
        record: dict = {"repo": repo, "base_commit": base_commit, "track": "T0"}
        try:
            clone_at_commit(repo, base_commit, dest)
            apply_t0(dest)
            vr = verify(dest)
            record["r1"] = vr.r1_build
            record["r2"] = vr.r2_bytecode
            record["minimal"] = vr.r1_build and vr.r2_bytecode
            record["exit_code"] = vr.exit_code
            record["r2_detail"] = vr.r2_detail
            if not vr.r1_build:
                record["tail"] = vr.maven_tail[-1500:]
            # T0 never touches tests, so no tamper check; r5 only matters
            # when minimal passed (maximal = minimal and r5).
            if record["minimal"]:
                mr = check_maximal_effective(dest, load_version_index())
                record["r5_maximal"] = mr.passed
                record["maximal_detail"] = mr.detail
            record["maximal"] = record["minimal"] and record.get("r5_maximal", False)
        except Exception as exc:  # noqa: BLE001
            record["r1"] = record["r2"] = record["minimal"] = record["maximal"] = False
            record["error"] = str(exc)
        record["seconds"] = round(time.monotonic() - start, 1)
        status = "PASS" if record.get("minimal") else "FAIL"
        print(f"  -> {status} in {record['seconds']}s")
        results.append(record)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T0: bump compiler to Java 17, single repo or full batch."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--repo", help="owner/name (single-repo mode)")
    mode.add_argument("--batch", action="store_true", help="run full manifest")
    parser.add_argument("--base-commit", help="required with --repo")
    parser.add_argument("--manifest", default="reporting_50.json",
                        help="manifest filename under manifests/ (batch mode)")
    args = parser.parse_args()

    if args.batch:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFEST_DIR / args.manifest
        results = run_batch(manifest_path)
        out = RESULTS_DIR / f"t0_{args.manifest}"
        out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        passed = sum(1 for r in results if r.get("minimal"))
        print(f"\n{passed}/{len(results)} minimal pass (T0)")
        print(f"results written to {out}")
    else:
        if not args.base_commit:
            parser.error("--base-commit required with --repo")
        dest = WORKDIR / args.repo.replace("/", "__")
        print(f"cloning {args.repo}@{args.base_commit[:12]}")
        clone_at_commit(args.repo, args.base_commit, dest)
        summary = apply_t0(dest)
        print(
            f"T0 applied: {summary['patched']} poms patched, "
            f"{summary['injected']} injected, "
            f"{summary['unchanged']} unchanged"
        )
        vr = verify(dest)
        print(f"r1={vr.r1_build} r2={vr.r2_bytecode} — {vr.r2_detail}")


if __name__ == "__main__":
    main()
