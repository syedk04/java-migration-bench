"""Unit tests for tamper gate — no Docker required."""

import tempfile
from pathlib import Path

from migration_agent.tamper import check_tamper, snapshot_tests

# --- helpers ---

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _pom(path: Path, extra: str = "") -> None:
    _write(
        path,
        f'<project><modelVersion>4.0.0</modelVersion>'
        f'<groupId>t</groupId><artifactId>t</artifactId>'
        f'<version>1.0</version>{extra}</project>',
    )


_BASELINE_SRC = """\
import org.junit.jupiter.api.Test;
public class FooTest {
    @Test
    public void testA() {
        assertEquals(1, 1);
    }

    @Test
    public void testB() {
        assertTrue(true);
    }
}
"""

_NO_CHANGE = _BASELINE_SRC  # identical to baseline

_METHOD_REMOVED = """\
import org.junit.jupiter.api.Test;
public class FooTest {
    @Test
    public void testA() {
        assertEquals(1, 1);
    }
}
"""

_METHOD_DISABLED = """\
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Disabled;
public class FooTest {
    @Test
    public void testA() {
        assertEquals(1, 1);
    }

    @Disabled
    @Test
    public void testB() {
        assertTrue(true);
    }
}
"""

_BODY_EMPTIED = """\
import org.junit.jupiter.api.Test;
public class FooTest {
    @Test
    public void testA() {
        assertEquals(1, 1);
    }

    @Test
    public void testB() {
    }
}
"""


def _make_repo(tmp: str, java_src: str, pom_extra: str = "") -> Path:
    root = Path(tmp)
    _write(root / "src" / "test" / "java" / "FooTest.java", java_src)
    _pom(root / "pom.xml", pom_extra)
    return root


# --- snapshot tests ---

def test_snapshot_counts_methods():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _BASELINE_SRC)
        snap = snapshot_tests(root)
        assert snap.total_count == 2
        names = {m.name for m in snap.methods}
        assert names == {"testA", "testB"}


def test_snapshot_baseline_not_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _BASELINE_SRC)
        snap = snapshot_tests(root)
        assert all(not m.disabled for m in snap.methods)


# --- check_tamper: clean migration ---

def test_no_change_passes():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _BASELINE_SRC)
        snap = snapshot_tests(root)
        # "migrate" without touching test source
        result = check_tamper(snap, root)
        assert not result.tampered
        assert result.r3_test_invariance
        assert result.r4_test_count
        assert result.pom_clean


# --- r4: count ---

def test_removed_method_fails_r4_and_r3():
    with tempfile.TemporaryDirectory() as tmp_b, tempfile.TemporaryDirectory() as tmp_m:
        snap = snapshot_tests(_make_repo(tmp_b, _BASELINE_SRC))
        migrated = _make_repo(tmp_m, _METHOD_REMOVED)
        result = check_tamper(snap, migrated)
        assert result.tampered
        assert not result.r4_test_count
        assert not result.r3_test_invariance


# --- r3: disabled ---

def test_newly_disabled_fails_r3():
    with tempfile.TemporaryDirectory() as tmp_b, tempfile.TemporaryDirectory() as tmp_m:
        snap = snapshot_tests(_make_repo(tmp_b, _BASELINE_SRC))
        migrated = _make_repo(tmp_m, _METHOD_DISABLED)
        result = check_tamper(snap, migrated)
        assert result.tampered
        assert not result.r3_test_invariance
        assert any("Disabled" in v or "Ignore" in v for v in result.violations)


# --- r3: body changed ---

def test_emptied_body_fails_r3():
    with tempfile.TemporaryDirectory() as tmp_b, tempfile.TemporaryDirectory() as tmp_m:
        snap = snapshot_tests(_make_repo(tmp_b, _BASELINE_SRC))
        migrated = _make_repo(tmp_m, _BODY_EMPTIED)
        result = check_tamper(snap, migrated)
        assert result.tampered
        assert not result.r3_test_invariance
        assert any("body changed" in v for v in result.violations)


# --- pom checks ---

def test_skip_tests_in_pom_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _BASELINE_SRC,
                          pom_extra="<properties><skipTests>true</skipTests></properties>")
        snap = snapshot_tests(root)
        result = check_tamper(snap, root)
        assert result.tampered
        assert not result.pom_clean
        assert any("skip" in v.lower() for v in result.violations)


def test_failure_ignore_in_pom_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(tmp, _BASELINE_SRC,
                          pom_extra="<properties>"
                                    "<maven.test.failure.ignore>true</maven.test.failure.ignore>"
                                    "</properties>")
        snap = snapshot_tests(root)
        result = check_tamper(snap, root)
        assert result.tampered
        assert not result.pom_clean


def test_surefire_excludes_in_pom_fails():
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_repo(
            tmp, _BASELINE_SRC,
            pom_extra=(
                "<build><plugins><plugin>"
                "<artifactId>maven-surefire-plugin</artifactId>"
                "<configuration><excludes><exclude>**/SlowTest.java</exclude></excludes>"
                "</configuration></plugin></plugins></build>"
            ),
        )
        snap = snapshot_tests(root)
        result = check_tamper(snap, root)
        assert result.tampered
        assert not result.pom_clean
