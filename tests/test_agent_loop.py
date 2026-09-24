"""Tests for S15: agent loop.

Mocks out the Gemini client, Docker calls, and filesystem cloning so the
loop logic can be exercised without network or Docker access.
"""

import json
from unittest.mock import MagicMock, patch

from migration_agent.agent_loop import (
    MAX_CALLS,
    AgentResult,
    _append_traj,
    _is_done,
    _load_traj,
    _parse_tool_call,
    _traj_record,
)

# ---------------------------------------------------------------------------
# _parse_tool_call
# ---------------------------------------------------------------------------

def test_parse_tool_call_valid():
    text = 'Sure, I will list the root.\nTOOL: {"tool":"list_dir","path":"."}'
    call = _parse_tool_call(text)
    assert call == {"tool": "list_dir", "path": "."}


def test_parse_tool_call_none_when_absent():
    assert _parse_tool_call("No tool here.") is None


def test_parse_tool_call_invalid_json_returns_none():
    assert _parse_tool_call("TOOL: {bad json}") is None


def test_parse_tool_call_missing_tool_key_returns_none():
    assert _parse_tool_call('TOOL: {"action":"read"}') is None


# ---------------------------------------------------------------------------
# _is_done
# ---------------------------------------------------------------------------

def test_is_done_true():
    assert _is_done("I have finished.\nDONE\n")


def test_is_done_false():
    assert not _is_done("Still working...")


def test_is_done_requires_exact_line():
    # "DONE" must be the entire line (strip), not embedded in text.
    assert not _is_done("NOT DONE YET")


# ---------------------------------------------------------------------------
# Trajectory helpers
# ---------------------------------------------------------------------------

def test_traj_record_fields():
    rec = _traj_record(1, "model", "hello world", prompt_tokens=5)
    assert rec["turn"] == 1
    assert rec["role"] == "model"
    assert rec["content_snippet"] == "hello world"
    assert rec["prompt_tokens"] == 5
    assert "ts" in rec


def test_traj_record_truncates_long_content():
    long_text = "x" * 1000
    rec = _traj_record(0, "user", long_text)
    assert len(rec["content_snippet"]) == 500


def test_append_and_load_traj(tmp_path):
    traj = tmp_path / "test.jsonl"
    rec1 = _traj_record(0, "user", "hello")
    rec2 = _traj_record(1, "model", "world")
    _append_traj(traj, rec1)
    _append_traj(traj, rec2)
    loaded = _load_traj(traj)
    assert len(loaded) == 2
    assert loaded[0]["role"] == "user"
    assert loaded[1]["role"] == "model"


def test_load_traj_nonexistent(tmp_path):
    assert _load_traj(tmp_path / "nope.jsonl") == []


# ---------------------------------------------------------------------------
# AgentResult.to_dict
# ---------------------------------------------------------------------------

def test_agent_result_to_dict_roundtrip():
    ar = AgentResult(
        repo="owner/repo", base_commit="abc123", track="T2",
        minimal=True, maximal=False, calls_used=5,
        total_prompt_tokens=100, total_completion_tokens=50,
        seconds=12.5,
    )
    d = ar.to_dict()
    assert d["repo"] == "owner/repo"
    assert d["minimal"] is True
    assert d["calls_used"] == 5
    # Round-trip through JSON.
    assert json.loads(json.dumps(d))["total_prompt_tokens"] == 100


# ---------------------------------------------------------------------------
# run_agent — mock integration
# ---------------------------------------------------------------------------

def _make_mock_response(text: str, prompt_tokens: int = 10, completion_tokens: int = 5):
    resp = MagicMock()
    resp.text = text
    resp.prompt_tokens = prompt_tokens
    resp.completion_tokens = completion_tokens
    return resp


@patch("migration_agent.agent_loop.clone_at_commit")
@patch("migration_agent.agent_loop.snapshot_tests")
@patch("migration_agent.agent_loop.verify")
@patch("migration_agent.agent_loop.check_tamper")
@patch("migration_agent.agent_loop.check_maximal_effective")
@patch("migration_agent.agent_loop.load_version_index")
@patch("migration_agent.agent_loop.GeminiClient")
def test_run_agent_done_on_first_response(
    MockClient, mock_load_idx, mock_maximal, mock_tamper,
    mock_verify, mock_snap, mock_clone,
    tmp_path,
):
    """Agent that replies DONE immediately — 1 call, FAIL (r1=False by default)."""
    from migration_agent.agent_loop import run_agent

    # Wire mocks.
    client_instance = MockClient.return_value
    client_instance.chat.return_value = _make_mock_response("I'm done.\nDONE")

    vr = MagicMock()
    vr.r1_build = False
    vr.r2_bytecode = False
    vr.maven_tail = ""
    mock_verify.return_value = vr
    mock_snap.return_value = MagicMock()
    mock_tamper.return_value = MagicMock(tampered=False, violations=[])
    mock_maximal.return_value = MagicMock(passed=False, detail="")
    mock_load_idx.return_value = {}

    ar = run_agent(
        "owner/repo", "abc123", "system prompt",
        track="T2",
        trajectory_dir=tmp_path / "traj",
        api_key="fake-key",
    )

    assert ar.calls_used == 1
    assert ar.minimal is False  # r1 is False


@patch("migration_agent.agent_loop.clone_at_commit")
@patch("migration_agent.agent_loop.snapshot_tests")
@patch("migration_agent.agent_loop.verify")
@patch("migration_agent.agent_loop.check_tamper")
@patch("migration_agent.agent_loop.check_maximal_effective")
@patch("migration_agent.agent_loop.load_version_index")
@patch("migration_agent.agent_loop.GeminiClient")
def test_run_agent_respects_max_calls(
    MockClient, mock_load_idx, mock_maximal, mock_tamper,
    mock_verify, mock_snap, mock_clone,
    tmp_path,
):
    """Agent that always calls a tool — loop must stop at MAX_CALLS."""
    from migration_agent.agent_loop import run_agent

    client_instance = MockClient.return_value
    client_instance.chat.return_value = _make_mock_response(
        'TOOL: {"tool":"list_dir","path":"."}'
    )

    vr = MagicMock()
    vr.r1_build = False
    vr.r2_bytecode = False
    vr.maven_tail = ""
    mock_verify.return_value = vr
    mock_snap.return_value = MagicMock()
    mock_tamper.return_value = MagicMock(tampered=False, violations=[])
    mock_maximal.return_value = MagicMock(passed=False, detail="")
    mock_load_idx.return_value = {}

    with patch("migration_agent.agent_loop._dispatch_tool",
               return_value={"ok": True, "output": "pom.xml", "error": None}):
        ar = run_agent(
            "owner/repo", "abc123", "system prompt",
            track="T2",
            trajectory_dir=tmp_path / "traj",
            api_key="fake-key",
        )

    assert ar.calls_used == MAX_CALLS


@patch("migration_agent.agent_loop.clone_at_commit")
@patch("migration_agent.agent_loop.snapshot_tests")
@patch("migration_agent.agent_loop.verify")
@patch("migration_agent.agent_loop.check_tamper")
@patch("migration_agent.agent_loop.check_maximal_effective")
@patch("migration_agent.agent_loop.load_version_index")
@patch("migration_agent.agent_loop.GeminiClient")
def test_run_agent_skips_completed_trajectory(
    MockClient, mock_load_idx, mock_maximal, mock_tamper,
    mock_verify, mock_snap, mock_clone,
    tmp_path,
):
    """If trajectory already has a terminal record, skip without calling Gemini."""
    from migration_agent.agent_loop import run_agent

    traj_dir = tmp_path / "traj"
    traj_dir.mkdir()
    traj_path = traj_dir / "owner__repo.jsonl"
    terminal = {
        "role": "terminal", "turn": 3, "minimal": True, "maximal": False,
        "r1": True, "r2": True, "tampered": False, "r5_maximal": False,
        "calls_used": 3, "total_prompt_tokens": 100,
        "total_completion_tokens": 50, "seconds": 30.0, "error": None,
        "ts": "2026-09-14T00:00:00Z",
    }
    traj_path.write_text(json.dumps(terminal) + "\n")

    ar = run_agent(
        "owner/repo", "abc123", "system prompt",
        track="T2",
        trajectory_dir=traj_dir,
        api_key="fake-key",
    )

    assert ar.skipped is True
    assert ar.minimal is True
    assert ar.calls_used == 3
    MockClient.return_value.chat.assert_not_called()


@patch("migration_agent.agent_loop.clone_at_commit")
@patch("migration_agent.agent_loop.snapshot_tests")
@patch("migration_agent.agent_loop.verify")
@patch("migration_agent.agent_loop.check_tamper")
@patch("migration_agent.agent_loop.check_maximal_effective")
@patch("migration_agent.agent_loop.load_version_index")
@patch("migration_agent.agent_loop.GeminiClient")
def test_run_agent_trajectory_written(
    MockClient, mock_load_idx, mock_maximal, mock_tamper,
    mock_verify, mock_snap, mock_clone,
    tmp_path,
):
    """Trajectory file must exist after run_agent completes."""
    from migration_agent.agent_loop import run_agent

    client_instance = MockClient.return_value
    client_instance.chat.return_value = _make_mock_response("DONE")

    vr = MagicMock()
    vr.r1_build = False
    vr.r2_bytecode = False
    vr.maven_tail = ""
    mock_verify.return_value = vr
    mock_snap.return_value = MagicMock()
    mock_tamper.return_value = MagicMock(tampered=False)
    mock_maximal.return_value = MagicMock(passed=False, detail="")
    mock_load_idx.return_value = {}

    traj_dir = tmp_path / "traj"
    run_agent(
        "owner/repo", "abc123", "sys",
        track="T2",
        trajectory_dir=traj_dir,
        api_key="fake-key",
    )

    traj_path = traj_dir / "owner__repo.jsonl"
    assert traj_path.exists()
    records = _load_traj(traj_path)
    # Must end with a terminal record.
    assert records[-1]["role"] == "terminal"
