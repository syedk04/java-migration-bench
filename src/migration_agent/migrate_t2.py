"""T2: Naive Gemini agent — no prompt engineering, no retrieval.

This is the baseline LLM track. The agent receives only the task description
and tool list; no Java 8→17 playbook, no version index, no examples.

CLI (5-repo pilot — ALWAYS run pilot first, show numbers, wait for approval):
    uv run python -m migration_agent.migrate_t2 \\
        --pilot --manifest reporting_50.json

CLI (full 50):
    uv run python -m migration_agent.migrate_t2 \\
        --batch --manifest reporting_50.json

The pilot runs repos 1-5 from the manifest and prints per-repo calls/tokens
so you can project daily throughput before committing the full run to quota.

Results written to workdir/_logs/t2_reporting_50.json (incremental, resumable).
Trajectories in workdir/trajectories/t2/.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from migration_agent.agent_loop import AgentResult, run_batch
from migration_agent.results import build_table, summarise

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
TRAJ_DIR = REPO_ROOT / "workdir" / "trajectories" / "t2"

# ---------------------------------------------------------------------------
# Naive system prompt — no engineering, no playbook
# ---------------------------------------------------------------------------

NAIVE_SYSTEM_PROMPT = """\
You are an expert Java developer. Your task is to migrate a Maven project \
from Java 8 to Java 17 so that `mvn clean verify` passes with all tests \
passing under Java 17.

Use the available tools to explore the repository, understand the code, \
make necessary changes, and verify the result. When you believe the \
migration is complete, output DONE on a line by itself.

Important: do not disable, skip, or remove any tests. The migration must \
pass with the original test suite intact.
"""


def _projection(results: list[AgentResult]) -> str:
    """Print a throughput projection based on pilot results."""
    if not results:
        return ""
    total_calls = sum(r.calls_used for r in results)
    avg_calls = total_calls / len(results)
    rpm = 14
    rpd = 1400
    if avg_calls == 0:
        return "  (no LLM calls made — API error during pilot)"
    # Time per repo assuming avg_calls at 1 call per interval.
    secs_per_repo = avg_calls * (60.0 / rpm)
    lines = [
        f"  avg calls/repo : {avg_calls:.1f}",
        f"  @ 14 RPM       : {secs_per_repo/60:.1f} min/repo",
        f"  projected RPD  : {rpd} / {avg_calls:.1f} = {rpd/avg_calls:.0f} repos/day",
        f"  wall-clock/day : {86400/secs_per_repo:.0f} repos/day (unlimited)",
        f"  50 repos @ RPD bound: {50/(rpd/avg_calls)*24:.1f} hours",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T2: naive Gemini agent baseline."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true",
                      help="Run first 5 repos and report projections.")
    mode.add_argument("--batch", action="store_true",
                      help="Run all repos in manifest.")
    parser.add_argument("--manifest", default="reporting_50.json")
    parser.add_argument("--api-key", default=None,
                        help="Gemini API key (or set GEMINI_API_KEY env var).")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: No Gemini API key. Set GEMINI_API_KEY or pass --api-key.",
              file=sys.stderr)
        sys.exit(1)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / args.manifest

    all_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = all_entries[:5] if args.pilot else all_entries

    # Write a pilot-specific or full manifest slice to a temp file so
    # run_batch can read it.
    if args.pilot:
        tmp_manifest = LOGS_DIR / "t2_pilot_5.json"
        tmp_manifest.write_text(json.dumps(entries) + "\n", encoding="utf-8")
        manifest_path = tmp_manifest
        out_path = LOGS_DIR / "t2_pilot_5_results.json"
    else:
        out_path = LOGS_DIR / f"t2_{args.manifest}"

    gemini_log = LOGS_DIR / "t2_gemini_calls.jsonl"

    print(f"T2 ({'pilot 5' if args.pilot else 'full batch'}) — {args.manifest}")
    print(f"Trajectories: {TRAJ_DIR}")
    print(f"Results: {out_path}", flush=True)

    t0 = time.monotonic()
    results = run_batch(
        manifest_path,
        NAIVE_SYSTEM_PROMPT,
        track="T2",
        out_path=out_path,
        trajectory_dir=TRAJ_DIR,
        gemini_log=gemini_log,
        api_key=api_key,
    )

    elapsed = round(time.monotonic() - t0, 1)
    n = len(results)
    minimal = sum(1 for r in results if r.minimal)
    maximal = sum(1 for r in results if r.maximal)

    print(f"\nT2 results ({n} repos, {elapsed}s):")
    print(f"  minimal: {minimal}/{n} = {100*minimal/n:.2f}%")
    print(f"  maximal: {maximal}/{n} = {100*maximal/n:.2f}%")

    if args.pilot:
        print("\nThroughput projection:")
        print(_projection(results))
        print(
            "\n*** S16 HARD GATE ***\n"
            "Show the numbers above to the user before running the full 50.\n"
            "Check calls/tokens per repo and the RPD projection.\n"
            "If anything looks wrong (0 calls, quota errors, etc.) investigate first."
        )
    else:
        s = summarise([r.to_dict() for r in results], "T2")
        print(build_table([s]))

    print(f"\nResults written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
