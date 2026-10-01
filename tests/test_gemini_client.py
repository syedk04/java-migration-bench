"""Tests for S13: Gemini client — rate limiting, retry, parsing, logging.

All network calls are intercepted via monkeypatching urllib so no real API
key or internet access is required.
"""

import json
import os
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

import pytest

from migration_agent.gemini_client import (
    GeminiClient,
    GeminiError,
    GeminiMessage,
    GeminiRateLimitError,
    load_env_file,
    read_env_file,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_response(text: str = "hello", prompt_tokens: int = 10,
                 completion_tokens: int = 5) -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": completion_tokens,
            "totalTokenCount": prompt_tokens + completion_tokens,
        },
    }


def _make_client(tmp_path: Path, rpm: int = 14, rpd: int = 1400) -> GeminiClient:
    return GeminiClient(
        api_key="test-key",
        model="gemini-2.5-flash",
        rpm=rpm,
        rpd=rpd,
        log_path=tmp_path / "gemini.jsonl",
    )


class _FakeResponse:
    """Minimal mock for urllib response object."""
    def __init__(self, body: dict) -> None:
        self._data = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def test_parse_response_fields(tmp_path, monkeypatch):
    client = _make_client(tmp_path)
    raw = _ok_response("world", prompt_tokens=20, completion_tokens=8)

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(raw))

    resp = client.chat([GeminiMessage(role="user", content="hi")])

    assert resp.text == "world"
    assert resp.prompt_tokens == 20
    assert resp.completion_tokens == 8
    assert resp.total_tokens == 28


def test_malformed_response_raises(tmp_path, monkeypatch):
    client = _make_client(tmp_path)

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse({"bad": "shape"}))

    with pytest.raises(GeminiError, match="Unexpected response shape"):
        client.chat([GeminiMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# Daily quota
# ---------------------------------------------------------------------------

def test_daily_quota_raises(tmp_path, monkeypatch):
    client = _make_client(tmp_path, rpd=2)
    raw = _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(raw))

    client.chat([GeminiMessage(role="user", content="1")])
    client.chat([GeminiMessage(role="user", content="2")])

    with pytest.raises(GeminiRateLimitError):
        client.chat([GeminiMessage(role="user", content="3")])


def test_daily_counter_resets_on_new_day(tmp_path, monkeypatch):
    from datetime import date
    client = _make_client(tmp_path, rpd=1)
    raw = _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(raw))

    client.chat([GeminiMessage(role="user", content="1")])
    # Simulate day rollover.
    from datetime import timedelta
    future = date.today() + timedelta(days=1)
    monkeypatch.setattr("migration_agent.gemini_client.date", type(
        "FakeDate", (), {"today": staticmethod(lambda: future)}
    ))
    # Should succeed because the counter resets.
    client.chat([GeminiMessage(role="user", content="2")])


# ---------------------------------------------------------------------------
# 429 retry
# ---------------------------------------------------------------------------

def test_429_retries_then_succeeds(tmp_path, monkeypatch):
    client = _make_client(tmp_path, rpm=1400)  # no throttle delay
    raw = _ok_response("retry worked")
    attempts = {"n": 0}

    def fake_urlopen(req, **kw):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(
                url="", code=429, msg="Too Many Requests",
                hdrs=None, fp=BytesIO(b"rate limited"),  # type: ignore[arg-type]
            )
        return _FakeResponse(raw)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(time, "sleep", lambda s: None)  # skip actual waits

    resp = client.chat([GeminiMessage(role="user", content="hi")])
    assert resp.text == "retry worked"
    assert attempts["n"] == 3


def test_http_error_non_429_raises_immediately(tmp_path, monkeypatch):
    client = _make_client(tmp_path, rpm=1400)

    def fake_urlopen(req, **kw):
        raise urllib.error.HTTPError(
            url="", code=403, msg="Forbidden",
            hdrs=None, fp=BytesIO(b"forbidden"),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(GeminiError, match="HTTP 403"):
        client.chat([GeminiMessage(role="user", content="hi")])


# ---------------------------------------------------------------------------
# JSONL log
# ---------------------------------------------------------------------------

def test_log_file_written(tmp_path, monkeypatch):
    client = _make_client(tmp_path)
    raw = _ok_response("logged")

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(raw))

    client.chat([GeminiMessage(role="user", content="log this")])

    log_path = tmp_path / "gemini.jsonl"
    assert log_path.exists()
    entry = json.loads(log_path.read_text().strip())
    assert entry["prompt_tokens"] == 10
    assert entry["completion_tokens"] == 5
    assert "log this" in entry["last_user_msg"]


def test_no_log_when_path_is_none(monkeypatch):
    client = GeminiClient(api_key="test-key", log_path=None, rpm=1400)
    raw = _ok_response()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **kw: _FakeResponse(raw))

    # Should not raise even with no log path.
    resp = client.chat([GeminiMessage(role="user", content="hi")])
    assert resp.text == "hello"


# ---------------------------------------------------------------------------
# No API key
# ---------------------------------------------------------------------------

def test_no_api_key_raises(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="No Gemini API key"):
        GeminiClient(api_key=None)


def test_read_env_file_parses_quotes_comments_export_and_bom(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(
        b"\xef\xbb\xbfGEMINI_API_KEY=abc123\n"  # UTF-8 BOM, as Notepad writes it
        b"# comment\n"
        b"\n"
        b"export GEMINI_MODEL=\"gemini-flash-latest\"\n"
        b"OTHER='x=y'\n"
        b"no_equals_line\n"
    )
    assert read_env_file(env) == {
        "GEMINI_API_KEY": "abc123",
        "GEMINI_MODEL": "gemini-flash-latest",
        "OTHER": "x=y",
    }


def test_read_env_file_missing_returns_empty(tmp_path):
    assert read_env_file(tmp_path / "nope.env") == {}


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=from_file\nGEMINI_MODEL=m\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from_shell")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    load_env_file(env)
    assert os.environ["GEMINI_API_KEY"] == "from_shell"
    assert os.environ["GEMINI_MODEL"] == "m"
