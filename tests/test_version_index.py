"""Tests for S20: version index builder."""

import json
import textwrap
from pathlib import Path

from migration_agent.version_index import (
    _write_index,
    collect_artifacts,
    query_latest_version,
)

# ---------------------------------------------------------------------------
# collect_artifacts
# ---------------------------------------------------------------------------

def _write_pom(tmp_path: Path, content: str) -> Path:
    pom = tmp_path / "pom.xml"
    pom.write_text(content, encoding="utf-8")
    return pom


def test_collect_artifacts_simple(tmp_path):
    pom = _write_pom(tmp_path, textwrap.dedent("""\
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <dependencies>
            <dependency>
              <groupId>org.springframework</groupId>
              <artifactId>spring-core</artifactId>
              <version>5.3.0</version>
            </dependency>
          </dependencies>
        </project>
    """))
    artifacts = collect_artifacts(pom)
    assert ("org.springframework", "spring-core") in artifacts


def test_collect_artifacts_skips_placeholder_version(tmp_path):
    """Deps without explicit version are still collected (version not required)."""
    pom = _write_pom(tmp_path, textwrap.dedent("""\
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <dependencies>
            <dependency>
              <groupId>org.junit.jupiter</groupId>
              <artifactId>junit-jupiter-api</artifactId>
            </dependency>
          </dependencies>
        </project>
    """))
    artifacts = collect_artifacts(pom)
    assert ("org.junit.jupiter", "junit-jupiter-api") in artifacts


def test_collect_artifacts_skips_placeholder_group(tmp_path):
    pom = _write_pom(tmp_path, textwrap.dedent("""\
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <dependencies>
            <dependency>
              <groupId>${project.groupId}</groupId>
              <artifactId>mymodule</artifactId>
              <version>1.0</version>
            </dependency>
          </dependencies>
        </project>
    """))
    artifacts = collect_artifacts(pom)
    assert not any(g.startswith("${") for g, _ in artifacts)


def test_collect_artifacts_multiple_deps(tmp_path):
    pom = _write_pom(tmp_path, textwrap.dedent("""\
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <dependencies>
            <dependency>
              <groupId>com.google.guava</groupId>
              <artifactId>guava</artifactId>
              <version>32.0.0-jre</version>
            </dependency>
            <dependency>
              <groupId>org.slf4j</groupId>
              <artifactId>slf4j-api</artifactId>
              <version>2.0.0</version>
            </dependency>
          </dependencies>
        </project>
    """))
    artifacts = collect_artifacts(pom)
    assert ("com.google.guava", "guava") in artifacts
    assert ("org.slf4j", "slf4j-api") in artifacts


def test_collect_artifacts_invalid_xml(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text("NOT XML", encoding="utf-8")
    # Should return empty set, not raise.
    assert collect_artifacts(pom) == set()


# ---------------------------------------------------------------------------
# query_latest_version (mocked)
# ---------------------------------------------------------------------------

def test_query_returns_version(monkeypatch):
    import urllib.request

    fake_response = {
        "response": {
            "docs": [{"latestVersion": "33.1.0-jre", "g": "com.google.guava", "a": "guava"}]
        }
    }

    class _Resp:
        def read(self): return json.dumps(fake_response).encode()
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())
    version = query_latest_version("com.google.guava", "guava")
    assert version == "33.1.0-jre"


def test_query_returns_none_on_error(monkeypatch):
    import urllib.error
    import urllib.request

    def fail(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert query_latest_version("bad.group", "bad-artifact") is None


def test_query_returns_none_on_empty_docs(monkeypatch):
    import urllib.request

    fake_response = {"response": {"docs": []}}

    class _Resp:
        def read(self): return json.dumps(fake_response).encode()
        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Resp())
    assert query_latest_version("nonexistent.group", "nothing") is None


# ---------------------------------------------------------------------------
# _write_index
# ---------------------------------------------------------------------------

def test_write_index_structure(tmp_path):
    index = {"com.google.guava:guava": "33.1.0-jre", "org.slf4j:slf4j-api": "2.0.9"}
    path = tmp_path / "version_index.json"
    _write_index(path, index, "reporting_50.json")
    data = json.loads(path.read_text())
    assert data["count"] == 2
    assert data["index"]["com.google.guava:guava"] == "33.1.0-jre"
    assert "generated_at" in data
    assert data["manifest"] == "reporting_50.json"
