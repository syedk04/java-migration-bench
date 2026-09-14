"""Verifier v1: r1 (build passes) + r2 (bytecode major version == 61).

r1: mvn clean verify exits 0 under Java 17.
r2: sampled production .class files have major version 61 (Java 17).

These are the first two of the five paper criteria (Section 4):
  r1  mvn clean verify green
  r2  compiled bytecode major version 61
  r3  test-method AST invariance              (S7 tamper gate)
  r4  non-decreasing test count              (S7 tamper gate)
  r5  every dependency at latest major version (S9 maximal check)
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from migration_agent.runner import WORKDIR, clone_at_commit, run_maven_verify

_JAVA17_MAJOR = 61
_CLASS_SAMPLE_SIZE = 20  # files to inspect; enough to catch a partial migration


@dataclass
class VerificationResult:
    r1_build: bool      # mvn clean verify exited 0
    r2_bytecode: bool   # sampled production .class files are major version 61
    exit_code: int
    r2_detail: str      # human-readable: version found or skip/error reason
    maven_tail: str     # last 2000 chars of combined stdout+stderr


def _class_files(repo_dir: Path) -> list[Path]:
    """Return production .class paths (target/classes, not test-classes)."""
    return [
        p for p in repo_dir.rglob("*.class")
        if "target" in p.parts
        and "classes" in p.parts
        and "test-classes" not in p.parts
    ]


def _read_major_version(path: Path) -> int:
    """Return the JVM major version from a .class file header."""
    # On Windows, use the \\?\ prefix so open() handles paths > 260 chars.
    safe = ("\\\\?\\" + str(path.resolve())) if sys.platform == "win32" else str(path)
    with open(safe, "rb") as f:
        header = f.read(8)
    if len(header) < 8 or header[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError(f"invalid class magic in {path.name}")
    return int.from_bytes(header[6:8], "big")


def check_bytecode_version(repo_dir: Path, expected: int = _JAVA17_MAJOR) -> tuple[bool, str]:
    """Sample production .class files and confirm they are all at `expected` major version."""
    files = _class_files(repo_dir)
    if not files:
        return False, "no production .class files found under target/classes"

    sample = files[:_CLASS_SAMPLE_SIZE]
    wrong: list[str] = []
    for f in sample:
        try:
            v = _read_major_version(f)
            if v != expected:
                wrong.append(f"{f.name}: version {v}")
        except ValueError as exc:
            wrong.append(str(exc))

    if wrong:
        return False, f"{wrong[0]} (checked {len(sample)} files, {len(wrong)} wrong)"
    return True, f"version {expected} on all {len(sample)}/{len(files)} sampled files"


def verify(repo_dir: Path) -> VerificationResult:
    """Run r1+r2 checks against a (potentially migrated) repo directory."""
    result = run_maven_verify(repo_dir, java_version=17)
    r1 = result.returncode == 0
    maven_tail = (result.stdout + result.stderr)[-2000:]

    if r1:
        r2_ok, r2_detail = check_bytecode_version(repo_dir)
    else:
        r2_ok, r2_detail = False, "skipped (r1 build failed)"

    return VerificationResult(
        r1_build=r1,
        r2_bytecode=r2_ok,
        exit_code=result.returncode,
        r2_detail=r2_detail,
        maven_tail=maven_tail,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run r1+r2 verification on a repo.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--base-commit", required=True)
    args = parser.parse_args()

    dest = WORKDIR / args.repo.replace("/", "__")
    print(f"cloning {args.repo}@{args.base_commit[:12]}")
    clone_at_commit(args.repo, args.base_commit, dest)

    print("running verifier (Java 17) ...")
    vr = verify(dest)
    print(f"r1 (build):    {'PASS' if vr.r1_build else 'FAIL'} (exit {vr.exit_code})")
    print(f"r2 (bytecode): {'PASS' if vr.r2_bytecode else 'FAIL'} — {vr.r2_detail}")
    if not vr.r1_build:
        print("\n--- maven tail ---")
        print(vr.maven_tail[-1000:])


if __name__ == "__main__":
    main()
