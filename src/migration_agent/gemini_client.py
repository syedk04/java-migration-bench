"""S13: Gemini 2.5 Flash client with rate limiting and full request logging.

Wraps the Google Generative AI REST API (free tier via Google AI Studio key).
Rate limits: 14 RPM / 1400 RPD for Gemini 2.5 Flash on the free tier.

Features:
- Token-bucket RPM throttle (14 RPM = one request every ~4.3 s on average)
- Daily request counter with date-rollover reset (1400 RPD ceiling)
- 429 / 503 exponential backoff with jitter (caps at 60 s)
- JSONL request log: every call writes one line with timestamp, model,
  prompt_tokens, completion_tokens, latency_ms, and truncated content
- Raises GeminiRateLimitError when the daily ceiling is hit so the caller
  can stop gracefully rather than burning the quota on retries

Usage:
    from migration_agent.gemini_client import GeminiClient, GeminiMessage

    client = GeminiClient(api_key="...", log_path=Path("workdir/_logs/gemini.jsonl"))
    response = client.chat([
        GeminiMessage(role="user", content="Hello, Gemini!"),
    ])
    print(response.text)
    print(f"tokens: {response.prompt_tokens} in / {response.completion_tokens} out")
"""

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class GeminiMessage:
    role: str           # "user" or "model"
    content: str


@dataclass
class GeminiResponse:
    text: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw: dict = field(repr=False, default_factory=dict)


class GeminiError(RuntimeError):
    """Non-retriable API error."""


class GeminiRateLimitError(GeminiError):
    """Daily request quota (1400 RPD) exhausted."""


# ---------------------------------------------------------------------------
# Internal defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "gemini-3.6-flash"
_DEFAULT_RPM = 14
_DEFAULT_RPD = 1400
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_MAX_BACKOFF_S = 60.0
_INITIAL_BACKOFF_S = 2.0
_MAX_RETRIES = 6

# Module-level last-call timestamp — shared across all GeminiClient instances
# so that the inter-repo gap (new client, reset _last_call_ts) doesn't cause
# burst calls that exceed the RPM limit.
_global_last_call_ts: float = 0.0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GeminiClient:
    """Thread-unsafe single-process Gemini client with rate limiting."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        rpm: int = _DEFAULT_RPM,
        rpd: int = _DEFAULT_RPD,
        temperature: float = 0.0,
        log_path: Path | None = None,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY") or ""
        if not self._api_key:
            raise ValueError(
                "No Gemini API key. Set GEMINI_API_KEY env var or pass api_key=."
            )
        # Allow GEMINI_MODEL env var override (used by the Flash-Lite arm).
        self._model = os.environ.get("GEMINI_MODEL", model)
        self._temperature = temperature
        self._log_path = log_path

        # Rate limiting state.
        self._rpm = rpm
        self._rpd = rpd
        self._min_interval_s = 60.0 / rpm  # seconds between requests

        # Daily counter — resets when the date changes.
        self._today: date = date.today()
        self._requests_today: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[GeminiMessage],
        *,
        system: str | None = None,
        max_output_tokens: int = 8192,
    ) -> GeminiResponse:
        """Send a conversation turn and return the model reply.

        Applies RPM throttling and 429 backoff transparently.
        Raises GeminiRateLimitError if the daily cap is exceeded.
        """
        self._check_daily_quota()
        self._throttle_rpm()

        payload = self._build_payload(messages, system, max_output_tokens)
        url = (
            f"{_BASE_URL}/{self._model}:generateContent"
            f"?key={self._api_key}"
        )

        start = time.monotonic()
        raw = self._post_with_retry(url, payload)
        latency_ms = round((time.monotonic() - start) * 1000)

        response = self._parse_response(raw)
        self._record_call(messages, response, latency_ms)
        self._requests_today += 1
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_daily_quota(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._requests_today = 0
        if self._requests_today >= self._rpd:
            raise GeminiRateLimitError(
                f"Daily request quota exhausted ({self._rpd} RPD). "
                f"Resets at midnight UTC."
            )

    def _throttle_rpm(self) -> None:
        """Sleep if we are calling faster than the RPM limit.

        Uses a module-level timestamp so the limit is respected across
        multiple GeminiClient instances (one per repo in a batch run).
        """
        global _global_last_call_ts
        elapsed = time.monotonic() - _global_last_call_ts
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        _global_last_call_ts = time.monotonic()

    def _build_payload(
        self,
        messages: list[GeminiMessage],
        system: str | None,
        max_output_tokens: int,
    ) -> dict:
        contents: list[dict] = []
        for m in messages:
            role = "model" if m.role == "assistant" else m.role
            contents.append({"role": role, "parts": [{"text": m.content}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": self._temperature,
                "maxOutputTokens": max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    def _post_with_retry(self, url: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        backoff = _INITIAL_BACKOFF_S
        for attempt in range(_MAX_RETRIES + 1):
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 503) and attempt < _MAX_RETRIES:
                    jitter = random.uniform(0, backoff * 0.25)
                    wait = min(backoff + jitter, _MAX_BACKOFF_S)
                    time.sleep(wait)
                    backoff = min(backoff * 2, _MAX_BACKOFF_S)
                    continue
                body_bytes = exc.read() if exc.fp else b""
                raise GeminiError(
                    f"HTTP {exc.code}: {body_bytes[:500].decode(errors='replace')}"
                ) from exc
            except urllib.error.URLError as exc:
                raise GeminiError(f"Network error: {exc.reason}") from exc
        raise GeminiError(f"Exhausted {_MAX_RETRIES} retries on {url}")

    def _parse_response(self, raw: dict) -> GeminiResponse:
        try:
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise GeminiError(
                f"Unexpected response shape: {json.dumps(raw)[:400]}"
            ) from exc
        usage = raw.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)
        return GeminiResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            raw=raw,
        )

    def _record_call(
        self,
        messages: list[GeminiMessage],
        response: GeminiResponse,
        latency_ms: int,
    ) -> None:
        if not self._log_path:
            return
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": self._model,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "latency_ms": latency_ms,
            # Truncate to keep logs manageable — full content lives in
            # the per-repo trajectory JSONL files.
            "last_user_msg": messages[-1].content[:300] if messages else "",
            "response_snippet": response.text[:300],
        }
        with self._log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
