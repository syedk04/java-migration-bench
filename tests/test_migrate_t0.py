"""Unit tests for T0 migration (compiler version bump) — no Docker required."""

import tempfile
from pathlib import Path

from migration_agent.migrate_t0 import _inject_compiler_properties, _patch_pom, apply_t0

# --- helpers ---

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- _patch_pom ---

def test_patch_property_source_target():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><properties>"
            "<maven.compiler.source>8</maven.compiler.source>"
            "<maven.compiler.target>8</maven.compiler.target>"
            "</properties></project>",
        )
        changed = _patch_pom(pom)
        assert changed
        text = pom.read_text()
        assert "<maven.compiler.source>17</maven.compiler.source>" in text
        assert "<maven.compiler.target>17</maven.compiler.target>" in text


def test_patch_plugin_source_target():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><build><plugins><plugin>"
            "<configuration><source>1.8</source><target>1.8</target></configuration>"
            "</plugin></plugins></build></project>",
        )
        changed = _patch_pom(pom)
        assert changed
        text = pom.read_text()
        assert "<source>17</source>" in text
        assert "<target>17</target>" in text


def test_patch_release_property():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><properties>"
            "<maven.compiler.release>11</maven.compiler.release>"
            "</properties></project>",
        )
        _patch_pom(pom)
        assert "<maven.compiler.release>17</maven.compiler.release>" in pom.read_text()


def test_patch_already_17_returns_false():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><properties>"
            "<maven.compiler.source>17</maven.compiler.source>"
            "</properties></project>",
        )
        changed = _patch_pom(pom)
        assert not changed


# --- _inject_compiler_properties ---

def test_inject_into_existing_properties():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><properties><some.prop>x</some.prop></properties></project>",
        )
        injected = _inject_compiler_properties(pom)
        assert injected
        text = pom.read_text()
        assert "maven.compiler.source" in text
        assert "17" in text


def test_inject_creates_properties_block():
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><groupId>g</groupId></project>",
        )
        injected = _inject_compiler_properties(pom)
        assert injected
        text = pom.read_text()
        assert "<properties>" in text
        assert "maven.compiler.source" in text


def test_inject_skips_if_already_patched():
    """After _patch_pom runs, _inject should detect existing settings and skip."""
    with tempfile.TemporaryDirectory() as d:
        pom = _write(
            Path(d) / "pom.xml",
            "<project><properties>"
            "<maven.compiler.source>8</maven.compiler.source>"
            "</properties></project>",
        )
        _patch_pom(pom)  # sets to 17
        injected = _inject_compiler_properties(pom)
        assert not injected


# --- apply_t0 ---

def test_apply_t0_counts():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # Module 1: has settings to patch
        _write(
            root / "module-a" / "pom.xml",
            "<project><properties>"
            "<maven.compiler.source>8</maven.compiler.source>"
            "</properties></project>",
        )
        # Module 2: no settings — will get injected
        _write(
            root / "module-b" / "pom.xml",
            "<project><groupId>g</groupId></project>",
        )
        summary = apply_t0(root)
        assert summary["patched"] == 1
        assert summary["injected"] == 1
        assert summary["unchanged"] == 0


def test_apply_t0_idempotent():
    """Running T0 twice should not change anything on the second run."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write(
            root / "pom.xml",
            "<project><properties>"
            "<maven.compiler.source>8</maven.compiler.source>"
            "</properties></project>",
        )
        apply_t0(root)
        summary2 = apply_t0(root)
        assert summary2["patched"] == 0
        assert summary2["injected"] == 0
        assert summary2["unchanged"] == 1
