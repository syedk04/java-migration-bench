"""S18: Failure taxonomy — categorize migration failures by root cause.

For T0/T1 (no agent trajectory): classifies by r1/r2 exit codes and the
error field. For T2/T3/T4 (agent trajectory): extracts Maven build output
from tool_result records and pattern-matches against known Java 8→17 failure
modes.

Failure categories (from the MigrationBench paper and common experience):
  CLONE_ERROR     — git clone / checkout failed (repo deleted, force-pushed)
  BUILD_INFRA     — build environment issue (Docker, Maven, network) not Java
  COMPILE_ERROR   — Java compilation failure (type errors, missing symbols)
  TEST_FAILURE    — compilation OK but tests fail at runtime
  BYTECODE_WRONG  — r1 passed but bytecode still not Java 17 (r2=False)
  TAMPERED        — test bodies modified (r3 gate)
  JAXB_MISSING    — javax.xml.bind removed in Java 17
  NASHORN_MISSING — Nashorn JS engine removed in Java 17
  JAVAX_JAKARTA   — javax.* vs jakarta.* namespace migration needed
  REFLECTION      — --add-opens / --add-exports needed at runtime
  SECURITY_MGR    — SecurityManager deprecated/removed
  DEP_CONFLICT    — version conflicts after bumping dependencies
  UNKNOWN         — doesn't match any pattern

CLI:
    uv run python -m migration_agent.failure_taxonomy --track T0
    uv run python -m migration_agent.failure_taxonomy --track T1
    uv run python -m migration_agent.failure_taxonomy --track T2
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
TRAJ_BASE = REPO_ROOT / "workdir" / "trajectories"

_RESULT_FILES = {
    "T0": "t0_reporting_50.json",
    "T1": "t1_reporting_50.json",
    "T2": "t2_reporting_50.json",
    "T3": "t3_reporting_50.json",
    "T4": "t4_reporting_50.json",
}


# ---------------------------------------------------------------------------
# Pattern-based classifiers — ordered from most-specific to least-specific
# ---------------------------------------------------------------------------

_PATTERNS: list[tuple[str, list[str]]] = [
    # Clone / environment failures
    ("CLONE_ERROR", [
        r"git.*clone.*error",
        r"No such file.*base_commit",
        r"Command.*git.*failed",
        r"Command.*git.*returned non-zero exit status",
        r"remote.*not found",
    ]),
    ("BUILD_INFRA", [
        r"docker.*error",
        r"Cannot connect to the Docker daemon",
        r"Maven.*network",
        r"Unable to find.*docker image",
    ]),
    # Java 17 specific removal issues
    ("JAXB_MISSING", [
        r"javax\.xml\.bind",
        r"package javax\.xml\.bind does not exist",
        r"JAXB",
    ]),
    ("NASHORN_MISSING", [
        r"nashorn",
        r"jdk\.nashorn",
        r"ScriptEngine.*not found",
    ]),
    ("JAVAX_JAKARTA", [
        r"javax\.(servlet|persistence|transaction|validation|ws|annotation)\.",
        r"jakarta\.(servlet|persistence|transaction|validation|ws|annotation)\.",
        r"package javax\.servlet does not exist",
    ]),
    ("REFLECTION", [
        r"--add-opens",
        r"--add-exports",
        r"InaccessibleObjectException",
        r"module.*does not.*open",
    ]),
    ("SECURITY_MGR", [
        r"SecurityManager",
        r"java\.lang\.SecurityManager",
    ]),
    # Generic compile / test failures
    ("DEP_CONFLICT", [
        r"version.*conflict",
        r"NoSuchMethodError",
        r"NoClassDefFoundError",
        r"ClassNotFoundException",
        r"MojoFailureException.*dependency",
    ]),
    ("COMPILE_ERROR", [
        r"COMPILATION ERROR",
        r"\d+ error[s]?$",
        r"cannot find symbol",
        r"package .* does not exist",
        r"incompatible types",
    ]),
    ("TEST_FAILURE", [
        r"Tests run:.*Failures: [1-9]",
        r"Tests run:.*Errors: [1-9]",
        r"BUILD FAILURE.*test",
        r"test.*failed",
    ]),
    ("BYTECODE_WRONG", []),  # handled by r2=True, r1=True logic
]

_COMPILED = [
    (label, [re.compile(p, re.IGNORECASE) for p in patterns])
    for label, patterns in _PATTERNS
]


def _classify_text(text: str) -> str:
    """Return the first matching category label, or UNKNOWN."""
    for label, regexes in _COMPILED:
        for rx in regexes:
            if rx.search(text):
                return label
    return "UNKNOWN"


def _classify_record(r: dict, maven_output: str = "") -> str:
    """Classify a single result record."""
    if r.get("minimal"):
        return "PASS"

    error = r.get("error", "") or ""

    # Clone/infrastructure failures first.
    if error:
        c = _classify_text(error)
        if c not in ("UNKNOWN",):
            return c

    # Bytecode-only failure (r1 passed, r2 failed).
    if r.get("r1") and not r.get("r2"):
        return "BYTECODE_WRONG"

    # Tamper gate failure.
    if r.get("tampered"):
        return "TAMPERED"

    # If we have Maven output from a trajectory, classify it.
    if maven_output:
        c = _classify_text(maven_output)
        if c != "UNKNOWN":
            return c

    # Fall back on error field.
    if error:
        return _classify_text(error)

    return "BUILD_FAIL_UNKNOWN"


def _load_maven_output_from_traj(traj_path: Path) -> str:
    """Extract Maven build output from a JSONL trajectory file."""
    if not traj_path.exists():
        return ""
    lines = traj_path.read_text(encoding="utf-8", errors="replace").splitlines()
    outputs = []
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        tr = rec.get("tool_result")
        if isinstance(tr, dict):
            out = tr.get("output", "")
            if out and ("BUILD" in out or "ERROR" in out or "Tests run" in out):
                outputs.append(out)
    return "\n".join(outputs)


def _traj_path(track: str, repo: str) -> Path:
    safe = repo.replace("/", "__")
    return TRAJ_BASE / track.lower() / f"{safe}.jsonl"


def build_taxonomy(track: str) -> dict[str, list[str]]:
    """Return a dict mapping category → list of repo names."""
    results_path = LOGS_DIR / _RESULT_FILES.get(track, "")
    if not results_path.exists():
        return {}

    records = json.loads(results_path.read_text(encoding="utf-8"))
    taxonomy: dict[str, list[str]] = {}

    for r in records:
        repo = r["repo"]
        maven_output = ""
        tp = _traj_path(track, repo)
        if tp.exists():
            maven_output = _load_maven_output_from_traj(tp)
        cat = _classify_record(r, maven_output)
        taxonomy.setdefault(cat, []).append(repo)

    return taxonomy


def print_taxonomy(track: str) -> None:
    taxonomy = build_taxonomy(track)
    if not taxonomy:
        print(f"No results for {track} in {LOGS_DIR}")
        return

    total = sum(len(v) for v in taxonomy.values())
    print(f"\nFailure taxonomy for {track} (n={total}):")
    print("-" * 50)
    for cat, repos in sorted(taxonomy.items(), key=lambda x: -len(x[1])):
        pct = 100 * len(repos) / total if total else 0
        print(f"  {cat:<22} {len(repos):>3}  ({pct:.0f}%)")
        if cat != "PASS":
            for repo in repos[:5]:
                print(f"      {repo}")
            if len(repos) > 5:
                print(f"      ... and {len(repos)-5} more")
    print("-" * 50)


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="S18: migration failure taxonomy.")
    parser.add_argument("--track", default=None,
                        choices=list(_RESULT_FILES.keys()),
                        help="Which track to analyze (default: all available).")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON instead of human-readable table.")
    args = parser.parse_args()

    tracks = [args.track] if args.track else list(_RESULT_FILES.keys())
    found = False
    for track in tracks:
        path = LOGS_DIR / _RESULT_FILES[track]
        if not path.exists():
            continue
        found = True
        if args.json:
            tax = build_taxonomy(track)
            print(json.dumps({"track": track, "taxonomy": {
                k: {"count": len(v), "repos": v} for k, v in tax.items()
            }}, indent=2))
        else:
            print_taxonomy(track)

    if not found:
        print("No result files found. Run a track first.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
