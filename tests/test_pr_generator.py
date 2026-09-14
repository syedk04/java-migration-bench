"""Tests for S26: PR generator (offline — no network, no GitHub calls)."""

import json
import tempfile
from pathlib import Path

from migration_agent.pr_generator import collect_passing, pr_body


def test_pr_body_minimal_pass():
    body = pr_body(
        repo="org/repo",
        track="T3",
        base_commit="abc123",
        diff_text=(
            "--- a/pom.xml\n+++ b/pom.xml\n@@ -1 +1 @@\n"
            "-<java.version>8</java.version>\n+<java.version>17</java.version>\n"
        ),
        minimal=True,
        maximal=False,
        calls_used=12,
        maximal_detail="3 deps checked, 1 outdated",
    )
    assert "Java 8 → 17 migration" in body
    assert "T3" in body
    assert "org/repo" in body
    assert "abc123" in body
    assert "12 LLM calls" in body
    # r1-r4 checked, r5 unchecked
    assert "- [x] r1:" in body
    assert "- [x] r4:" in body
    assert "- [ ] r5:" in body
    assert "3 deps checked" in body


def test_pr_body_maximal_pass():
    body = pr_body(
        repo="org/repo",
        track="T4",
        base_commit="def456",
        diff_text="diff line",
        minimal=True,
        maximal=True,
        calls_used=25,
        maximal_detail="10 deps checked, 0 outdated",
    )
    assert "- [x] r5:" in body
    assert "10 deps checked" in body


def test_pr_body_static_track():
    body = pr_body(
        repo="org/repo",
        track="T1",
        base_commit="aaa",
        diff_text="",
        minimal=True,
        maximal=False,
        calls_used=0,
    )
    assert "static tool (no LLM calls)" in body


def test_pr_body_long_diff_truncated():
    long_diff = "\n".join(f"+line {i}" for i in range(300))
    body = pr_body(
        repo="org/repo",
        track="T3",
        base_commit="aaa",
        diff_text=long_diff,
        minimal=True,
        maximal=False,
        calls_used=5,
    )
    assert "diff truncated" in body
    assert "300 lines total" in body


def test_pr_body_short_diff_inlined():
    short_diff = "+one line\n"
    body = pr_body(
        repo="org/repo",
        track="T3",
        base_commit="aaa",
        diff_text=short_diff,
        minimal=True,
        maximal=False,
        calls_used=5,
    )
    assert "diff truncated" not in body
    assert "+one line" in body


def test_collect_passing_empty_when_file_missing():
    results = collect_passing(Path("/nonexistent/path.json"), "T3")
    assert results == []


def test_collect_passing_filters_minimal():
    records = [
        {"repo": "a/b", "minimal": True, "maximal": False, "base_commit": "x"},
        {"repo": "c/d", "minimal": False, "maximal": False, "base_commit": "y"},
        {"repo": "e/f", "minimal": True, "maximal": True, "base_commit": "z"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(records, f)
        fname = f.name

    passing = collect_passing(Path(fname), "T3")
    assert len(passing) == 2
    repos = {r["repo"] for r in passing}
    assert "a/b" in repos
    assert "e/f" in repos
    assert "c/d" not in repos


def test_collect_passing_augments_diff_path():
    records = [
        {"repo": "myorg/myrepo", "minimal": True, "maximal": False, "base_commit": "abc"},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(records, f)
        fname = f.name

    passing = collect_passing(Path(fname), "T3")
    assert len(passing) == 1
    assert "diff_path" in passing[0]
    assert "myorg__myrepo" in passing[0]["diff_path"]
    assert "t3" in passing[0]["diff_path"].lower()
