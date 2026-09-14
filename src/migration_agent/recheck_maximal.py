"""Recheck maximal scores for completed result files.

Bug fix: load_version_index() was returning the outer wrapper dict instead
of the nested index, making check_maximal always pass vacuously. This script
re-runs check_maximal on the cloned repos and patches the JSON results files.

CLI:
    uv run python -m migration_agent.recheck_maximal
    uv run python -m migration_agent.recheck_maximal --tracks T0 T1
"""

import argparse
import json
from pathlib import Path

from migration_agent.maximal import check_maximal, load_version_index
from migration_agent.runner import WORKDIR

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"

_RESULT_FILES = {
    "T0": "t0_reporting_50.json",
    "T1": "t1_reporting_50.json",
    "T2": "t2_reporting_50.json",
    "T3": "t3_reporting_50.json",
    "T4": "t4_reporting_50.json",
}


def recheck_track(track: str, filename: str, index: dict) -> int:
    path = LOGS_DIR / filename
    if not path.exists():
        print(f"[{track}] {filename} not found — skipping")
        return 0

    records = json.loads(path.read_text())
    changed = 0

    for r in records:
        repo = r["repo"]
        safe = repo.replace("/", "__")
        repo_dir = WORKDIR / safe

        if not repo_dir.is_dir():
            # Repo not cloned locally — skip (maximal stays as-is)
            continue

        mr = check_maximal(repo_dir, index)
        old_r5 = r.get("r5_maximal")
        new_r5 = mr.passed
        new_maximal = r.get("minimal", False) and new_r5

        if old_r5 != new_r5 or r.get("maximal") != new_maximal:
            print(
                f"  {repo}: r5_maximal {old_r5} -> {new_r5}  "
                f"maximal {r.get('maximal')} -> {new_maximal}  "
                f"({mr.detail})"
            )
            r["r5_maximal"] = new_r5
            r["maximal"] = new_maximal
            r["maximal_detail"] = mr.detail
            changed += 1

    if changed:
        path.write_text(json.dumps(records, indent=2) + "\n")
        print(f"[{track}] Updated {changed} records in {filename}")
    else:
        print(f"[{track}] No changes needed")

    minimal_k = sum(1 for r in records if r.get("minimal"))
    maximal_k = sum(1 for r in records if r.get("maximal"))
    n = len(records)
    print(
        f"[{track}] n={n}  minimal={minimal_k}/{n}={100*minimal_k/n:.1f}%  "
        f"maximal={maximal_k}/{n}={100*maximal_k/n:.1f}%"
    )
    return changed


def main(tracks: list[str] | None = None) -> None:
    index = load_version_index()
    if not index:
        print("ERROR: version index is empty — run S20 first.")
        return
    print(f"Loaded version index: {len(index)} entries")

    targets = tracks or list(_RESULT_FILES.keys())
    total_changed = 0
    for track in targets:
        filename = _RESULT_FILES.get(track)
        if not filename:
            print(f"Unknown track: {track}")
            continue
        print(f"\nRechecking {track}...")
        total_changed += recheck_track(track, filename, index)

    print(f"\nTotal records updated: {total_changed}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Recheck maximal scores with fixed index.")
    parser.add_argument("--tracks", nargs="+", default=None,
                        choices=list(_RESULT_FILES.keys()),
                        help="Which tracks to recheck (default: all).")
    args = parser.parse_args()
    main(tracks=args.tracks)
