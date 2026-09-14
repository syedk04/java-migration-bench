"""S24: Gemini 2.5 Flash-Lite second model arm.

Reruns T3 and T4 with Flash-Lite (smaller/cheaper model) to measure whether
the engineered prompt and retrieval gains hold on a weaker model.

Flash-Lite free tier: same 14 RPM / 1400 RPD as Flash — budget identical.
Model ID: gemini-2.5-flash-lite-preview-06-17  (latest Flash-Lite as of Aug 2025)

CLI:
    uv run python -m migration_agent.migrate_t24_lite \\
        --track T3-lite --pilot --manifest reporting_50.json

    uv run python -m migration_agent.migrate_t24_lite \\
        --track T4-lite --batch --manifest reporting_50.json

Results: workdir/_logs/t3_lite_reporting_50.json / t4_lite_reporting_50.json
Trajectories: workdir/trajectories/t3_lite/ or t4_lite/
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from migration_agent.agent_loop import AgentResult, run_batch
from migration_agent.migrate_t3 import ENGINEERED_SYSTEM_PROMPT
from migration_agent.migrate_t4 import _load_version_map, _VersionAugmentedBatch

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"

_FLASH_LITE_MODEL = "gemini-2.5-flash-lite-preview-06-17"

_TRACK_CONFIG = {
    "T3-lite": {
        "traj_dir": REPO_ROOT / "workdir" / "trajectories" / "t3_lite",
        "out_name": "t3_lite",
        "use_version_index": False,
    },
    "T4-lite": {
        "traj_dir": REPO_ROOT / "workdir" / "trajectories" / "t4_lite",
        "out_name": "t4_lite",
        "use_version_index": True,
    },
}


def _projection(results: list[AgentResult]) -> str:
    if not results:
        return ""
    total_calls = sum(r.calls_used for r in results)
    avg_calls = total_calls / len(results)
    rpm, rpd = 14, 1400
    secs_per_repo = avg_calls * (60.0 / rpm)
    return (
        f"  avg calls/repo : {avg_calls:.1f}\n"
        f"  @ 14 RPM       : {secs_per_repo/60:.1f} min/repo\n"
        f"  projected RPD  : {rpd} / {avg_calls:.1f} = {rpd/avg_calls:.0f} repos/day"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S24: Flash-Lite model arm (T3/T4 with gemini-2.5-flash-lite)."
    )
    parser.add_argument("--track", required=True, choices=list(_TRACK_CONFIG),
                        help="T3-lite or T4-lite.")
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

    cfg = _TRACK_CONFIG[args.track]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / args.manifest
    all_entries = json.loads(manifest_path.read_text())
    entries = all_entries[:5] if args.pilot else all_entries

    if args.pilot:
        tmp = LOGS_DIR / f"{cfg['out_name']}_pilot_5.json"
        tmp.write_text(json.dumps(entries) + "\n")
        manifest_path = tmp
        out_path = LOGS_DIR / f"{cfg['out_name']}_pilot_5_results.json"
    else:
        out_path = LOGS_DIR / f"{cfg['out_name']}_{args.manifest}"

    gemini_log = LOGS_DIR / f"{cfg['out_name']}_gemini_calls.jsonl"
    traj_dir: Path = cfg["traj_dir"]  # type: ignore[assignment]

    print(f"{args.track} ({'pilot 5' if args.pilot else 'full batch'}) "
          f"model={_FLASH_LITE_MODEL}", flush=True)

    t0 = time.monotonic()

    if cfg["use_version_index"]:
        version_map = _load_version_map()
        print(f"Version index: {len(version_map)} entries", flush=True)
        runner = _VersionAugmentedBatch(
            version_map=version_map,
            base_prompt=ENGINEERED_SYSTEM_PROMPT,
            track=args.track,
            out_path=out_path,
            trajectory_dir=traj_dir,
            gemini_log=gemini_log,
            api_key=api_key,
        )
        # Patch the client model inside the runner via env (GeminiClient reads
        # GEMINI_MODEL if set, otherwise defaults to gemini-2.5-flash).
        os.environ["GEMINI_MODEL"] = _FLASH_LITE_MODEL
        results = runner.run(manifest_path)
    else:
        # T3-lite: use run_batch but override model via env var.
        os.environ["GEMINI_MODEL"] = _FLASH_LITE_MODEL
        results = run_batch(
            manifest_path,
            ENGINEERED_SYSTEM_PROMPT,
            track=args.track,
            out_path=out_path,
            trajectory_dir=traj_dir,
            gemini_log=gemini_log,
            api_key=api_key,
        )

    elapsed = round(time.monotonic() - t0, 1)
    n = len(results)
    minimal = sum(1 for r in results if r.minimal)
    maximal = sum(1 for r in results if r.maximal)

    print(f"\n{args.track} results ({n} repos, {elapsed}s):")
    print(f"  minimal: {minimal}/{n} = {100*minimal/n:.2f}%")
    print(f"  maximal: {maximal}/{n} = {100*maximal/n:.2f}%")
    if args.pilot:
        print("\nThroughput projection:")
        print(_projection(results))

    print(f"\nResults written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
