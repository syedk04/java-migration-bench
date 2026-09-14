"""S14: Tool layer for the agent loop.

Provides a closed set of filesystem and Maven operations the LLM agent can
call. Every tool returns a plain dict so results can be round-tripped through
JSON in the trajectory log.

Design principles:
- No raw shell execution. The `run_command` tool is restricted to an
  explicit allowlist so an agent cannot, say, delete tests or push code.
- Long outputs are truncated to head + tail (configurable) so a 100 kB Maven
  stack trace doesn't blow the context window.
- `apply_patch` uses Python's `difflib`-based approach instead of shelling
  out to `patch`, which isn't reliably available on Windows.
- `run_maven` calls Docker via subprocess — same machinery as the verifier —
  and always runs under Java 17 (the target JDK for T2+).

Return dict schema for every tool:
    {
        "ok": bool,          # True if the operation succeeded
        "output": str,       # Truncated stdout/result text
        "error": str | None, # Error message if ok=False, else None
    }
"""

import re
import subprocess
from pathlib import Path

from migration_agent.runner import IMAGE, M2_VOLUME

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum lines to keep from head and tail of long output.
_HEAD_LINES = 40
_TAIL_LINES = 40

# Maximum file size to read (bytes) — refuse larger files.
_MAX_READ_BYTES = 256 * 1024  # 256 KB

# Allowlisted commands for run_command.  Values are the literal first token
# of the command that must match; arguments are passed through unchecked
# EXCEPT that shell metacharacters are forbidden (see _validate_args).
_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "java", "javac", "mvn",
    "ls", "find", "cat", "head", "tail",
    "grep", "sed", "diff",
    "git",   # only git diff / git status sub-commands (enforced below)
})

# git sub-commands that are permitted when command == "git".
_ALLOWED_GIT_SUBCOMMANDS: frozenset[str] = frozenset({"diff", "status", "log"})

# Shell metacharacters — reject any arg that contains these.
_SHELL_META_RE = re.compile(r'[;&|`$<>\\]')

# Maven goals the agent is allowed to invoke.
_ALLOWED_MAVEN_GOALS: frozenset[str] = frozenset({"compile", "test", "verify"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(text: str, head: int = _HEAD_LINES, tail: int = _TAIL_LINES) -> str:
    """Keep first *head* and last *tail* lines; insert an omission notice."""
    lines = text.splitlines()
    if len(lines) <= head + tail:
        return text
    omitted = len(lines) - head - tail
    kept = (
        lines[:head]
        + [f"... [{omitted} lines omitted] ..."]
        + lines[-tail:]
    )
    return "\n".join(kept)


def _result(ok: bool, output: str = "", error: str | None = None) -> dict:
    return {"ok": ok, "output": output, "error": error}


def _safe_path(repo_dir: Path, rel: str) -> Path | None:
    """Resolve *rel* inside *repo_dir*, returning None if it escapes the tree."""
    try:
        target = (repo_dir / rel).resolve()
        repo_dir.resolve()
        target.relative_to(repo_dir.resolve())
        return target
    except (ValueError, OSError):
        return None


def _validate_args(args: list[str]) -> str | None:
    """Return an error string if any arg contains shell metacharacters."""
    for arg in args:
        if _SHELL_META_RE.search(arg):
            return f"Argument contains forbidden shell metacharacter: {arg!r}"
    return None


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------

def read_file(repo_dir: Path, path: str) -> dict:
    """Read a text file relative to *repo_dir*."""
    target = _safe_path(repo_dir, path)
    if target is None:
        return _result(False, error=f"Path escapes repo root: {path!r}")
    if not target.exists():
        return _result(False, error=f"File not found: {path!r}")
    if not target.is_file():
        return _result(False, error=f"Not a file: {path!r}")
    size = target.stat().st_size
    if size > _MAX_READ_BYTES:
        return _result(False, error=(
            f"File too large ({size} bytes > {_MAX_READ_BYTES} limit): {path!r}"
        ))
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _result(False, error=str(exc))
    return _result(True, output=content)


def write_file(repo_dir: Path, path: str, content: str) -> dict:
    """Write *content* to a file relative to *repo_dir*."""
    target = _safe_path(repo_dir, path)
    if target is None:
        return _result(False, error=f"Path escapes repo root: {path!r}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return _result(False, error=str(exc))
    return _result(True, output=f"Written {path}")


def list_dir(repo_dir: Path, path: str = ".") -> dict:
    """List directory contents relative to *repo_dir*."""
    target = _safe_path(repo_dir, path)
    if target is None:
        return _result(False, error=f"Path escapes repo root: {path!r}")
    if not target.exists():
        return _result(False, error=f"Directory not found: {path!r}")
    if not target.is_dir():
        return _result(False, error=f"Not a directory: {path!r}")
    try:
        entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = [
            f"{'d' if e.is_dir() else 'f'}  {e.name}"
            for e in entries
        ]
    except OSError as exc:
        return _result(False, error=str(exc))
    return _result(True, output="\n".join(lines))


def grep_files(
    repo_dir: Path,
    pattern: str,
    path: str = ".",
    *,
    case_insensitive: bool = False,
    include: str = "*.java",
) -> dict:
    """Search for *pattern* in files under *path* (relative to repo_dir).

    Returns matching lines with relative file paths and line numbers.
    """
    target = _safe_path(repo_dir, path)
    if target is None:
        return _result(False, error=f"Path escapes repo root: {path!r}")
    if not target.exists():
        return _result(False, error=f"Path not found: {path!r}")

    try:
        flags = re.IGNORECASE if case_insensitive else 0
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        return _result(False, error=f"Invalid regex: {exc}")

    matches: list[str] = []
    search_root = target if target.is_dir() else target.parent
    try:
        for fpath in sorted(search_root.rglob(include or "*")):
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    rel = fpath.relative_to(repo_dir)
                    matches.append(f"{rel}:{lineno}: {line.rstrip()}")
    except OSError as exc:
        return _result(False, error=str(exc))

    if not matches:
        return _result(True, output="(no matches)")
    return _result(True, output=_truncate("\n".join(matches)))


def apply_patch(repo_dir: Path, patch_text: str) -> dict:
    """Apply a unified diff to files in *repo_dir*.

    The patch is expected to be in unified diff format (--- / +++ / @@ lines).
    Each file patch is applied independently; returns a summary of applied
    and failed files.
    """
    # Split patch into per-file sections on "--- " lines.
    file_patches: list[list[str]] = []
    current: list[str] = []
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("--- ") and current:
            file_patches.append(current)
            current = []
        current.append(line)
    if current:
        file_patches.append(current)

    applied: list[str] = []
    errors: list[str] = []

    for fp in file_patches:
        # Extract target filename from the +++ line.
        target_rel: str | None = None
        for line in fp:
            if line.startswith("+++ "):
                raw = line[4:].split("\t", 1)[0].strip()
                # Strip leading a/ b/ prefixes common in git diffs.
                for prefix in ("b/", "a/"):
                    if raw.startswith(prefix):
                        raw = raw[len(prefix):]
                        break
                target_rel = raw
                break

        if target_rel is None:
            errors.append("Could not determine target file from patch hunk")
            continue

        target = _safe_path(repo_dir, target_rel)
        if target is None:
            errors.append(f"Target path escapes repo root: {target_rel!r}")
            continue

        original = ""
        if target.is_file():
            try:
                original = target.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"{target_rel}: read error: {exc}")
                continue

        try:
            result = _apply_unified_hunks(original, fp)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{target_rel}: patch failed: {exc}")
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(result, encoding="utf-8")
            applied.append(target_rel)
        except OSError as exc:
            errors.append(f"{target_rel}: write error: {exc}")

    summary_parts = []
    if applied:
        summary_parts.append(f"Applied: {', '.join(applied)}")
    if errors:
        summary_parts.append(f"Errors: {'; '.join(errors)}")
    ok = bool(applied) and not errors
    return _result(ok, output="\n".join(summary_parts) or "Nothing applied",
                   error="; ".join(errors) if errors else None)


def _apply_unified_hunks(original: str, patch_lines: list[str]) -> str:
    """Apply unified-diff hunks to *original* text, return modified text.

    Processes hunks from bottom to top so earlier line numbers stay valid
    after each substitution.
    """
    orig_lines = original.splitlines(keepends=True)

    # Collect hunks: list of (orig_start_0based, orig_count, hunk_body_lines)
    hunks: list[tuple[int, int, list[str]]] = []
    i = 0
    while i < len(patch_lines):
        line = patch_lines[i]
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", line)
        if m:
            orig_start = int(m.group(1)) - 1  # 0-based
            orig_count = int(m.group(2)) if m.group(2) is not None else 1
            i += 1
            hunk_body: list[str] = []
            while i < len(patch_lines) and not patch_lines[i].startswith("@@"):
                if not patch_lines[i].startswith(("---", "+++")):
                    hunk_body.append(patch_lines[i])
                i += 1
            hunks.append((orig_start, orig_count, hunk_body))
        else:
            i += 1

    # Apply from bottom up.
    for orig_start, orig_count, hunk_body in reversed(hunks):
        new_lines: list[str] = []
        for hl in hunk_body:
            if hl.startswith("+"):
                new_lines.append(hl[1:])
            elif hl.startswith(" "):
                new_lines.append(hl[1:])
            # "-" lines are dropped.
        orig_lines[orig_start : orig_start + orig_count] = new_lines

    return "".join(orig_lines)


# ---------------------------------------------------------------------------
# Maven tool
# ---------------------------------------------------------------------------

def run_maven(repo_dir: Path, goal: str) -> dict:
    """Run `mvn clean <goal>` under Java 17 in the sandbox container.

    *goal* must be one of: compile, test, verify.
    """
    if goal not in _ALLOWED_MAVEN_GOALS:
        return _result(False, error=(
            f"Goal {goal!r} not allowed. "
            f"Permitted: {sorted(_ALLOWED_MAVEN_GOALS)}"
        ))
    command = f". /use-java.sh 17 && cd /workspace && mvn -B clean {goal}"
    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "-v", f"{repo_dir.resolve()}:/workspace",
                "-v", f"{M2_VOLUME}:/root/.m2",
                IMAGE, "bash", "-c", command,
            ],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min hard wall; T2/T3/T4 agent calls hit this limit
        )
    except subprocess.TimeoutExpired:
        return _result(False, error="Maven build timed out after 600 seconds")
    combined = proc.stdout + proc.stderr
    return _result(
        ok=proc.returncode == 0,
        output=_truncate(combined),
        error=None if proc.returncode == 0 else f"exit {proc.returncode}",
    )


# ---------------------------------------------------------------------------
# run_command (allowlisted shell commands)
# ---------------------------------------------------------------------------

def run_command(repo_dir: Path, command: str, args: list[str]) -> dict:
    """Run an allowlisted command in *repo_dir* on the host (not Docker).

    Permitted commands: java, javac, mvn, ls, find, cat, head, tail,
    grep, sed, diff, git (sub-commands: diff, status, log only).
    """
    if command not in _ALLOWED_COMMANDS:
        return _result(False, error=(
            f"Command {command!r} not in allowlist. "
            f"Permitted: {sorted(_ALLOWED_COMMANDS)}"
        ))
    if command == "git":
        if not args or args[0] not in _ALLOWED_GIT_SUBCOMMANDS:
            return _result(False, error=(
                f"git sub-command {args[0] if args else '(none)'!r} not allowed. "
                f"Permitted: {sorted(_ALLOWED_GIT_SUBCOMMANDS)}"
            ))

    err = _validate_args(args)
    if err:
        return _result(False, error=err)

    try:
        proc = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            cwd=str(repo_dir),
        )
    except FileNotFoundError:
        return _result(False, error=f"Command not found on PATH: {command!r}")
    combined = proc.stdout + proc.stderr
    return _result(
        ok=proc.returncode == 0,
        output=_truncate(combined),
        error=None if proc.returncode == 0 else f"exit {proc.returncode}",
    )
