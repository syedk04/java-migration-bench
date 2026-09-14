"""Tests for S22: transcript auditor."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from migration_agent.agent_loop import _traj_record
from migration_agent.transcript_audit import (
    _AUDITOR_SYSTEM,
    traj_to_text,
)

# ---------------------------------------------------------------------------
# traj_to_text
# ---------------------------------------------------------------------------

def _write_traj(path: Path, records: list[dict]) -> None:
    for rec in records:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")


def test_traj_to_text_basic(tmp_path):
    traj = tmp_path / "traj.jsonl"
    _write_traj(traj, [
        _traj_record(0, "user", "List the root."),
        _traj_record(1, "model", "TOOL: list_dir",
                     tool_call={"tool": "list_dir", "path": "."}),
        _traj_record(2, "tool_result", "pom.xml",
                     tool_result={"ok": True, "output": "pom.xml", "error": None}),
    ])
    text = traj_to_text(traj)
    assert "USER" in text
    assert "MODEL" in text
    assert "TOOL CALL" in text
    assert "TOOL RESULT" in text


def test_traj_to_text_skips_terminal(tmp_path):
    traj = tmp_path / "traj.jsonl"
    _write_traj(traj, [
        _traj_record(0, "user", "hello"),
        {"role": "terminal", "minimal": True, "ts": "2026-09-14T00:00:00Z"},
    ])
    text = traj_to_text(traj)
    assert "terminal" not in text.lower()
    assert "USER" in text


def test_traj_to_text_truncates_long(tmp_path):
    traj = tmp_path / "traj.jsonl"
    # Write many turns to force truncation.
    for i in range(200):
        _write_traj(traj, [_traj_record(i, "user", f"message {i} " + "x" * 50)])
    text = traj_to_text(traj)
    assert "truncated" in text


def test_traj_to_text_empty_file(tmp_path):
    traj = tmp_path / "empty.jsonl"
    traj.write_text("")
    text = traj_to_text(traj)
    assert text == ""


# ---------------------------------------------------------------------------
# Auditor system prompt sanity
# ---------------------------------------------------------------------------

def test_auditor_system_contains_classifications():
    for label in ("GENUINE", "RETRIEVED", "GAMED"):
        assert label in _AUDITOR_SYSTEM


def test_auditor_system_mentions_tests():
    assert "test" in _AUDITOR_SYSTEM.lower()


# ---------------------------------------------------------------------------
# audit_one (mocked Gemini)
# ---------------------------------------------------------------------------

def test_audit_one_parses_classification(tmp_path, monkeypatch):
    from migration_agent.transcript_audit import audit_one

    traj = tmp_path / "traj.jsonl"
    _write_traj(traj, [_traj_record(0, "user", "hello")])

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    # Mock git diff.
    import subprocess
    def fake_run(cmd, **kw):
        r = MagicMock()
        r.stdout = (
            "--- a/pom.xml\n+++ b/pom.xml\n"
            "@@ -1 +1 @@\n-<source>8</source>\n+<source>17</source>\n"
        )
        return r
    monkeypatch.setattr(subprocess, "run", fake_run)

    # Mock Gemini response.
    resp = MagicMock()
    resp.text = "CLASSIFICATION: GENUINE\nThe agent correctly bumped the Java version."
    resp.prompt_tokens = 100
    resp.completion_tokens = 20

    client = MagicMock()
    client.chat.return_value = resp

    result = audit_one(traj, repo_dir, client)
    assert result["classification"] == "GENUINE"
    assert "GENUINE" in result["reasoning"]


def test_audit_one_unknown_classification(tmp_path, monkeypatch):
    from migration_agent.transcript_audit import audit_one

    traj = tmp_path / "traj.jsonl"
    _write_traj(traj, [_traj_record(0, "user", "hello")])
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: MagicMock(stdout=""))

    resp = MagicMock()
    resp.text = "I cannot determine the classification."
    resp.prompt_tokens = 50
    resp.completion_tokens = 10

    client = MagicMock()
    client.chat.return_value = resp

    result = audit_one(traj, repo_dir, client)
    assert result["classification"] == "UNKNOWN"
