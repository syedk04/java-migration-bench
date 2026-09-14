"""Tests for S27: GitHub Pages site generator."""

from migration_agent.generate_site import _badge_color, _ci, _pct, generate_html

_SAMPLE_REPORT = {
    "generated_at": "2026-09-14T00:00:00Z",
    "tracks": [
        {
            "track": "T0",
            "label": "T0: compiler bump only",
            "n": 50,
            "minimal_k": 9,
            "maximal_k": 9,
            "maximal_vacuous_k": 9,
            "minimal_pct": 18.0,
            "maximal_pct": 18.0,
            "minimal_ci95": [9.8, 30.8],
            "maximal_ci95": [9.8, 30.8],
            "avg_calls": None,
            "audit": None,
        },
        {
            "track": "T3",
            "label": "T3: + engineered prompt (playbook)",
            "n": 50,
            "minimal_k": 30,
            "maximal_k": 15,
            "maximal_vacuous_k": 0,  # some deps actually checked
            "minimal_pct": 60.0,
            "maximal_pct": 30.0,
            "minimal_ci95": [46.5, 72.3],
            "maximal_ci95": [19.0, 43.8],
            "avg_calls": 28.5,
            "audit": None,
        },
    ],
}


def test_generate_html_contains_track_badge():
    html = generate_html(_SAMPLE_REPORT)
    assert 'T0' in html
    assert 'T3' in html
    assert 'compiler bump only' in html


def test_vacuous_flag_shown_when_all_maximal_vacuous():
    html = generate_html(_SAMPLE_REPORT)
    # T0: maximal_vacuous_k == maximal_k == 9 → should have † symbol
    assert '18.0%†' in html or '18.0%\u2020' in html or '\u2020' in html


def test_vacuous_flag_absent_when_not_all_vacuous():
    html = generate_html(_SAMPLE_REPORT)
    # T3 has maximal_vacuous_k=0 — no † expected for its 30.0% entry
    # Check that T3 row does not have the dagger next to 30.0%
    # (The dagger is only for T0's 18.0%)
    t3_section = html[html.index('T3'):html.index('T3') + 500]
    assert '30.0%†' not in t3_section
    assert '30.0%\u2020' not in t3_section


def test_paper_reference_rows_present():
    html = generate_html(_SAMPLE_REPORT)
    assert 'OpenRewrite (paper, n=300)' in html
    assert 'Strands baseline' in html
    assert '16.3%' in html
    assert '71.7%' in html


def test_generated_at_in_html():
    html = generate_html(_SAMPLE_REPORT)
    assert '2026-09-14T00:00:00Z' in html


def test_avg_calls_shown():
    html = generate_html(_SAMPLE_REPORT)
    assert '28.5' in html  # T3 avg_calls


def test_avg_calls_dash_when_none():
    html = generate_html(_SAMPLE_REPORT)
    # T0 has no avg_calls — should show —
    assert '—' in html


def test_pct_helper():
    assert _pct(18.0) == '18.0%'
    assert _pct(None) == '—'


def test_ci_helper():
    assert _ci(9.8, 30.8) == '[9.8, 30.8]'


def test_badge_color():
    assert _badge_color(60.0) == '#198754'  # green ≥50
    assert _badge_color(30.0) == '#fd7e14'  # orange 20-49
    assert _badge_color(10.0) == '#dc3545'  # red <20
    assert _badge_color(None) == '#6c757d'  # grey
