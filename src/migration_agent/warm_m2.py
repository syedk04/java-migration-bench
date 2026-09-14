"""Warm the shared .m2 volume by cloning and building every repo in a manifest.

One repo's network hiccup or genuine build flakiness shouldn't stop the rest,
so failures are logged, not raised.
"""

import argparse
import json
import time
from pathlib import Path

from migration_agent.runner import WORKDIR, clone_at_commit, run_maven_verify

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOG_DIR = REPO_ROOT / "workdir" / "_logs"


def warm(manifest_path: Path) -> list[dict]:
    entries = json.loads(manifest_path.read_text())
    results = []
    for i, entry in enumerate(entries, start=1):
        repo, base_commit = entry["repo"], entry["base_commit"]
        dest = WORKDIR / repo.replace("/", "__")
        print(f"[{i}/{len(entries)}] {repo}@{base_commit[:12]}")
        start = time.monotonic()
        record = {"repo": repo, "base_commit": base_commit}
        try:
            clone_at_commit(repo, base_commit, dest)
            result = run_maven_verify(dest, java_version=8)
            record["success"] = result.returncode == 0
            record["exit_code"] = result.returncode
            if not record["success"]:
                record["tail"] = (result.stdout + result.stderr)[-2000:]
        except Exception as exc:  # noqa: BLE001 - log and keep going
            record["success"] = False
            record["error"] = str(exc)
        record["seconds"] = round(time.monotonic() - start, 1)
        print(f"  -> {'green' if record['success'] else 'FAILED'} in {record['seconds']}s")
        results.append(record)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="reporting_50.json")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / args.manifest
    results = warm(manifest_path)

    out_path = LOG_DIR / f"warm_{args.manifest}"
    out_path.write_text(json.dumps(results, indent=2) + "\n")

    green = sum(1 for r in results if r["success"])
    print(f"\n{green}/{len(results)} green under Java 8")
    print(f"log written to {out_path}")


if __name__ == "__main__":
    main()
