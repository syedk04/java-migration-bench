"""T4: Retrieval-augmented Gemini agent.

Extends T3 with a version index lookup: for each repo, before the agent
starts, we extract its declared dependencies and inject the latest-known
versions from data/version_index.json into the system prompt. The agent
sees exact target versions without needing to guess or query Maven Central.

Hypothesis (from paper): the version index (lookup) drives the RAG gain
more than general reasoning improvement. We'll test this by comparing T4
vs T3 failure breakdowns (dep-vs-language cause). Report honestly if
the hypothesis is wrong.

CLI:
    uv run python -m migration_agent.migrate_t4 \\
        --pilot --manifest reporting_50.json

    uv run python -m migration_agent.migrate_t4 \\
        --batch --manifest reporting_50.json

Results: workdir/_logs/t4_reporting_50.json
Trajectories: workdir/trajectories/t4/
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from migration_agent.agent_loop import AgentResult
from migration_agent.migrate_t3 import ENGINEERED_SYSTEM_PROMPT
from migration_agent.runner import WORKDIR
from migration_agent.version_index import collect_artifacts

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
TRAJ_DIR = REPO_ROOT / "workdir" / "trajectories" / "t4"

_VERSION_INDEX_PATH = REPO_ROOT / "data" / "version_index.json"

# Maximum number of version entries to inject per repo (keep prompt size sane).
_MAX_VERSION_ENTRIES = 60


# ---------------------------------------------------------------------------
# Version context builder
# ---------------------------------------------------------------------------

def _load_version_map() -> dict[str, str]:
    """Return {groupId:artifactId → latest_version} from the committed index."""
    if not _VERSION_INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(_VERSION_INDEX_PATH.read_text(encoding="utf-8"))
        return data.get("index", {})
    except Exception:  # noqa: BLE001
        return {}


def build_version_context(repo: str, version_map: dict[str, str]) -> str:
    """Return a markdown table of known latest versions for repo's dependencies.

    Returns empty string if no version data is available.
    """
    safe = repo.replace("/", "__")
    repo_dir = WORKDIR / safe
    if not repo_dir.is_dir():
        return ""

    # Collect all (groupId, artifactId) from this repo's pom files.
    artifacts: set[tuple[str, str]] = set()
    for pom in repo_dir.rglob("pom.xml"):
        artifacts |= collect_artifacts(pom)

    rows: list[str] = []
    for g, a in sorted(artifacts):
        key = f"{g}:{a}"
        version = version_map.get(key)
        if version:
            rows.append(f"  {g}:{a} → {version}")

    if not rows:
        return ""

    # Cap length to avoid bloating the prompt.
    if len(rows) > _MAX_VERSION_ENTRIES:
        rows = rows[:_MAX_VERSION_ENTRIES] + [
            f"  ... [{len(rows) - _MAX_VERSION_ENTRIES} more entries truncated]"
        ]

    return (
        "\n## Latest versions from Maven Central (snapshot 2024-11)\n\n"
        + "\n".join(rows)
        + "\n\nUse these versions when bumping dependencies for the maximal score.\n"
    )


# ---------------------------------------------------------------------------
# Per-repo system prompt with version context injected
# ---------------------------------------------------------------------------

class _VersionAugmentedBatch:
    """Wraps run_batch with per-repo system prompt injection.

    run_batch accepts a single system_prompt string. To inject per-repo
    version context we monkey-patch at the batch level: we run each repo
    individually with its own augmented prompt, collecting results manually.
    """

    def __init__(
        self,
        version_map: dict[str, str],
        base_prompt: str,
        track: str,
        out_path: Path,
        trajectory_dir: Path,
        gemini_log: Path | None,
        api_key: str | None,
    ) -> None:
        self._version_map = version_map
        self._base_prompt = base_prompt
        self._track = track
        self._out_path = out_path
        self._trajectory_dir = trajectory_dir
        self._gemini_log = gemini_log
        self._api_key = api_key

    def run(self, manifest_path: Path) -> list[AgentResult]:
        from migration_agent.agent_loop import run_agent
        from migration_agent.gemini_client import GeminiRateLimitError

        entries = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Resume: load already-done.
        done: dict[str, dict] = {}
        if self._out_path.exists():
            try:
                for r in json.loads(self._out_path.read_text(encoding="utf-8")):
                    done[r["repo"]] = r
            except Exception:  # noqa: BLE001
                pass

        results: list[AgentResult] = []
        for d in done.values():
            results.append(AgentResult(
                repo=d["repo"], base_commit=d["base_commit"], track=d["track"],
                minimal=d["minimal"], maximal=d["maximal"],
                calls_used=d.get("calls_used", 0), skipped=True,
            ))

        for i, entry in enumerate(entries, start=1):
            repo, base_commit = entry["repo"], entry["base_commit"]
            if repo in done:
                print(f"[{i}/{len(entries)}] {repo} SKIP", flush=True)
                continue

            version_ctx = build_version_context(repo, self._version_map)
            system_prompt = self._base_prompt + version_ctx

            print(
                f"[{i}/{len(entries)}] {repo}@{base_commit[:12]} "
                f"(+{version_ctx.count(chr(10))} version lines)",
                flush=True,
            )
            t0 = time.monotonic()
            try:
                ar = run_agent(
                    repo, base_commit, system_prompt,
                    track=self._track,
                    trajectory_dir=self._trajectory_dir,
                    gemini_log=self._gemini_log,
                    api_key=self._api_key,
                )
            except GeminiRateLimitError as exc:
                print(f"  QUOTA EXHAUSTED: {exc}", flush=True)
                break
            except Exception as exc:  # noqa: BLE001
                ar = AgentResult(
                    repo=repo, base_commit=base_commit, track=self._track,
                    minimal=False, maximal=False,
                    error=str(exc),
                    seconds=round(time.monotonic() - t0, 1),
                )

            status = "PASS" if ar.minimal else "FAIL"
            print(
                f"  -> {status} in {ar.seconds}s  "
                f"calls={ar.calls_used}  "
                f"tokens={ar.total_prompt_tokens}in/{ar.total_completion_tokens}out",
                flush=True,
            )
            results.append(ar)
            self._out_path.write_text(
                json.dumps([r.to_dict() for r in results], indent=2) + "\n"
            )

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _projection(results: list[AgentResult]) -> str:
    if not results:
        return ""
    total_calls = sum(r.calls_used for r in results)
    avg_calls = total_calls / len(results)
    rpm, rpd = 14, 1400
    secs_per_repo = avg_calls * (60.0 / rpm)
    lines = [
        f"  avg calls/repo : {avg_calls:.1f}",
        f"  @ 14 RPM       : {secs_per_repo/60:.1f} min/repo",
        f"  projected RPD  : {rpd} / {avg_calls:.1f} = {rpd/avg_calls:.0f} repos/day",
        f"  50 repos @ RPD : {50/(rpd/avg_calls)*24:.1f} hours",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="T4: retrieval-augmented Gemini agent.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true")
    mode.add_argument("--batch", action="store_true")
    parser.add_argument("--manifest", default="reporting_50.json")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    if not _VERSION_INDEX_PATH.exists():
        print(
            "WARNING: data/version_index.json not found — T4 will run without "
            "version context (equivalent to T3). Run S20 first:\n"
            "  uv run python -m migration_agent.version_index",
            file=sys.stderr,
        )

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / args.manifest
    all_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = all_entries[:5] if args.pilot else all_entries

    if args.pilot:
        tmp_manifest = LOGS_DIR / "t4_pilot_5.json"
        tmp_manifest.write_text(json.dumps(entries) + "\n", encoding="utf-8")
        manifest_path = tmp_manifest
        out_path = LOGS_DIR / "t4_pilot_5_results.json"
    else:
        out_path = LOGS_DIR / f"t4_{args.manifest}"

    version_map = _load_version_map()
    print(f"Version index: {len(version_map)} entries loaded", flush=True)

    runner = _VersionAugmentedBatch(
        version_map=version_map,
        base_prompt=ENGINEERED_SYSTEM_PROMPT,
        track="T4",
        out_path=out_path,
        trajectory_dir=TRAJ_DIR,
        gemini_log=LOGS_DIR / "t4_gemini_calls.jsonl",
        api_key=api_key,
    )

    print(f"T4 ({'pilot 5' if args.pilot else 'full batch'}) — {args.manifest}", flush=True)
    t0 = time.monotonic()
    results = runner.run(manifest_path)

    elapsed = round(time.monotonic() - t0, 1)
    n = len(results)
    minimal = sum(1 for r in results if r.minimal)
    maximal = sum(1 for r in results if r.maximal)

    print(f"\nT4 results ({n} repos, {elapsed}s):")
    print(f"  minimal: {minimal}/{n} = {100*minimal/n:.2f}%")
    print(f"  maximal: {maximal}/{n} = {100*maximal/n:.2f}%")

    if args.pilot:
        print("\nThroughput projection:")
        print(_projection(results))

    print(f"\nResults written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
