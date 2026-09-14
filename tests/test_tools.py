"""Tests for S14: agent tool layer.

Docker-dependent tools (run_maven) are tested only for the allowlist guard;
the actual Docker invocation is integration-only.
"""

import textwrap
from pathlib import Path

import pytest

from migration_agent.tools import (
    _truncate,
    apply_patch,
    grep_files,
    list_dir,
    read_file,
    run_command,
    run_maven,
    write_file,
)


# ---------------------------------------------------------------------------
# _truncate helper
# ---------------------------------------------------------------------------

def test_truncate_short_passthrough():
    text = "\n".join(str(i) for i in range(5))
    assert _truncate(text) == text


def test_truncate_long_inserts_notice():
    lines = [str(i) for i in range(200)]
    result = _truncate("\n".join(lines), head=10, tail=10)
    assert "omitted" in result
    assert "0" in result      # first lines present
    assert "199" in result    # last lines present
    assert "10" not in result.split("omitted")[0].split("\n")[-2]  # line 10 not in head


def test_truncate_exact_limit_no_truncation():
    lines = [str(i) for i in range(20)]
    result = _truncate("\n".join(lines), head=10, tail=10)
    assert "omitted" not in result


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

def test_read_file_ok(tmp_path):
    (tmp_path / "Hello.java").write_text("public class Hello {}", encoding="utf-8")
    r = read_file(tmp_path, "Hello.java")
    assert r["ok"] is True
    assert "Hello" in r["output"]


def test_read_file_not_found(tmp_path):
    r = read_file(tmp_path, "Missing.java")
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


def test_read_file_path_traversal(tmp_path):
    r = read_file(tmp_path, "../../etc/passwd")
    assert r["ok"] is False
    assert "escapes" in r["error"].lower()


def test_read_file_too_large(tmp_path):
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * (256 * 1024 + 1))
    r = read_file(tmp_path, "big.txt")
    assert r["ok"] is False
    assert "large" in r["error"].lower()


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

def test_write_file_creates(tmp_path):
    r = write_file(tmp_path, "src/Main.java", "class Main{}")
    assert r["ok"] is True
    assert (tmp_path / "src" / "Main.java").read_text() == "class Main{}"


def test_write_file_overwrites(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("old")
    write_file(tmp_path, "f.txt", "new")
    assert f.read_text() == "new"


def test_write_file_path_traversal(tmp_path):
    r = write_file(tmp_path, "../evil.txt", "bad")
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------

def test_list_dir_root(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "src").mkdir()
    r = list_dir(tmp_path)
    assert r["ok"] is True
    assert "pom.xml" in r["output"]
    assert "src" in r["output"]


def test_list_dir_subdir(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "A.java").write_text("class A{}")
    r = list_dir(tmp_path, "sub")
    assert r["ok"] is True
    assert "A.java" in r["output"]


def test_list_dir_not_found(tmp_path):
    r = list_dir(tmp_path, "nonexistent")
    assert r["ok"] is False


# ---------------------------------------------------------------------------
# grep_files
# ---------------------------------------------------------------------------

def test_grep_finds_match(tmp_path):
    (tmp_path / "A.java").write_text("public class Foo {\n  void bar() {}\n}")
    r = grep_files(tmp_path, "void bar", include="*.java")
    assert r["ok"] is True
    assert "bar" in r["output"]
    assert "A.java" in r["output"]


def test_grep_no_match(tmp_path):
    (tmp_path / "A.java").write_text("class A {}")
    r = grep_files(tmp_path, "XXXX_NEVER", include="*.java")
    assert r["ok"] is True
    assert "no matches" in r["output"]


def test_grep_case_insensitive(tmp_path):
    (tmp_path / "A.java").write_text("void Baz() {}")
    r = grep_files(tmp_path, "baz", case_insensitive=True, include="*.java")
    assert r["ok"] is True
    assert "Baz" in r["output"]


def test_grep_invalid_regex(tmp_path):
    r = grep_files(tmp_path, "[invalid(", include="*.java")
    assert r["ok"] is False
    assert "regex" in r["error"].lower()


# ---------------------------------------------------------------------------
# apply_patch
# ---------------------------------------------------------------------------

def test_apply_patch_simple(tmp_path):
    (tmp_path / "Foo.java").write_text("class Foo {\n  int x = 1;\n}\n")
    patch = textwrap.dedent("""\
        --- a/Foo.java
        +++ b/Foo.java
        @@ -1,3 +1,3 @@
         class Foo {
        -  int x = 1;
        +  int x = 2;
         }
    """)
    r = apply_patch(tmp_path, patch)
    assert r["ok"] is True
    content = (tmp_path / "Foo.java").read_text()
    assert "x = 2" in content
    assert "x = 1" not in content


def test_apply_patch_path_traversal(tmp_path):
    patch = textwrap.dedent("""\
        --- a/../../evil.txt
        +++ b/../../evil.txt
        @@ -0,0 +1 @@
        +bad
    """)
    r = apply_patch(tmp_path, patch)
    # Should fail or report error — escaping path must not succeed.
    assert r["error"] is not None or r["ok"] is False


# ---------------------------------------------------------------------------
# run_command allowlist
# ---------------------------------------------------------------------------

def test_run_command_disallowed(tmp_path):
    r = run_command(tmp_path, "rm", ["-rf", "."])
    assert r["ok"] is False
    assert "not in allowlist" in r["error"]


def test_run_command_shell_metacharacter(tmp_path):
    r = run_command(tmp_path, "ls", ["; rm -rf /"])
    assert r["ok"] is False
    assert "forbidden" in r["error"].lower()


def test_run_command_git_disallowed_subcommand(tmp_path):
    r = run_command(tmp_path, "git", ["push"])
    assert r["ok"] is False
    assert "not allowed" in r["error"]


def test_run_command_git_allowed_subcommand(tmp_path):
    # git status won't fail even on a non-repo (exits 128 with an error
    # message, but the important thing is it's not blocked by the allowlist).
    r = run_command(tmp_path, "git", ["status"])
    # ok may be False (not a git repo), but error must not be "not in allowlist"
    assert r["error"] is None or "not in allowlist" not in r["error"]


# ---------------------------------------------------------------------------
# run_maven allowlist
# ---------------------------------------------------------------------------

def test_run_maven_disallowed_goal(tmp_path):
    r = run_maven(tmp_path, "clean")  # "clean" alone is not in the allowlist
    assert r["ok"] is False
    assert "not allowed" in r["error"]


def test_run_maven_allowed_goals_accepted(tmp_path, monkeypatch):
    import subprocess as _sp
    monkeypatch.setattr(
        _sp, "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "BUILD SUCCESS", "stderr": ""})(),
    )
    for goal in ("compile", "test", "verify"):
        r = run_maven(tmp_path, goal)
        assert r["ok"] is True
