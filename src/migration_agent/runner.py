"""Clone a benchmark repo at its base commit and run it in the sandbox.

The cloned repo's git history is deleted and replaced with a single fresh
commit. This is deliberate, not cleanup: an agent given the real history can
find the actual upstream fix commit for later Java versions and copy it
instead of solving the migration, which Cursor measured at 9% of SWE-bench
Pro solves (retrieval, not derivation).
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKDIR = REPO_ROOT / "workdir"
IMAGE = "migration-agent-base:latest"
M2_VOLUME = "migration-agent-m2"


def _force_remove_readonly(func, path, _exc_info) -> None:
    """Git's pack files are read-only on Windows; clear that before deleting."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.platform == "win32":
        # shutil.rmtree uses Win32 APIs that fail on paths > 260 chars even
        # when core.longpaths=true (that flag only affects git, not Python).
        # Maven target/ trees routinely exceed this limit. cmd rmdir handles it.
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(path)], check=True)
    else:
        shutil.rmtree(path, onerror=_force_remove_readonly)


def clone_at_commit(repo: str, base_commit: str, dest: Path) -> None:
    """Clone `repo` (owner/name) at `base_commit`, then collapse to one commit."""
    if dest.exists():
        _rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        ["git", "clone", f"https://github.com/{repo}.git", str(dest)],
        check=True,
    )
    subprocess.run(["git", "-C", str(dest), "checkout", base_commit], check=True)

    _rmtree(dest / ".git")
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "-c", "user.email=agent@local", "-c", "user.name=agent"]
        + ["add", "-A"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(dest), "-c", "user.email=agent@local", "-c", "user.name=agent"]
        + ["commit", "-q", "-m", "base commit: fresh snapshot, no upstream history"],
        check=True,
    )


def run_maven_verify(repo_dir: Path, java_version: int = 8) -> subprocess.CompletedProcess:
    """Run `mvn clean verify` for `repo_dir` inside the sandbox container."""
    return run_maven_goals(repo_dir, "clean verify", java_version)


def run_dependency_tree(repo_dir: Path, java_version: int = 17) -> subprocess.CompletedProcess:
    """Run `mvn dependency:tree` (resolved versions, used by the r5 check)."""
    return run_maven_goals(repo_dir, "dependency:tree", java_version)


def run_maven_goals(
    repo_dir: Path, goals: str, java_version: int = 8
) -> subprocess.CompletedProcess:
    """Run `mvn -B <goals>` for `repo_dir` inside the sandbox container."""
    switch = "" if java_version == 8 else f". use-java.sh {java_version} && "
    command = f"{switch}cd /workspace && mvn -B {goals}"
    # 600 s hard wall — Maven builds shouldn't take more than 10 min.
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{repo_dir.resolve()}:/workspace",
            "-v",
            f"{M2_VOLUME}:/root/.m2",
            IMAGE,
            "bash",
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--java", type=int, default=8, choices=[8, 17])
    args = parser.parse_args()

    dest = WORKDIR / args.repo.replace("/", "__")
    print(f"cloning {args.repo}@{args.base_commit[:12]} into {dest}")
    clone_at_commit(args.repo, args.base_commit, dest)

    print(f"running mvn clean verify under Java {args.java}")
    result = run_maven_verify(dest, args.java)
    print(result.stdout[-4000:])
    print(result.stderr[-2000:])
    print(f"exit code: {result.returncode}")


if __name__ == "__main__":
    main()
