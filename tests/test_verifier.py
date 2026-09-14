"""Unit tests for verifier.py — no Docker required."""

import struct
import tempfile
from pathlib import Path

from migration_agent.verifier import _read_major_version, check_bytecode_version


def _write_class(path: Path, major: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, major))


def test_read_major_version():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "Foo.class"
        _write_class(f, 61)
        assert _read_major_version(f) == 61


def test_read_major_version_java8():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "Bar.class"
        _write_class(f, 52)
        assert _read_major_version(f) == 52


def test_check_bytecode_version_pass():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_class(root / "target" / "classes" / "Foo.class", 61)
        _write_class(root / "target" / "classes" / "sub" / "Bar.class", 61)
        ok, detail = check_bytecode_version(root)
        assert ok
        assert "61" in detail


def test_check_bytecode_version_wrong_version():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_class(root / "target" / "classes" / "Foo.class", 52)
        ok, detail = check_bytecode_version(root)
        assert not ok
        assert "52" in detail


def test_check_bytecode_version_no_classes():
    with tempfile.TemporaryDirectory() as d:
        ok, detail = check_bytecode_version(Path(d))
        assert not ok
        assert "no production" in detail


def test_check_bytecode_version_ignores_test_classes():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        # test-classes should be excluded; no production classes present
        _write_class(root / "target" / "test-classes" / "FooTest.class", 52)
        ok, detail = check_bytecode_version(root)
        assert not ok
        assert "no production" in detail


def test_check_bytecode_version_mixed_fails():
    """If even one sampled file has the wrong version, the check fails."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _write_class(root / "target" / "classes" / "Good.class", 61)
        _write_class(root / "target" / "classes" / "Bad.class", 52)
        ok, detail = check_bytecode_version(root)
        assert not ok
