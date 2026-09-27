"""Unit tests for recheck_maximal.rescore_records — no Docker or network."""

from migration_agent.maximal import MaximalResult
from migration_agent.recheck_maximal import rescore_records


def _mr(passed: bool) -> MaximalResult:
    return MaximalResult(passed=passed, outdated=[] if passed else ["a:b"],
                         skipped=[], checked=1, detail=f"effective: passed={passed}")


def test_only_minimal_passes_are_scored():
    seen = []
    records = [
        {"repo": "a/x", "minimal": True, "r5_maximal": True, "maximal": True},
        {"repo": "a/y", "minimal": False, "r5_maximal": True, "maximal": False},
    ]
    rescore_records(records, lambda r: seen.append(r["repo"]) or _mr(False))
    assert seen == ["a/x"]
    assert records[0]["maximal"] is False
    assert records[0]["maximal_outdated"] == ["a:b"]
    assert records[1]["maximal"] is False


def test_vacuous_pass_flipped_to_fail_counts_as_change():
    records = [{"repo": "a/x", "minimal": True, "r5_maximal": True, "maximal": True,
                "maximal_detail": "0 deps checked [vacuous]"}]
    assert rescore_records(records, lambda r: _mr(False)) == 1


def test_scoring_error_fails_r5_with_reason():
    def boom(r):
        raise RuntimeError("OpenRewrite exit 1")

    records = [{"repo": "a/x", "minimal": True, "r5_maximal": True, "maximal": True}]
    rescore_records(records, boom)
    assert records[0]["r5_maximal"] is False
    assert records[0]["maximal"] is False
    assert "OpenRewrite exit 1" in records[0]["maximal_detail"]


def test_unchanged_record_not_counted():
    records = [{"repo": "a/x", "minimal": True, "r5_maximal": True, "maximal": True,
                "maximal_detail": "effective: passed=True"}]
    assert rescore_records(records, lambda r: _mr(True)) == 0
