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


_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def _load_pom(pom_path: Path) -> ET.Element:
    """Parse a pom.xml. Raises ET.ParseError if it is not well-formed XML."""
    text = pom_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"<!DOCTYPE[^>]+>", "", text)
    # Parse with namespaces intact and strip them from tags afterwards.
    # (Regex-stripping xmlns declarations left xsi:schemaLocation with an
    # unbound prefix, so nearly every real pom failed to parse.)
    return ET.fromstring(text)


def _pom_properties(root: ET.Element) -> dict[str, str]:
    """Return the <properties> block of a parsed pom as a flat dict."""
    props: dict[str, str] = {}
    for el in root:
        if _strip_ns(el.tag) == "properties":
            for p in el:
                if isinstance(p.tag, str):
                    props[_strip_ns(p.tag)] = (p.text or "").strip()
    return props


def _resolve(value: str, props: dict[str, str]) -> str:
    """Substitute ${name} placeholders from *props*, following nested refs.

    Placeholders that can't be resolved are left in place.
    """
    for _ in range(5):
        new = _PLACEHOLDER_RE.sub(lambda m: props.get(m.group(1), m.group(0)), value)
        if new == value:
            break
        value = new
    return value


def _parse_pom_deps(
    pom_path: Path, props: dict[str, str] | None = None
) -> list[tuple[str, str, str]]:
    """Return (groupId, artifactId, version) triples from a pom.xml.

    ${...} versions are resolved from *props* (if given) overlaid with the
    pom's own <properties>. Versions that still contain a placeholder after
    resolution are skipped — we can't resolve them without a full Maven build.

    Raises ET.ParseError if the pom is not well-formed XML.
    """
    root = _load_pom(pom_path)
    merged = {**(props or {}), **_pom_properties(root)}

    deps: list[tuple[str, str, str]] = []
    for dep in root.iter():
        if _strip_ns(dep.tag) != "dependency":
            continue
        children = {_strip_ns(c.tag): (c.text or "").strip() for c in dep}
        g = children.get("groupId", "")
        a = children.get("artifactId", "")
        v = _resolve(children.get("version", ""), merged)
        if g and a and v and "${" not in v:
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

    ${...} versions are resolved from <properties>: the pom's own block first,
    then the union of every other pom in the repo (approximates inheritance
    from an in-repo parent without walking relativePath).

    Limitation: dependencies with no <version> at all (managed by a parent POM
    or BOM import) are not checked. The BOM import itself is checked when its
    version is resolvable. A repo where nothing is checkable returns checked=0
    and passes vacuously; the detail string flags this.
    """
    outdated: list[str] = []
    skipped: list[str] = []
    checked = 0

    poms = list(repo_dir.rglob("pom.xml"))
    repo_props: dict[str, str] = {}
    for pom in poms:
        try:
            repo_props.update(_pom_properties(_load_pom(pom)))
        except ET.ParseError:
            pass

    for pom in poms:
        try:
            deps = _parse_pom_deps(pom, repo_props)
        except ET.ParseError:
            skipped.append(f"{pom.relative_to(repo_dir).as_posix()} (unparseable pom)")
            continue
        for g, a, v in deps:
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
