"""Re-score r5 (maximal) for finished tracks with the effective-version check.

The shared workdir/<repo> clone holds whatever ran last, so scoring it
directly can grade one track's r5 on another track's code. Instead, each
minimal-passing repo is rebuilt from scratch in workdir/_recheck/:

    T0      clone + apply_t0 (deterministic pom edit)
    T1      clone + OpenRewrite (plugin/recipe pinned, reproduces the run)
    T2+     clone + git apply of the saved <trajectories>/<track>/<repo>.diff

then scored with check_maximal_effective against the paper's reference
list. Records that failed minimal can't pass maximal and are not rebuilt.

CLI:
    uv run python -m migration_agent.recheck_maximal --tracks T0 T1
"""

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from migration_agent.maximal import MaximalResult, check_maximal_effective, load_version_index
from migration_agent.runner import WORKDIR, _rmtree, clone_at_commit

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
TRAJ_ROOT = REPO_ROOT / "workdir" / "trajectories"
RECHECK_DIR = WORKDIR / "_recheck"

_RESULT_FILES = {
    "T0": "t0_reporting_50.json",
    "T1": "t1_reporting_50.json",
    "T2": "t2_reporting_50.json",
    "T3": "t3_reporting_50.json",
    "T4": "t4_reporting_50.json",
}


def materialize(track: str, repo: str, base_commit: str, dest: Path) -> None:
    """Rebuild the migrated working tree *track* produced for *repo*."""
    clone_at_commit(repo, base_commit, dest)
    if track == "T0":
        from migration_agent.migrate_t0 import apply_t0

        apply_t0(dest)
    elif track == "T1":
        from migration_agent.migrate_openrewrite import run_openrewrite

        proc = run_openrewrite(dest)
        if proc.returncode != 0:
            raise RuntimeError(f"OpenRewrite exit {proc.returncode}")
    else:
        diff = TRAJ_ROOT / track.lower() / f"{repo.replace('/', '__')}.diff"
        if not diff.exists():
            raise FileNotFoundError(f"no saved diff at {diff}")
        subprocess.run(["git", "-C", str(dest), "apply", str(diff)], check=True)


def rescore_records(
    records: list[dict], score: Callable[[dict], MaximalResult]
) -> int:
    """Update r5/maximal fields in place; return how many records changed.

    Only minimal passes are scored. A scoring error fails r5 and records
    the reason rather than silently keeping the old value.
    """
    changed = 0
    for r in records:
        before = (r.get("r5_maximal"), r.get("maximal"), r.get("maximal_detail"))
        if not r.get("minimal"):
            r["maximal"] = False
        else:
            try:
                mr = score(r)
                r["r5_maximal"] = mr.passed
                r["maximal_detail"] = mr.detail
                r["maximal_outdated"] = mr.outdated
            except Exception as exc:  # noqa: BLE001
                r["r5_maximal"] = False
                r["maximal_detail"] = f"recheck error: {exc}"
            r["maximal"] = bool(r["r5_maximal"])
        if before != (r.get("r5_maximal"), r.get("maximal"), r.get("maximal_detail")):
            changed += 1
    return changed


def recheck_track(track: str, filename: str, index: dict[str, str]) -> int:
    path = LOGS_DIR / filename
    if not path.exists():
        print(f"[{track}] {filename} not found — skipping")
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))

    def score(r: dict) -> MaximalResult:
        dest = RECHECK_DIR / track.lower() / r["repo"].replace("/", "__")
        try:
            materialize(track, r["repo"], r["base_commit"], dest)
            mr = check_maximal_effective(dest, index)
        finally:
            if dest.exists():
                _rmtree(dest)
        print(f"  {r['repo']}: r5={mr.passed}  ({mr.detail})")
        return mr

    print(f"[{track}] rescoring {sum(1 for r in records if r.get('minimal'))} minimal passes")
    changed = rescore_records(records, score)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    n = len(records)
    minimal_k = sum(1 for r in records if r.get("minimal"))
    maximal_k = sum(1 for r in records if r.get("maximal"))
    print(
        f"[{track}] {changed} records changed  n={n}  "
        f"minimal={minimal_k}/{n}={100 * minimal_k / n:.1f}%  "
        f"maximal={maximal_k}/{n}={100 * maximal_k / n:.1f}%"
    )
    return changed


def main(tracks: list[str] | None = None) -> None:
    index = load_version_index()
    if not index:
        print("ERROR: reference version list is missing.")
        return
    print(f"Loaded reference versions: {len(index)} entries")
    for track in tracks or list(_RESULT_FILES):
        recheck_track(track, _RESULT_FILES[track], index)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-score maximal (r5) for finished tracks.")
    parser.add_argument("--tracks", nargs="+", default=None, choices=list(_RESULT_FILES),
                        help="Which tracks to recheck (default: all).")
    args = parser.parse_args()
    main(tracks=args.tracks)
