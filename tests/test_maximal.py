"""Unit tests for maximal.py — no network or Docker required."""

import tempfile
from pathlib import Path

from migration_agent.maximal import _major, _parse_pom_deps, check_maximal

_INDEX = {
    "org.springframework:spring-core": "6.1.0",
    "junit:junit": "4.13.2",
    "com.google.guava:guava": "33.0.0-jre",
}

_POM_CURRENT = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>6.1.0</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
    </dependency>
  </dependencies>
</project>
"""

_POM_OUTDATED = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>5.3.30</version>
    </dependency>
  </dependencies>
</project>
"""

_POM_PROPERTY_VERSION = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>${spring.version}</version>
    </dependency>
  </dependencies>
</project>
"""

_POM_UNKNOWN_DEP = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>com.internal</groupId>
      <artifactId>secret-lib</artifactId>
      <version>1.0.0</version>
    </dependency>
  </dependencies>
</project>
"""


def _make_repo(tmp: str, pom_content: str) -> Path:
    root = Path(tmp)
    (root / "pom.xml").write_text(pom_content, encoding="utf-8")
    return root


# --- _major ---

def test_major_simple():
    assert _major("6.1.0") == 6
    assert _major("4.13.2") == 4
    assert _major("33.0.0-jre") == 33


def test_major_unparseable():
    assert _major("RELEASE") is None
    assert _major("") is None


# --- _parse_pom_deps ---

def test_parse_finds_deps():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pom.xml"
        p.write_text(_POM_CURRENT, encoding="utf-8")
        deps = _parse_pom_deps(p)
        assert len(deps) == 2
        assert ("org.springframework", "spring-core", "6.1.0") in deps


def test_parse_skips_property_versions():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pom.xml"
        p.write_text(_POM_PROPERTY_VERSION, encoding="utf-8")
        deps = _parse_pom_deps(p)
        assert deps == []


# --- check_maximal ---

def test_all_current_passes():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_CURRENT)
        result = check_maximal(root, _INDEX)
        assert result.passed
        assert result.checked == 2
        assert result.outdated == []


def test_outdated_dep_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_OUTDATED)
        result = check_maximal(root, _INDEX)
        assert not result.passed
        assert len(result.outdated) == 1
        assert "spring-core" in result.outdated[0]
        assert "5.3.30" in result.outdated[0]
        assert "6.1.0" in result.outdated[0]


def test_unknown_dep_skipped_not_failed():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_UNKNOWN_DEP)
        result = check_maximal(root, _INDEX)
        assert result.passed          # unknown = skip, not fail
        assert result.checked == 0
        assert "com.internal:secret-lib" in result.skipped


def test_empty_index_skips_all():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_CURRENT)
        result = check_maximal(root, {})  # index not yet built (pre-S20)
        assert result.passed
        assert result.checked == 0
        assert len(result.skipped) == 2


def test_load_version_index_missing_returns_empty():
    from migration_agent.maximal import load_version_index
    with tempfile.TemporaryDirectory() as tmp:
        assert load_version_index(Path(tmp) / "nope.json") == {}


def test_load_version_index_unwraps_crawl_format():
    from migration_agent.maximal import load_version_index
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "idx.json"
        p.write_text('{"generated_at": "x", "index": {"a:b": "1.0"}}', encoding="utf-8")
        assert load_version_index(p) == {"a:b": "1.0"}


def test_default_index_is_paper_reference():
    """r5 is scored against the paper's Nov-2024 list, not our 2026 crawl."""
    from migration_agent.maximal import load_version_index
    ref = load_version_index()
    assert len(ref) == 240
    assert ref["org.springframework.boot:spring-boot-starter-parent"] == "3.3.4"
    assert ref["junit:junit"] == "4.13.2"


_POM_BOM_MANAGED = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>2.7.0</version>
  </parent>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
    </dependency>
  </dependencies>
</project>
"""


def test_bom_managed_passes_vacuously():
    """BOM-managed deps (no explicit <version>) are invisible to the scanner.
    Result: checked=0, passed=True (vacuous), detail contains 'vacuous' flag.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_BOM_MANAGED)
        result = check_maximal(root, _INDEX)
        assert result.passed          # vacuous pass
        assert result.checked == 0
        assert result.outdated == []
        assert "vacuous" in result.detail


def test_unknown_dep_detail_is_vacuous():
    """Repos with no indexable deps also get the vacuous flag in detail."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_UNKNOWN_DEP)
        result = check_maximal(root, _INDEX)
        assert result.checked == 0
        assert "vacuous" in result.detail


# Real-world pom header: default namespace + xsi:schemaLocation. The old
# parser regex-stripped xmlns declarations, leaving xsi: unbound, so every
# pom like this silently parsed to zero deps and maximal passed vacuously.
_POM_REAL_HEADER = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>5.3.30</version>
    </dependency>
  </dependencies>
</project>
"""


def test_parse_real_pom_header():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pom.xml"
        p.write_text(_POM_REAL_HEADER, encoding="utf-8")
        assert _parse_pom_deps(p) == [("org.springframework", "spring-core", "5.3.30")]


def test_real_pom_header_outdated_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_REAL_HEADER)
        result = check_maximal(root, _INDEX)
        assert not result.passed
        assert result.checked == 1
        assert "vacuous" not in result.detail


def test_unparseable_pom_is_reported_not_silent():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, "<project><dependencies></project>")
        result = check_maximal(root, _INDEX)
        assert result.checked == 0
        assert any("unparseable pom" in s for s in result.skipped)


# --- property resolution ---

_POM_PROPS_SAME_FILE = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>test</artifactId><version>1.0</version>
  <properties>
    <spring.major>5</spring.major>
    <spring.version>${spring.major}.3.30</spring.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework</groupId>
      <artifactId>spring-core</artifactId>
      <version>${spring.version}</version>
    </dependency>
  </dependencies>
</project>
"""


def test_parse_resolves_nested_same_file_property():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "pom.xml"
        p.write_text(_POM_PROPS_SAME_FILE, encoding="utf-8")
        assert _parse_pom_deps(p) == [("org.springframework", "spring-core", "5.3.30")]


def test_property_version_outdated_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_PROPS_SAME_FILE)
        result = check_maximal(root, _INDEX)
        assert not result.passed
        assert result.checked == 1


def test_property_from_parent_pom_in_repo():
    parent = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>test</groupId><artifactId>parent</artifactId><version>1.0</version>
  <packaging>pom</packaging>
  <properties><guava.version>20.0</guava.version></properties>
  <modules><module>child</module></modules>
</project>
"""
    child = """\
<project>
  <modelVersion>4.0.0</modelVersion>
  <artifactId>child</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>${guava.version}</version>
    </dependency>
  </dependencies>
</project>
"""
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, parent)
        (root / "child").mkdir()
        (root / "child" / "pom.xml").write_text(child, encoding="utf-8")
        result = check_maximal(root, _INDEX)
        assert not result.passed
        assert result.checked == 1
        assert "guava" in result.outdated[0]


# --- effective versions (mvn dependency:tree) ---

from migration_agent.maximal import (  # noqa: E402
    check_effective_versions,
    declared_coords,
    parse_dependency_tree,
)

_TREE = r"""[INFO] --- dependency:3.6.1:tree (default-cli) @ demo ---
[INFO] com.example:demo:jar:1.0
[INFO] +- org.springframework.boot:spring-boot-starter-web:jar:2.7.0:compile
[INFO] |  +- org.springframework:spring-web:jar:5.3.20:compile
[INFO] |  \- org.springframework:spring-core:jar:5.3.20:compile
[INFO] +- io.netty:netty-tcnative:jar:linux-x86_64:2.0.0:compile
[INFO] \- junit:junit:jar:4.13.2:test
[INFO]    \- org.hamcrest:hamcrest-core:jar:1.3:test
[INFO] BUILD SUCCESS
"""

_REF = {
    "org.springframework.boot:spring-boot-starter-web": "3.3.4",
    "org.springframework:spring-core": "6.1.13",
    "junit:junit": "4.13.2",
}


def test_parse_dependency_tree_direct_only():
    assert parse_dependency_tree(_TREE) == [
        ("org.springframework.boot:spring-boot-starter-web", "2.7.0"),
        ("junit:junit", "4.13.2"),
    ]  # transitive lines and the 6-part classifier coord are ignored


def test_effective_bom_managed_version_is_scored():
    """A starter with no <version> in the pom still fails on its resolved 2.x."""
    declared = {"org.springframework.boot:spring-boot-starter-web", "junit:junit"}
    r = check_effective_versions(_TREE, declared, _REF)
    assert not r.passed
    assert r.checked == 2
    assert "spring-boot-starter-web resolved 2.7.0" in r.outdated[0]


def test_effective_ignores_undeclared_and_transitive():
    r = check_effective_versions(_TREE, {"junit:junit"}, _REF)
    assert r.passed
    assert r.checked == 1


def test_effective_newer_major_passes():
    tree = r"[INFO] \- org.springframework.boot:spring-boot-starter-web:jar:4.0.0:compile" + "\n"
    r = check_effective_versions(
        tree, {"org.springframework.boot:spring-boot-starter-web"}, _REF
    )
    assert r.passed and r.checked == 1


def test_declared_coords_includes_versionless_deps():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _POM_BOM_MANAGED)
        assert declared_coords(root) == {"org.springframework:spring-core", "junit:junit"}
