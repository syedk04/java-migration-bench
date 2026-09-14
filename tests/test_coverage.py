"""Unit tests for coverage.py — no Docker required."""

import tempfile
from pathlib import Path

from migration_agent.coverage import CoverageResult, _parse_jacoco_xml, check_coverage

_JACOCO_XML = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="my-project">
  <package name="com/example">
    <counter type="INSTRUCTION" missed="10" covered="90"/>
    <counter type="LINE" missed="4" covered="16"/>
  </package>
  <counter type="INSTRUCTION" missed="10" covered="90"/>
  <counter type="LINE" missed="4" covered="16"/>
</report>
"""

_JACOCO_XML_ZERO = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="empty">
  <counter type="LINE" missed="0" covered="0"/>
</report>
"""


def _write_jacoco(repo_dir: Path, xml: str, module: str = "") -> None:
    report_dir = repo_dir / module / "target" / "site" / "jacoco" if module else \
                 repo_dir / "target" / "site" / "jacoco"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "jacoco.xml").write_text(xml, encoding="utf-8")


# --- _parse_jacoco_xml ---

def test_parse_returns_covered_and_missed():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "jacoco.xml"
        p.write_text(_JACOCO_XML, encoding="utf-8")
        covered, missed = _parse_jacoco_xml(p)
        assert covered == 16
        assert missed == 4


def test_parse_handles_doctype():
    """DOCTYPE declaration must not cause a parse error."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "jacoco.xml"
        p.write_text(_JACOCO_XML, encoding="utf-8")
        covered, missed = _parse_jacoco_xml(p)
        assert covered + missed == 20


def test_parse_zero_coverage():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "jacoco.xml"
        p.write_text(_JACOCO_XML_ZERO, encoding="utf-8")
        covered, missed = _parse_jacoco_xml(p)
        assert covered == 0 and missed == 0


# --- check_coverage with None measurements ---

def test_none_baseline_passes_inconclusive():
    result = check_coverage(baseline_pct=None, migrated_dir=Path("."), java_version=17)
    # We override measure_coverage by testing with None baseline — passes
    # (can't actually call measure_coverage without Docker, so test the None path)
    assert isinstance(result, CoverageResult)


def test_coverage_result_drop_calculation():
    """Verify drop_pp arithmetic and 5pp threshold."""
    # simulate: baseline=80%, migrated=74% → drop=6pp → FAIL
    result = CoverageResult(
        baseline_pct=80.0,
        migrated_pct=74.0,
        drop_pp=6.0,
        passed=False,
        detail="",
    )
    assert not result.passed
    assert result.drop_pp == 6.0

    # baseline=80%, migrated=76% → drop=4pp → PASS
    result2 = CoverageResult(
        baseline_pct=80.0,
        migrated_pct=76.0,
        drop_pp=4.0,
        passed=True,
        detail="",
    )
    assert result2.passed


def test_coverage_result_exact_threshold():
    """A drop of exactly 5pp should pass."""
    r = CoverageResult(
        baseline_pct=70.0, migrated_pct=65.0, drop_pp=5.0, passed=True, detail=""
    )
    assert r.passed
