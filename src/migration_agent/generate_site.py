"""S27: GitHub Pages results page generator.

Reads results/final_report.json and generates docs/index.html —
a self-contained static page with the results table, CI badges,
and links to the paper.

CLI:
    uv run python -m migration_agent.generate_site
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "results"
DOCS_DIR = REPO_ROOT / "docs"

_REPO_URL = "https://github.com/syedk04/java-migration-bench"
_PAPER_URL = "https://arxiv.org/abs/2505.09569"

_TRACK_COLORS = {
    "T0": "#6c757d",
    "T1": "#0d6efd",
    "T2": "#198754",
    "T3": "#fd7e14",
    "T4": "#6610f2",
    "T3-lite": "#dc3545",
    "T4-lite": "#20c997",
}


def _pct(v: float | None) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _ci(lo: float, hi: float) -> str:
    return f"[{lo:.1f}, {hi:.1f}]"


def _badge_color(pct: float | None) -> str:
    if pct is None:
        return "#6c757d"
    if pct >= 50:
        return "#198754"
    if pct >= 20:
        return "#fd7e14"
    return "#dc3545"


def generate_html(report: dict) -> str:
    tracks = report.get("tracks", [])
    generated_at = report.get("generated_at", "unknown")

    # Build rows.
    rows = ""
    for s in tracks:
        color = _TRACK_COLORS.get(s["track"], "#333")
        min_pct = s.get("minimal_pct")
        max_pct = s.get("maximal_pct")
        ci_min = s.get("minimal_ci95", [0, 100])
        ci_max = s.get("maximal_ci95", [0, 100])
        avg = f"{s['avg_calls']:.1f}" if s.get("avg_calls") else "—"
        # Flag when all maximal passes are vacuous (BOM-managed deps, 0 checked)
        vacuous_k = s.get("maximal_vacuous_k", 0)
        maximal_k = s.get("maximal_k", 0)
        vacuous_flag = "†" if vacuous_k > 0 and vacuous_k == maximal_k else ""

        rows += f"""
        <tr>
          <td><span class="badge" style="background:{color}">{s['track']}</span>
              {s.get('label','').replace(s['track']+': ','')}</td>
          <td class="num">{s['n']}</td>
          <td class="num" style="color:{_badge_color(min_pct)};font-weight:600">
              {_pct(min_pct)}</td>
          <td class="num small">{_ci(*ci_min)}</td>
          <td class="num" style="color:{_badge_color(max_pct)};font-weight:600">
              {_pct(max_pct)}{vacuous_flag}</td>
          <td class="num small">{_ci(*ci_max)}</td>
          <td class="num">{avg}</td>
        </tr>"""

    paper_rows = """
        <tr><td>OpenRewrite (paper, n=300)</td>
            <td class="num">300</td><td class="num">16.3%</td><td></td>
            <td class="num">2.0%</td><td></td><td class="num">—</td></tr>
        <tr><td>Strands baseline (paper, n=300)</td>
            <td class="num">300</td><td class="num">71.7%</td><td></td>
            <td class="num">15.3%</td><td></td><td class="num">33.68</td></tr>
        <tr><td>+ prompt engineering (paper, n=300)</td>
            <td class="num">300</td><td class="num">—</td><td></td>
            <td class="num">45.7%</td><td></td><td class="num">49.22</td></tr>
        <tr><td>+ PE + RAG (paper, n=300)</td>
            <td class="num">300</td><td class="num">—</td><td></td>
            <td class="num">53.3%</td><td></td><td class="num">59.22</td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>java-migration-bench results</title>
  <style>
    :root {{
      --bg: #0d1117; --surface: #161b22; --border: #30363d;
      --text: #e6edf3; --muted: #8b949e; --link: #58a6ff;
    }}
    body {{ background: var(--bg); color: var(--text); font-family: -apple-system,
            BlinkMacSystemFont,'Segoe UI',sans-serif; margin: 0; padding: 2rem; }}
    h1 {{ font-size: 1.8rem; margin-bottom: .25rem; }}
    .subtitle {{ color: var(--muted); margin-bottom: 2rem; font-size: .95rem; }}
    a {{ color: var(--link); }}
    .card {{ background: var(--surface); border: 1px solid var(--border);
             border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; }}
    h2 {{ font-size: 1.1rem; margin: 0 0 1rem; color: var(--muted);
          text-transform: uppercase; letter-spacing: .05em; font-size: .8rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .9rem; }}
    th {{ text-align: left; padding: .5rem .75rem; color: var(--muted);
          border-bottom: 1px solid var(--border); font-weight: 500; }}
    td {{ padding: .5rem .75rem; border-bottom: 1px solid var(--border); }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .small {{ font-size: .8rem; color: var(--muted); }}
    .badge {{ display: inline-block; padding: .2em .6em; border-radius: 4px;
              font-size: .75rem; font-weight: 700; color: #fff;
              margin-right: .4rem; }}
    .sep {{ border-top: 2px solid var(--border); }}
    .note {{ color: var(--muted); font-size: .8rem; margin-top: 1rem; }}
    .meta {{ color: var(--muted); font-size: .8rem; }}
    .constraints {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
    .pill {{ background: var(--surface); border: 1px solid var(--border);
             border-radius: 20px; padding: .3rem .8rem; font-size: .8rem; color: var(--muted); }}
  </style>
</head>
<body>

<h1>java-migration-bench</h1>
<p class="subtitle">
  Reproducing the <a href="{_PAPER_URL}">MigrationBench</a> Java 8→17 ladder
  on free-tier infrastructure — zero paid compute, defensible numbers.
  <a href="{_REPO_URL}">Code on GitHub</a>
</p>

<div class="constraints">
  <span class="pill">n = 50 repos</span>
  <span class="pill">Gemini 2.5 Flash (free tier)</span>
  <span class="pill">40 LLM calls / repo</span>
  <span class="pill">14 RPM / 1400 RPD quota</span>
  <span class="pill">zero paid infrastructure</span>
</div>

<div class="card">
  <h2>Our results</h2>
  <table>
    <thead>
      <tr>
        <th>Track</th><th class="num">n</th>
        <th class="num">Minimal</th><th class="num">95% CI</th>
        <th class="num">Maximal</th><th class="num">95% CI</th>
        <th class="num">Avg calls</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
  <p class="note">
    <strong>Minimal</strong>: build green under Java 17, bytecode v61,
    all original tests present and passing.<br>
    <strong>Maximal</strong>: minimal + every dependency at latest major version
    (Maven Central snapshot 2026-09).<br>
    <em>Caveat (†):</em> r5 only checks deps with explicit &lt;version&gt; in pom.xml.
    BOM/parent-managed versions pass vacuously (0 deps checked). A † means all
    maximal passes for this track were vacuous — no explicit dep versions found.
    Maximal ≈ minimal until full POM resolution is implemented.
  </p>
</div>

<div class="card">
  <h2>Paper reference (n=300, 80-call budget)</h2>
  <table>
    <thead>
      <tr>
        <th>Method</th><th class="num">n</th>
        <th class="num">Minimal</th><th></th>
        <th class="num">Maximal</th><th></th>
        <th class="num">Avg calls</th>
      </tr>
    </thead>
    <tbody>
      {paper_rows}
    </tbody>
  </table>
  <p class="note">
    At n=50, Wilson 95% CI ≈ ±13 pp around 50%. Enough to distinguish 2% from 45%.
    Not enough to distinguish 45% from 53%.
    All our numbers use n=50; paper numbers use n=300.
  </p>
</div>

<p class="meta">
  Generated {generated_at} ·
  <a href="{_REPO_URL}">syedk04/java-migration-bench</a> ·
  Paper: <a href="{_PAPER_URL}">arxiv 2505.09569</a>
</p>

</body>
</html>"""


def main() -> None:
    report_path = RESULTS_DIR / "final_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        # Fallback: build a minimal report from available summaries.
        print("final_report.json not found — building from individual summaries")
        from migration_agent.final_report import main as build_report
        build_report()
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.exists()
            else {"tracks": []}
        )

    DOCS_DIR.mkdir(exist_ok=True)
    html = generate_html(report)
    out = DOCS_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"Written {out.relative_to(REPO_ROOT)}")

    # Jekyll bypass file (GitHub Pages needs this to serve files in docs/).
    nojekyll = DOCS_DIR / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("")
        print(f"Created {nojekyll.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
