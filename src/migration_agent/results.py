"""S12: Results store and README table generator.

Reads raw per-repo JSON logs from workdir/_logs/, computes summary
statistics (including Wilson 95% CI), writes committed JSON to results/,
and regenerates the Results section of README.md in place.

CLI:
    uv run python -m migration_agent.results
    uv run python -m migration_agent.results --dry-run   # print table, no writes
"""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
RESULTS_DIR = REPO_ROOT / "results"

# Tracks in display order.
_TRACKS = [
    ("T0", "t0_reporting_50.json"),
    ("T1", "t1_reporting_50.json"),
]

# Paper reference numbers (n=300, Claude 4.5 Sonnet, 80-call cutoff).
_PAPER: dict[str, dict[str, float | None]] = {
    "OpenRewrite (paper n=300)": {"minimal": 16.33, "maximal": 2.00},
    "Strands baseline (paper n=300)": {"minimal": 71.67, "maximal": 15.33},
    "+ prompt engineering (paper n=300)": {"minimal": None, "maximal": 45.67},
    "+ PE + RAG (paper n=300)": {"minimal": None, "maximal": 53.33},
}

_TRACK_LABEL = {
    "T0": "T0: compiler bump only",
    "T1": "T1: OpenRewrite UpgradeToJava17",
}


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% confidence interval, returns (lo, hi) in percent."""
    if n == 0:
        return (0.0, 100.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) * 100), min(100.0, (centre + half) * 100))


def load_track(filename: str) -> list[dict] | None:
    path = LOGS_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def summarise(records: list[dict], track: str) -> dict:
    n = len(records)
    minimal = sum(1 for r in records if r.get("minimal"))
    maximal = sum(1 for r in records if r.get("maximal"))
    min_lo, min_hi = wilson_ci(minimal, n)
    max_lo, max_hi = wilson_ci(maximal, n)
    return {
        "track": track,
        "n": n,
        "minimal_count": minimal,
        "maximal_count": maximal,
        "minimal_pct": round(100 * minimal / n, 2) if n else None,
        "maximal_pct": round(100 * maximal / n, 2) if n else None,
        "minimal_ci95": [round(min_lo, 1), round(min_hi, 1)],
        "maximal_ci95": [round(max_lo, 1), round(max_hi, 1)],
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _pct_str(pct: float | None) -> str:
    return f"{pct:.1f}%" if pct is not None else "—"


def _ci_str(ci: list[float]) -> str:
    return f"[{ci[0]:.1f}, {ci[1]:.1f}]"


def build_table(summaries: list[dict]) -> str:
    rows = []
    rows.append("| track | n | minimal | 95% CI | maximal | 95% CI |")
    rows.append("|---|---|---|---|---|---|")

    for s in summaries:
        label = _TRACK_LABEL.get(s["track"], s["track"])
        rows.append(
            f"| {label} | {s['n']} "
            f"| {_pct_str(s.get('minimal_pct'))} "
            f"| {_ci_str(s['minimal_ci95'])} "
            f"| {_pct_str(s.get('maximal_pct'))} "
            f"| {_ci_str(s['maximal_ci95'])} |"
        )

    rows.append("")
    rows.append("Paper reference (n=300, Claude 4.5 Sonnet, 80-call budget):")
    rows.append("")
    rows.append("| method | minimal | maximal |")
    rows.append("|---|---|---|")
    for label, v in _PAPER.items():
        rows.append(
            f"| {label} "
            f"| {_pct_str(v['minimal'])} "
            f"| {_pct_str(v['maximal'])} |"
        )

    return "\n".join(rows)


def update_readme(table: str) -> None:
    readme = REPO_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")

    # Replace everything between the sentinel comments (or the old placeholder).
    start_tag = "<!-- RESULTS_TABLE_START -->"
    end_tag = "<!-- RESULTS_TABLE_END -->"
    block = f"{start_tag}\n{table}\n{end_tag}"

    if start_tag in text and end_tag in text:
        before = text[: text.index(start_tag)]
        after = text[text.index(end_tag) + len(end_tag) :]
        text = before + block + after
    else:
        # Replace the old static placeholder section.
        old = (
            "## Results\n\nNothing yet. First numbers land after the "
            "OpenRewrite calibration run\n(zero LLM calls) - see the build log."
        )
        if old in text:
            text = text.replace(old, f"## Results\n\n{block}")
        else:
            # Fallback: append before "## Build log"
            text = text.replace("## Build log", f"## Results\n\n{block}\n\n## Build log")

    readme.write_text(text, encoding="utf-8")


def main(dry_run: bool = False) -> None:
    summaries = []
    for track, filename in _TRACKS:
        records = load_track(filename)
        if records is None:
            print(f"[{track}] {filename} not found — skipping")
            continue
        s = summarise(records, track)
        summaries.append(s)
        print(
            f"[{track}] n={s['n']} "
            f"minimal={s['minimal_count']}/{s['n']}={s['minimal_pct']}% "
            f"CI{s['minimal_ci95']}  "
            f"maximal={s['maximal_count']}/{s['n']}={s['maximal_pct']}% "
            f"CI{s['maximal_ci95']}"
        )

    if not summaries:
        print("No result files found. Run T0/T1 batches first.")
        return

    table = build_table(summaries)
    print("\n--- Generated table ---")
    print(table)
    print("--- End table ---\n")

    if dry_run:
        print("Dry run — no files written.")
        return

    RESULTS_DIR.mkdir(exist_ok=True)
    for s in summaries:
        out = RESULTS_DIR / f"{s['track'].lower()}_summary.json"
        out.write_text(json.dumps(s, indent=2) + "\n")
        print(f"Written {out.relative_to(REPO_ROOT)}")

    update_readme(table)
    print("README.md updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="S12: results store + README table.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print table without writing any files.")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
