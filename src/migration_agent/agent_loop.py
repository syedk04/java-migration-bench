"""S15: Hand-rolled agent loop for Java 8 → 17 migration.

Drives the Gemini client through up to MAX_CALLS tool-use turns per repo.
Every turn is appended to a per-repo JSONL trajectory file so the run is
fully auditable and resumable — a repo whose trajectory file already ends
with a terminal record is skipped on restart.

Design choices (not up for re-litigation):
- MAX_CALLS = 40  (quota constraint vs paper's 80)
- temperature = 0
- No LangChain/embeddings/vector DB — hand-rolled tool dispatch
- Tool results are plain text back into the conversation; Gemini sees them
  as "user" turns (function-call style requires a paid tier feature we
  don't need)
- JSONL trajectory: one JSON object per line, fields:
    turn, role, content_snippet (first 500 chars), tool_call (dict|None),
    tool_result (dict|None), prompt_tokens, completion_tokens, latency_ms, ts

Public API:
    run_agent(repo, base_commit, system_prompt, *, track, trajectory_dir,
              log_path, api_key) -> AgentResult

    run_batch(manifest_path, system_prompt, *, track, trajectory_dir, ...)
      -> list[AgentResult]
"""

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from migration_agent.gemini_client import GeminiClient, GeminiMessage, GeminiRateLimitError
from migration_agent.maximal import check_maximal, load_version_index
from migration_agent.runner import WORKDIR, clone_at_commit
from migration_agent.tamper import check_tamper, snapshot_tests
from migration_agent.tools import (
    apply_patch,
    grep_files,
    list_dir,
    read_file,
    run_command,
    run_maven,
    write_file,
)
from migration_agent.verifier import verify

MAX_CALLS = 40

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"

# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

# Maps tool name → (function, required_param_names)
_TOOL_SPECS: dict[str, tuple] = {
    "read_file":    (read_file,    ["path"]),
    "write_file":   (write_file,   ["path", "content"]),
    "list_dir":     (list_dir,     []),          # path is optional
    "grep":         (grep_files,   ["pattern"]), # path, include optional
    "apply_patch":  (apply_patch,  ["patch"]),
    "run_maven":    (run_maven,    ["goal"]),
    "run_command":  (run_command,  ["command", "args"]),
}

_TOOL_DESCRIPTIONS = """\
Available tools (call with JSON on a line starting with TOOL:):

  read_file     {"tool":"read_file","path":"<rel-path>"}
  write_file    {"tool":"write_file","path":"<rel-path>","content":"<text>"}
  list_dir      {"tool":"list_dir","path":"<rel-path>"}
  grep          {"tool":"grep","pattern":"<regex>","path":".","include":"*.java"}
  apply_patch   {"tool":"apply_patch","patch":"<unified-diff>"}
  run_maven     {"tool":"run_maven","goal":"compile|test|verify"}
  run_command   {"tool":"run_command","command":"<cmd>","args":["..."]}

To finish, output: DONE

All paths are relative to the repository root.
apply_patch expects a standard unified diff (--- / +++ / @@ hunks).
run_maven runs under Java 17 inside the sandbox container.
"""


def _dispatch_tool(repo_dir: Path, call: dict) -> dict:
    """Execute one tool call dict, return result dict."""
    name = call.get("tool", "")
    if name not in _TOOL_SPECS:
        return {"ok": False, "output": "", "error": f"Unknown tool: {name!r}"}
    fn, _required = _TOOL_SPECS[name]

    if name == "read_file":
        return fn(repo_dir, call.get("path", ""))
    if name == "write_file":
        return fn(repo_dir, call.get("path", ""), call.get("content", ""))
    if name == "list_dir":
        return fn(repo_dir, call.get("path", "."))
    if name == "grep":
        return fn(
            repo_dir,
            call.get("pattern", ""),
            call.get("path", "."),
            case_insensitive=call.get("case_insensitive", False),
            include=call.get("include", "*.java"),
        )
    if name == "apply_patch":
        return fn(repo_dir, call.get("patch", ""))
    if name == "run_maven":
        return fn(repo_dir, call.get("goal", ""))
    if name == "run_command":
        args = call.get("args", [])
        if isinstance(args, str):
            args = args.split()
        return fn(repo_dir, call.get("command", ""), args)
    return {"ok": False, "output": "", "error": f"Unhandled tool: {name!r}"}


def _parse_tool_call(text: str) -> dict | None:
    """Extract the first TOOL: JSON line from model output, or None."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("TOOL:"):
            payload = stripped[len("TOOL:"):].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict) and "tool" in obj:
                    return obj
            except json.JSONDecodeError:
                pass
    return None


def _is_done(text: str) -> bool:
    return any(ln.strip() == "DONE" for ln in text.splitlines())


# ---------------------------------------------------------------------------
# Trajectory logging
# ---------------------------------------------------------------------------

def _traj_record(
    turn: int,
    role: str,
    content: str,
    tool_call: dict | None = None,
    tool_result: dict | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
) -> dict:
    return {
        "turn": turn,
        "role": role,
        "content_snippet": content[:500],
        "tool_call": tool_call,
        "tool_result": tool_result,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _append_traj(traj_path: Path, record: dict) -> None:
    with traj_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _load_traj(traj_path: Path) -> list[dict]:
    if not traj_path.exists():
        return []
    records = []
    for line in traj_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return records


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    repo: str
    base_commit: str
    track: str
    minimal: bool
    maximal: bool
    r1: bool = False
    r2: bool = False
    tampered: bool = False
    r5_maximal: bool = False
    calls_used: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None
    skipped: bool = False
    trajectory_path: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "base_commit": self.base_commit,
            "track": self.track,
            "minimal": self.minimal,
            "maximal": self.maximal,
            "r1": self.r1,
            "r2": self.r2,
            "tampered": self.tampered,
            "r5_maximal": self.r5_maximal,
            "calls_used": self.calls_used,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "seconds": self.seconds,
            "error": self.error,
            "skipped": self.skipped,
            "trajectory_path": self.trajectory_path,
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------

def run_agent(
    repo: str,
    base_commit: str,
    system_prompt: str,
    *,
    track: str,
    trajectory_dir: Path,
    gemini_log: Path | None = None,
    api_key: str | None = None,
) -> AgentResult:
    """Clone repo, run the agent loop, verify, return AgentResult.

    Resumes if a trajectory file already exists (reruns from scratch
    unless already terminal — a trajectory ending with role="terminal"
    means the verification ran and the result is committed).
    """
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    safe_name = repo.replace("/", "__")
    traj_path = trajectory_dir / f"{safe_name}.jsonl"

    # Check if already completed (terminal record present).
    existing = _load_traj(traj_path)
    if existing and existing[-1].get("role") == "terminal":
        last = existing[-1]
        return AgentResult(
            repo=repo,
            base_commit=base_commit,
            track=track,
            minimal=last.get("minimal", False),
            maximal=last.get("maximal", False),
            r1=last.get("r1", False),
            r2=last.get("r2", False),
            tampered=last.get("tampered", False),
            r5_maximal=last.get("r5_maximal", False),
            calls_used=last.get("calls_used", 0),
            total_prompt_tokens=last.get("total_prompt_tokens", 0),
            total_completion_tokens=last.get("total_completion_tokens", 0),
            seconds=last.get("seconds", 0.0),
            skipped=True,
            trajectory_path=str(traj_path),
        )

    dest = WORKDIR / safe_name
    start = time.monotonic()

    clone_at_commit(repo, base_commit, dest)
    snap = snapshot_tests(dest)

    client = GeminiClient(
        api_key=api_key,
        temperature=0.0,
        log_path=gemini_log,
    )

    messages: list[GeminiMessage] = []
    turn = 0
    total_prompt = 0
    total_completion = 0

    # Opening user message: task description + tool instructions.
    opening = (
        f"You are migrating a Java Maven repository from Java 8 to Java 17.\n"
        f"Repository root is /workspace (you operate via tools only).\n\n"
        f"{_TOOL_DESCRIPTIONS}\n"
        f"Your goal: make `mvn clean verify` pass under Java 17 with all "
        f"original tests still present and passing.\n"
        f"Start by listing the repository root."
    )
    messages.append(GeminiMessage(role="user", content=opening))
    _append_traj(traj_path, _traj_record(turn, "user", opening))

    calls_used = 0
    final_error: str | None = None

    for _ in range(MAX_CALLS):
        try:
            resp = client.chat(messages, system=system_prompt)
        except GeminiRateLimitError as exc:
            final_error = f"GeminiRateLimitError: {exc}"
            break
        except Exception as exc:  # noqa: BLE001
            final_error = f"API error: {exc}"
            break

        calls_used += 1
        total_prompt += resp.prompt_tokens
        total_completion += resp.completion_tokens
        messages.append(GeminiMessage(role="model", content=resp.text))

        tool_call = _parse_tool_call(resp.text)
        done = _is_done(resp.text)

        _append_traj(
            traj_path,
            _traj_record(
                turn, "model", resp.text,
                tool_call=tool_call,
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                latency_ms=0,
            ),
        )
        turn += 1

        if done or (not tool_call and calls_used >= 2):
            # Model declared done, or gave a non-tool response after at least
            # one turn — treat as finished.
            break

        if tool_call:
            result = _dispatch_tool(dest, tool_call)
            result_text = (
                f"Tool result (ok={result['ok']}):\n"
                + (result["output"] or "")
                + (f"\nError: {result['error']}" if result.get("error") else "")
            )
            messages.append(GeminiMessage(role="user", content=result_text))
            _append_traj(
                traj_path,
                _traj_record(turn, "tool_result", result_text, tool_result=result),
            )
            turn += 1

    # --- Verification phase ---
    vr = verify(dest)
    tampered = False
    r5_maximal = False
    if vr.r1_build and vr.r2_bytecode:
        tr = check_tamper(snap, dest)
        tampered = tr.tampered
        index = load_version_index()
        mr = check_maximal(dest, index)
        r5_maximal = mr.passed

    minimal = vr.r1_build and vr.r2_bytecode and not tampered
    maximal = minimal and r5_maximal
    elapsed = round(time.monotonic() - start, 1)

    terminal_rec = {
        "role": "terminal",
        "turn": turn,
        "minimal": minimal,
        "maximal": maximal,
        "r1": vr.r1_build,
        "r2": vr.r2_bytecode,
        "tampered": tampered,
        "r5_maximal": r5_maximal,
        "calls_used": calls_used,
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "seconds": elapsed,
        "error": final_error,
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _append_traj(traj_path, terminal_rec)

    return AgentResult(
        repo=repo,
        base_commit=base_commit,
        track=track,
        minimal=minimal,
        maximal=maximal,
        r1=vr.r1_build,
        r2=vr.r2_bytecode,
        tampered=tampered,
        r5_maximal=r5_maximal,
        calls_used=calls_used,
        total_prompt_tokens=total_prompt,
        total_completion_tokens=total_completion,
        seconds=elapsed,
        error=final_error,
        trajectory_path=str(traj_path),
    )


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(
    manifest_path: Path,
    system_prompt: str,
    *,
    track: str,
    out_path: Path,
    trajectory_dir: Path,
    gemini_log: Path | None = None,
    api_key: str | None = None,
) -> list[AgentResult]:
    """Run agent loop over all repos in manifest, writing results incrementally."""

    entries = json.loads(manifest_path.read_text())

    # Load already-completed results for resume.
    done: dict[str, dict] = {}
    if out_path.exists():
        try:
            for r in json.loads(out_path.read_text()):
                done[r["repo"]] = r
        except Exception:  # noqa: BLE001
            pass

    results: list[AgentResult] = []
    for d in done.values():
        ar = AgentResult(
            repo=d["repo"], base_commit=d["base_commit"], track=d["track"],
            minimal=d["minimal"], maximal=d["maximal"],
            calls_used=d.get("calls_used", 0), skipped=True,
        )
        results.append(ar)

    for i, entry in enumerate(entries, start=1):
        repo, base_commit = entry["repo"], entry["base_commit"]
        if repo in done:
            print(f"[{i}/{len(entries)}] {repo} SKIP", flush=True)
            continue
        print(f"[{i}/{len(entries)}] {repo}@{base_commit[:12]}", flush=True)
        t0 = time.monotonic()
        try:
            ar = run_agent(
                repo, base_commit, system_prompt,
                track=track,
                trajectory_dir=trajectory_dir,
                gemini_log=gemini_log,
                api_key=api_key,
            )
        except GeminiRateLimitError as exc:
            print(f"  QUOTA EXHAUSTED: {exc}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001
            ar = AgentResult(
                repo=repo, base_commit=base_commit, track=track,
                minimal=False, maximal=False,
                error=str(exc),
                seconds=round(time.monotonic() - t0, 1),
            )
        status = "PASS" if ar.minimal else "FAIL"
        print(
            f"  -> {status} in {ar.seconds}s  "
            f"calls={ar.calls_used}  "
            f"tokens={ar.total_prompt_tokens}in/{ar.total_completion_tokens}out",
            flush=True,
        )
        results.append(ar)
        # Incremental write — crash-safe.
        out_path.write_text(
            json.dumps([r.to_dict() for r in results], indent=2) + "\n"
        )

    return results
