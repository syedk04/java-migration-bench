"""Tests for S23: final report generator."""

import json
import math
from pathlib import Path

from migration_agent.final_report import (
    _ci,
    _pct,
    build_full_table,
    summarise_track,
    wilson_ci,
)


def test_wilson_ci_basic():
    lo, hi = wilson_ci(8, 50)
    assert lo < 16 < hi  # 16% should be inside CI


def test_pct_none():
    assert _pct(None) == "—"


def test_pct_value():
    assert _pct(18.0) == "18.0%"


def test_ci_format():
    assert _ci(9.8, 30.8) == "[9.8, 30.8]"


def _make_results_file(tmp_path: Path, track: str, n: int,
                       n_minimal: int, n_maximal: int) -> Path:
    records = [
        {
            "repo": f"owner/repo{i}", "base_commit": "abc", "track": track,
            "minimal": i < n_minimal, "maximal": i < n_maximal,
            "calls_used": 15,
        }
        for i in range(n)
    ]
    path = tmp_path / f"{track.lower()}_reporting_50.json"
    path.write_text(json.dumps(records) + "\n")
    return path


def test_summarise_track(tmp_path, monkeypatch):
    monkeypatch.setattr("migration_agent.final_report.LOGS_DIR", tmp_path)
    _make_results_file(tmp_path, "T0", 50, 9, 0)
    s = summarise_track("T0", "T0: compiler bump only", "t0_reporting_50.json")
    assert s is not None
    assert s["n"] == 50
    assert s["minimal_k"] == 9
    assert math.isclose(s["minimal_pct"], 18.0)


def test_summarise_track_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("migration_agent.final_report.LOGS_DIR", tmp_path)
    assert summarise_track("T0", "label", "missing.json") is None


def test_build_full_table_contains_tracks():
    s0 = {
        "track": "T0", "label": "T0: compiler bump", "n": 50,
        "minimal_k": 9, "maximal_k": 0,
        "minimal_pct": 18.0, "maximal_pct": 0.0,
        "minimal_ci95": [9.8, 30.8], "maximal_ci95": [0.0, 7.1],
        "avg_calls": None, "audit": None,
    }
    s1 = {
        "track": "T2", "label": "T2: naive", "n": 50,
        "minimal_k": 25, "maximal_k": 5,
        "minimal_pct": 50.0, "maximal_pct": 10.0,
        "minimal_ci95": [36.3, 63.7], "maximal_ci95": [3.3, 21.8],
        "avg_calls": 22.5, "audit": None,
    }
    table = build_full_table([s0, s1])
    assert "compiler bump" in table
    assert "naive" in table
    assert "18.0%" in table
    assert "50.0%" in table
    # Paper reference rows.
    assert "OpenRewrite" in table
    assert "Strands" in table


def test_build_full_table_shows_audit_when_present():
    s = {
        "track": "T2", "label": "T2: naive", "n": 50,
        "minimal_k": 10, "maximal_k": 3,
        "minimal_pct": 20.0, "maximal_pct": 6.0,
        "minimal_ci95": [10.8, 33.5], "maximal_ci95": [1.3, 16.5],
        "avg_calls": 18.0,
        "audit": {"GENUINE": 6, "RETRIEVED": 2, "GAMED": 2},
    }
    table = build_full_table([s])
    assert "GENUINE" in table.upper() or "genuine" in table.lower()


def test_vacuous_count_uses_marker_not_substring(tmp_path, monkeypatch):
    """'10 deps checked' contains '0 deps checked' but is not vacuous."""
    monkeypatch.setattr("migration_agent.final_report.LOGS_DIR", tmp_path)
    records = [
        {"repo": "a/x", "minimal": True, "maximal": True,
         "maximal_detail": "effective: 10 deps checked, 0 outdated, 2 skipped"},
        {"repo": "a/y", "minimal": True, "maximal": True,
         "maximal_detail": "effective: 0 deps checked, 0 outdated, 1 skipped "
                           "[vacuous — no reference deps resolved]"},
    ]
    (tmp_path / "t.json").write_text(json.dumps(records), encoding="utf-8")
    s = summarise_track("T0", "T0: test", "t.json")
    assert s is not None
    assert s["maximal_k"] == 2
    assert s["maximal_vacuous_k"] == 1
