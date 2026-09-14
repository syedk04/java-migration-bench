"""S17: Network monitoring for agent runs.

Two complementary mechanisms:

1. **Maven download logging**: parses Maven stdout for "Downloading from <repo>"
   lines to see which artifact repositories Maven contacted. Purely passive —
   no firewall changes needed.

2. **Isolated network mode**: creates a Docker bridge network with `--internal`
   (no external egress). Runs Maven with that network. If Maven tries to
   reach the internet (e.g., a non-cached repo), the connection will time out
   and the failure is logged. Only use after the .m2 cache is fully warmed.

The agent tool layer (run_maven) is unmodified in the default path. Pass
`isolated=True` to run with the restricted network instead.

Usage in code:
    from migration_agent.network_monitor import (
        parse_download_urls,
        ensure_isolated_network,
        ISOLATED_NETWORK,
    )

CLI (check what a specific repo's Maven run contacts):
    uv run python -m migration_agent.network_monitor \\
        --repo owner/name --base-commit <sha>
"""

import json
import re
import subprocess
from pathlib import Path

from migration_agent.runner import IMAGE, M2_VOLUME, WORKDIR, clone_at_commit

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"

# Docker network used for isolated agent runs.
ISOLATED_NETWORK = "migration-agent-isolated"

# Pattern matching Maven's download log lines.
_DOWNLOAD_RE = re.compile(
    r"Downloading(?: from \S+)?:\s+(https?://\S+)"
)
_DOWNLOADED_RE = re.compile(
    r"Downloaded(?: from \S+)?:\s+(https?://\S+)"
)

# Known Maven Central / approved hosts.
_APPROVED_HOSTS = frozenset({
    "repo1.maven.org",
    "repo.maven.apache.org",
    "central.maven.org",
    "oss.sonatype.org",
    "s01.oss.sonatype.org",
    "plugins.gradle.org",        # OpenRewrite downloads from here sometimes
})


def parse_download_urls(maven_output: str) -> list[dict]:
    """Extract all download URLs from Maven console output.

    Returns list of {"url": str, "host": str, "approved": bool}.
    """
    seen: set[str] = set()
    results: list[dict] = []
    for m in _DOWNLOAD_RE.finditer(maven_output):
        url = m.group(1).strip()
        if url in seen:
            continue
        seen.add(url)
        host = url.split("/")[2] if "/" in url else url
        results.append({
            "url": url,
            "host": host,
            "approved": host in _APPROVED_HOSTS,
        })
    return results


def ensure_isolated_network() -> bool:
    """Create the isolated Docker bridge network if it doesn't exist.

    Returns True if the network is ready.
    """
    # Check if it already exists.
    inspect = subprocess.run(
        ["docker", "network", "inspect", ISOLATED_NETWORK],
        capture_output=True,
    )
    if inspect.returncode == 0:
        return True

    # Create it: internal = no external egress from containers.
    result = subprocess.run(
        [
            "docker", "network", "create",
            "--driver", "bridge",
            "--internal",          # no external egress
            "--opt", "com.docker.network.bridge.enable_icc=false",
            ISOLATED_NETWORK,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Warning: could not create isolated network: {result.stderr}")
        return False
    print(f"Created Docker network: {ISOLATED_NETWORK}")
    return True


def run_maven_monitored(
    repo_dir: Path,
    goal: str = "verify",
    isolated: bool = False,
) -> dict:
    """Run Maven and capture all download URLs from stdout.

    Returns:
        {
            "exit_code": int,
            "downloads": list of {url, host, approved},
            "unapproved": list of {url, host},
            "stdout": str (truncated),
        }
    """
    network_args: list[str] = []
    if isolated:
        if ensure_isolated_network():
            network_args = ["--network", ISOLATED_NETWORK]

    command = f". /use-java.sh 17 && cd /workspace && mvn -B clean {goal}"
    proc = subprocess.run(
        [
            "docker", "run", "--rm",
            *network_args,
            "-v", f"{repo_dir.resolve()}:/workspace",
            "-v", f"{M2_VOLUME}:/root/.m2",
            IMAGE, "bash", "-c", command,
        ],
        capture_output=True,
        text=True,
    )
    combined = proc.stdout + proc.stderr
    downloads = parse_download_urls(combined)
    unapproved = [d for d in downloads if not d["approved"]]
    return {
        "exit_code": proc.returncode,
        "downloads": downloads,
        "unapproved": unapproved,
        "stdout": combined[-3000:],  # last 3000 chars
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="S17: network monitor — log Maven download URLs."
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--isolated", action="store_true",
                        help="Run with --internal Docker network (blocks external egress).")
    parser.add_argument("--goal", default="verify")
    args = parser.parse_args()

    dest = WORKDIR / args.repo.replace("/", "__")
    print(f"Cloning {args.repo}@{args.base_commit[:12]}...")
    clone_at_commit(args.repo, args.base_commit, dest)

    print(f"Running mvn clean {args.goal} {'(isolated)' if args.isolated else ''}...")
    result = run_maven_monitored(dest, goal=args.goal, isolated=args.isolated)

    print(f"\nExit code: {result['exit_code']}")
    print(f"Downloads ({len(result['downloads'])} total):")
    for d in result["downloads"]:
        flag = "" if d["approved"] else " *** UNAPPROVED ***"
        print(f"  {d['host']}{flag}")

    if result["unapproved"]:
        print(f"\n*** {len(result['unapproved'])} unapproved hosts contacted ***")
        for d in result["unapproved"]:
            print(f"  {d['url']}")
    else:
        print("\nAll downloads from approved Maven Central hosts.")

    LOGS_DIR.mkdir(exist_ok=True)
    out = LOGS_DIR / f"network_monitor_{args.repo.replace('/', '__')}.json"
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nWritten to {out}")
    sys.exit(result["exit_code"])
