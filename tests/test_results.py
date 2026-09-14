"""Tests for S12: results store and table generator."""

import json
import math

import pytest

from migration_agent.results import build_table, summarise, wilson_ci


# ---------------------------------------------------------------------------
# Wilson CI
# ---------------------------------------------------------------------------

def test_wilson_ci_zero_n():
    lo, hi = wilson_ci(0, 0)
    assert lo == 0.0 and hi == 100.0


def test_wilson_ci_all_pass():
    lo, hi = wilson_ci(50, 50)
    # With all successes the CI should be entirely above 90%.
    assert lo > 90.0 and hi == 100.0


def test_wilson_ci_none_pass():
    lo, hi = wilson_ci(0, 50)
    assert lo == 0.0 and hi < 10.0


def test_wilson_ci_half():
    lo, hi = wilson_ci(25, 50)
    # Rough sanity: should be a wide interval centred around 50%.
    assert lo < 40.0 and hi > 60.0


def test_wilson_ci_symmetric_approx():
    # For 8/50 ≈ 16%, the CI should span roughly ±10 pp.
    lo, hi = wilson_ci(8, 50)
    centre = (lo + hi) / 2
    assert abs(centre - 16) < 5  # centre near 16%
    assert (hi - lo) > 10  # wide enough


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------

def _make_records(n: int, n_minimal: int, n_maximal: int) -> list[dict]:
    records = []
    for i in range(n):
        records.append({
            "repo": f"owner/repo{i}",
            "base_commit": "abc",
            "track": "T0",
            "minimal": i < n_minimal,
            "maximal": i < n_maximal,
        })
    return records


def test_summarise_counts():
    records = _make_records(50, 9, 2)
    s = summarise(records, "T0")
    assert s["n"] == 50
    assert s["minimal_count"] == 9
    assert s["maximal_count"] == 2
    assert math.isclose(s["minimal_pct"], 18.0)
    assert math.isclose(s["maximal_pct"], 4.0)


def test_summarise_ci_present():
    records = _make_records(50, 9, 2)
    s = summarise(records, "T0")
    lo, hi = s["minimal_ci95"]
    assert lo < 18.0 < hi  # 18% should be inside its own CI


def test_summarise_empty():
    s = summarise([], "T0")
    assert s["n"] == 0
    assert s["minimal_pct"] is None


# ---------------------------------------------------------------------------
# build_table
# ---------------------------------------------------------------------------

def test_build_table_contains_track_label():
    records = _make_records(50, 9, 2)
    s = summarise(records, "T0")
    table = build_table([s])
    assert "compiler bump" in table.lower() or "T0" in table


def test_build_table_contains_paper_row():
    records = _make_records(50, 9, 2)
    s = summarise(records, "T0")
    table = build_table([s])
    assert "16.3" in table  # OpenRewrite paper reference


def test_build_table_multiple_tracks():
    t0 = summarise(_make_records(50, 9, 2), "T0")
    t1 = summarise(_make_records(50, 8, 1), "T1")
    table = build_table([t0, t1])
    assert "T0" in table or "compiler" in table.lower()
    assert "OpenRewrite" in table
