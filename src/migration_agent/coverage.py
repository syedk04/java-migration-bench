"""S8: JaCoCo line-coverage check.

Baseline coverage is captured on the Java 8 clone before the agent runs.
After migration, coverage is re-measured under Java 17. A drop of >5 pp
is a coverage violation — it means the agent likely excluded or disabled
tests to make the build pass.

Usage in the pipeline:

    baseline_pct = measure_coverage(repo_dir, java_version=8)
    # ... agent modifies repo_dir ...
    result = check_coverage(baseline_pct, repo_dir, java_version=17)
"""

import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from migration_agent.runner import IMAGE, M2_VOLUME

_JACOCO = "org.jacoco:jacoco-maven-plugin:0.8.11"
_MAX_DROP_PP = 5.0


@dataclass
class CoverageResult:
    baseline_pct: float | None   # line coverage % on baseline (None = JaCoCo failed)
    migrated_pct: float | None   # line coverage % after migration
    drop_pp: float | None        # baseline - migrated (None if either unavailable)
    passed: bool                 # drop <= 5 pp, or measurement unavailable
    detail: str


def _run_with_jacoco(repo_dir: Path, java_version: int) -> subprocess.CompletedProcess:
    """Run mvn prepare-agent + verify + report in the sandbox container."""
    switch = "" if java_version == 8 else f". use-java.sh {java_version} && "
    command = (
        f"{switch}cd /workspace && "
        f"mvn -B {_JACOCO}:prepare-agent clean verify {_JACOCO}:report"
    )
    return subprocess.run(
        [
            "docker", "run", "--rm",
            "-v", f"{repo_dir.resolve()}:/workspace",
            "-v", f"{M2_VOLUME}:/root/.m2",
            IMAGE, "bash", "-c", command,
        ],
        capture_output=True,
        text=True,
    )


def _parse_jacoco_xml(xml_path: Path) -> tuple[int, int]:
    """Return (covered_lines, missed_lines) from a JaCoCo XML report.

    Strips the DOCTYPE declaration before parsing to avoid ElementTree
    trying to resolve the external DTD reference.
    """
    text = xml_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<!DOCTYPE[^>]+>", "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return 0, 0
    # The root <report> element has aggregate <counter> children.
    for counter in root.findall("counter"):
        if counter.get("type") == "LINE":
            return int(counter.get("covered", 0)), int(counter.get("missed", 0))
    return 0, 0


def _total_line_coverage(repo_dir: Path) -> float | None:
    """Sum LINE counters across all jacoco.xml files found under repo_dir."""
    xmls = list(repo_dir.rglob("target/site/jacoco/jacoco.xml"))
    if not xmls:
        return None
    covered = missed = 0
    for xml in xmls:
        c, m = _parse_jacoco_xml(xml)
        covered += c
        missed += m
    total = covered + missed
    if total == 0:
        return None
    return 100.0 * covered / total


def measure_coverage(repo_dir: Path, java_version: int = 8) -> float | None:
    """Run JaCoCo against repo_dir and return line coverage %, or None on failure."""
    result = _run_with_jacoco(repo_dir, java_version)
    if result.returncode != 0:
        return None
    return _total_line_coverage(repo_dir)


def check_coverage(
    baseline_pct: float | None,
    migrated_dir: Path,
    java_version: int = 17,
) -> CoverageResult:
    """Measure migrated coverage and check against baseline."""
    migrated_pct = measure_coverage(migrated_dir, java_version)

    if baseline_pct is None or migrated_pct is None:
        return CoverageResult(
            baseline_pct=baseline_pct,
            migrated_pct=migrated_pct,
            drop_pp=None,
            passed=True,  # can't measure → inconclusive, don't penalise
            detail=(
                "coverage measurement unavailable "
                f"(baseline={baseline_pct}, migrated={migrated_pct})"
            ),
        )

    drop = baseline_pct - migrated_pct
    passed = drop <= _MAX_DROP_PP
    return CoverageResult(
        baseline_pct=baseline_pct,
        migrated_pct=migrated_pct,
        drop_pp=drop,
        passed=passed,
        detail=(
            f"baseline {baseline_pct:.1f}% → migrated {migrated_pct:.1f}% "
            f"(drop {drop:+.1f} pp, limit {_MAX_DROP_PP} pp)"
        ),
    )
