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
    """load_version_index returns {} when data/version_index.json doesn't exist yet."""
    from migration_agent.maximal import VERSION_INDEX_PATH, load_version_index
    if not VERSION_INDEX_PATH.exists():
        assert load_version_index() == {}


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
