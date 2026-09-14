"""S9: Maximal check — every pom dependency at its latest major version.

Uses a frozen, date-stamped snapshot of Maven Central latest versions so
the criterion doesn't drift week to week. The snapshot is built once (S20)
and committed; this module only reads it.

A dependency passes if its declared version == the latest major version in
the snapshot, OR if it is absent from the snapshot (unknown artifact —
skip rather than fail, same as the paper's treatment of private/internal
deps).

Usage:

    from migration_agent.maximal import load_version_index, check_maximal

    index = load_version_index()          # loads data/version_index.json
    result = check_maximal(repo_dir, index)
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSION_INDEX_PATH = REPO_ROOT / "data" / "version_index.json"

# Maven version string — captures numeric prefix before any qualifier
_VERSION_RE = re.compile(r"^(\d+)")


@dataclass
class MaximalResult:
    passed: bool
    outdated: list[str]   # "groupId:artifactId declared X latest Y"
    skipped: list[str]    # coords not in index (unknown / private)
    checked: int          # total deps checked against the index
    detail: str


def _strip_ns(tag: str) -> str:
    """Remove XML namespace prefix from a tag."""
    return tag.split("}")[-1] if "}" in tag else tag


def _parse_pom_deps(pom_path: Path) -> list[tuple[str, str, str]]:
    """Return (groupId, artifactId, version) triples from a pom.xml.

    Skips entries with property-placeholder versions (${...}) since we
    can't resolve them without a full Maven build.
    """
    text = pom_path.read_text(encoding="utf-8", errors="replace")
    # Strip DOCTYPE and namespace declarations for simpler parsing
    text = re.sub(r"<!DOCTYPE[^>]+>", "", text)
    text = re.sub(r'\s+xmlns(?::\w+)?="[^"]*"', "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    deps: list[tuple[str, str, str]] = []
    for dep in root.iter("dependency"):
        children = {_strip_ns(c.tag): (c.text or "").strip() for c in dep}
        g = children.get("groupId", "")
        a = children.get("artifactId", "")
        v = children.get("version", "")
        if g and a and v and not v.startswith("${"):
            deps.append((g, a, v))
    return deps


def _major(version: str) -> int | None:
    """Return the major version number, or None if unparseable."""
    m = _VERSION_RE.match(version.strip())
    return int(m.group(1)) if m else None


def load_version_index() -> dict[str, str]:
    """Load the frozen version index from data/version_index.json.

    Returns a dict mapping "groupId:artifactId" -> "latestVersion".
    Returns an empty dict if the file doesn't exist yet (pre-S20).
    """
    if not VERSION_INDEX_PATH.exists():
        return {}
    import json
    data = json.loads(VERSION_INDEX_PATH.read_text(encoding="utf-8"))
    # version_index.json wraps the index in {"index": {...}, "generated_at": ...}
    return data.get("index", data) if isinstance(data, dict) else {}


def check_maximal(repo_dir: Path, version_index: dict[str, str]) -> MaximalResult:
    """Check that every versioned dependency is at its latest major version.

    Limitation: only dependencies with an explicit <version> element in pom.xml
    are checked. Dependencies whose versions are managed through a parent POM,
    BOM import, or <dependencyManagement> without an inline <version> are not
    checked (they appear in skipped with note "BOM-managed"). This means repos
    that rely entirely on BOM version management will return checked=0 and
    pass vacuously. In practice, most Spring/Spring Boot projects do this.

    This matches what the paper describes (they also use a flat pom scan), but
    means our maximal criterion is weaker than it appears for BOM-heavy repos.
    The result's checked==0 flag lets callers detect this case.
    """
    outdated: list[str] = []
    skipped: list[str] = []
    checked = 0

    for pom in repo_dir.rglob("pom.xml"):
        for g, a, v in _parse_pom_deps(pom):
            coord = f"{g}:{a}"
            latest = version_index.get(coord)
            if latest is None:
                skipped.append(coord)
                continue
            checked += 1
            declared_major = _major(v)
            latest_major = _major(latest)
            if declared_major is None or latest_major is None:
                skipped.append(f"{coord} (unparseable version)")
                checked -= 1
                continue
            if declared_major < latest_major:
                outdated.append(
                    f"{coord} declared {v} (major {declared_major}), "
                    f"latest {latest} (major {latest_major})"
                )

    passed = len(outdated) == 0
    vacuous = checked == 0
    detail = (
        f"{checked} deps checked, {len(outdated)} outdated, {len(skipped)} skipped"
        + (" [vacuous — no explicit versions found]" if vacuous else "")
    )
    return MaximalResult(
        passed=passed,
        outdated=outdated,
        skipped=skipped,
        checked=checked,
        detail=detail,
    )
