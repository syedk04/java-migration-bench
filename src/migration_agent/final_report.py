"""S23: Final results tables with Wilson 95% CIs, audited numbers, and cost.

Reads all committed results JSONs, computes Wilson CIs, and generates:
  1. Full markdown table for README.md
  2. results/final_report.json (all numbers, machine-readable)
  3. Console summary

CLI:
    uv run python -m migration_agent.final_report
    uv run python -m migration_agent.final_report --dry-run
"""

import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

# Windows consoles default to cp1252 which can't handle em-dashes or arrows.
# Reconfigure stdout to UTF-8 so print() works everywhere.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
RESULTS_DIR = REPO_ROOT / "results"

# Order matters — displayed top to bottom.
_TRACKS = [
    ("T0", "t0_reporting_50.json",  "T0: compiler bump only"),
    ("T1", "t1_reporting_50.json",  "T1: OpenRewrite UpgradeToJava17"),
    ("T2", "t2_reporting_50.json",  "T2: Gemini naive prompt"),
    ("T3", "t3_reporting_50.json",  "T3: + engineered prompt (playbook)"),
    ("T4", "t4_reporting_50.json",  "T4: + version index retrieval"),
]

_PAPER = [
    ("OpenRewrite (paper, n=300)",          16.33, 2.00,  None),
    ("Strands baseline (paper, n=300)",     71.67, 15.33, 33.68),
    ("+ prompt engineering (paper, n=300)", None,  45.67, 49.22),
    ("+ PE + RAG (paper, n=300)",           None,  53.33, 59.22),
    ("hybrid static+agent (paper, n=300)",  None,  53.33, 52.55),
]


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 100.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (centre - half) * 100), min(100.0, (centre + half) * 100))


def _pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _ci(lo: float, hi: float) -> str:
    return f"[{lo:.1f}, {hi:.1f}]"


# ---------------------------------------------------------------------------
# Load + summarise track
# ---------------------------------------------------------------------------

def _load(filename: str) -> list[dict] | None:
    path = LOGS_DIR / filename
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _audit_counts(track: str) -> dict[str, int] | None:
    audit_path = LOGS_DIR / f"{track.lower()}_audit.json"
    if not audit_path.exists():
        return None
    entries = json.loads(audit_path.read_text())
    counts: dict[str, int] = {}
    for e in entries:
        c = e.get("classification", "UNKNOWN")
        counts[c] = counts.get(c, 0) + 1
    return counts


def summarise_track(track: str, label: str, filename: str) -> dict | None:
    records = _load(filename)
    if not records:
        return None
    n = len(records)
    minimal_k = sum(1 for r in records if r.get("minimal"))
    maximal_k = sum(1 for r in records if r.get("maximal"))
    # Vacuous maximal pass: maximal=True but 0 deps were actually checked
    # (happens when all deps are BOM/parent-managed with no explicit <version>)
    maximal_vacuous_k = sum(
        1 for r in records
        if r.get("maximal") and "0 deps checked" in r.get("maximal_detail", "")
    )
    calls = [r.get("calls_used", 0) for r in records if r.get("calls_used")]
    avg_calls = sum(calls) / len(calls) if calls else None
    min_lo, min_hi = wilson_ci(minimal_k, n)
    max_lo, max_hi = wilson_ci(maximal_k, n)
    audit = _audit_counts(track)
    return {
        "track": track,
        "label": label,
        "n": n,
        "minimal_k": minimal_k,
        "maximal_k": maximal_k,
        "maximal_vacuous_k": maximal_vacuous_k,
        "minimal_pct": round(100 * minimal_k / n, 2) if n else None,
        "maximal_pct": round(100 * maximal_k / n, 2) if n else None,
        "minimal_ci95": [round(min_lo, 1), round(min_hi, 1)],
        "maximal_ci95": [round(max_lo, 1), round(max_hi, 1)],
        "avg_calls": round(avg_calls, 1) if avg_calls else None,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------

def build_full_table(summaries: list[dict]) -> str:
    lines: list[str] = []

    # Our results.
    lines.append("### Our results (n=50, Gemini 2.5 Flash, 40-call budget)")
    lines.append("")
    lines.append("| track | n | minimal | 95% CI | maximal | 95% CI | avg calls |")
    lines.append("|---|---|---|---|---|---|---|")
    for s in summaries:
        ac = f"{s['avg_calls']:.1f}" if s["avg_calls"] else "—"
        lines.append(
            f"| {s['label']} | {s['n']} "
            f"| {_pct(s.get('minimal_pct'))} "
            f"| {_ci(*s['minimal_ci95'])} "
            f"| {_pct(s.get('maximal_pct'))} "
            f"| {_ci(*s['maximal_ci95'])} "
            f"| {ac} |"
        )

    # Audited maximal (if available).
    audited = [(s, s["audit"]) for s in summaries if s.get("audit")]
    if audited:
        lines.append("")
        lines.append("Audited maximal (GENUINE + RETRIEVED, excludes GAMED):")
        lines.append("")
        lines.append("| track | genuine | retrieved | gamed | audited maximal |")
        lines.append("|---|---|---|---|---|")
        for s, audit in audited:
            g = audit.get("GENUINE", 0)
            r = audit.get("RETRIEVED", 0)
            gm = audit.get("GAMED", 0)
            total = g + r + gm
            audited_max_k = g + r
            audited_pct = 100 * audited_max_k / s["n"] if s["n"] else 0
            lines.append(
                f"| {s['label']} | {g} | {r} | {gm} "
                f"| {audited_pct:.1f}% (n={total} audited) |"
            )

    lines.append("")
    lines.append("### Paper reference (n=300, Claude 4.5 Sonnet, 80-call budget)")
    lines.append("")
    lines.append("| method | minimal | maximal | avg calls |")
    lines.append("|---|---|---|---|")
    for label, minimal, maximal, avg_calls in _PAPER:
        ac = f"{avg_calls:.2f}" if avg_calls else "—"
        lines.append(
            f"| {label} | {_pct(minimal)} | {_pct(maximal)} | {ac} |"
        )

    lines.append("")
    lines.append(
        "> **Note:** n=50 → Wilson 95% CI ≈ ±13 pp around 50%. "
        "Enough to distinguish 2% from 45%. Not enough to distinguish 45% from 53%."
    )
    lines.append(
        "> **Maximal check caveat:** r5 only inspects dependencies with an explicit "
        "`<version>` element in pom.xml. Dependencies managed through a parent POM or "
        "BOM import (common in Spring/Spring Boot projects) are not checked and pass "
        "vacuously. Repos with 0 explicit dep versions show '0 deps checked' in "
        "`maximal_detail`; their maximal=True is a vacuous pass, not a verified result."
    )

    return "\n".join(lines)


def _update_readme(table: str) -> None:
    readme = REPO_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start_tag = "<!-- RESULTS_TABLE_START -->"
    end_tag = "<!-- RESULTS_TABLE_END -->"
    block = f"{start_tag}\n{table}\n{end_tag}"
    if start_tag in text and end_tag in text:
        before = text[: text.index(start_tag)]
        after = text[text.index(end_tag) + len(end_tag) :]
        text = before + block + after
    else:
        text = text + f"\n\n## Results\n\n{block}\n"
    readme.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(dry_run: bool = False) -> None:
    summaries = []
    for track, filename, label in _TRACKS:
        s = summarise_track(track, label, filename)
        if s is None:
            print(f"[{track}] {filename} not found — skipping")
            continue
        summaries.append(s)
        min_pct = s.get("minimal_pct")
        max_pct = s.get("maximal_pct")
        ci_min = s["minimal_ci95"]
        ci_max = s["maximal_ci95"]
        print(
            f"[{track}] n={s['n']} "
            f"minimal={s['minimal_k']}/{s['n']}={min_pct}% CI{ci_min}  "
            f"maximal={s['maximal_k']}/{s['n']}={max_pct}% CI{ci_max}"
            + (f"  avg_calls={s['avg_calls']}" if s["avg_calls"] else "")
        )

    if not summaries:
        print("No result files found yet.")
        return

    table = build_full_table(summaries)
    print("\n--- Full results table ---")
    print(table)
    print("--- End table ---\n")

    if dry_run:
        print("Dry run — no files written.")
        return

    RESULTS_DIR.mkdir(exist_ok=True)
    report = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tracks": summaries,
    }
    out = RESULTS_DIR / "final_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Written {out.relative_to(REPO_ROOT)}")

    _update_readme(table)
    print("README.md updated.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="S23: final results tables.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
