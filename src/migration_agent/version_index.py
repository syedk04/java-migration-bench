"""S20: Dependency version index — query Maven Central for latest major versions.

Scans every pom.xml in the 50 reporting repos, collects unique
(groupId, artifactId) pairs, queries Maven Central REST API for the latest
version of each, and writes the result to `data/version_index.json`.

The snapshot is dated so consumers can see when it was taken. The S9
maximal checker (maximal.py) loads this file via `load_version_index()`.

CLI:
    uv run python -m migration_agent.version_index          # build full index
    uv run python -m migration_agent.version_index --dry-run  # just list artifacts
    uv run python -m migration_agent.version_index --resume   # skip already-queried

Rate limiting: 1 request per second (Maven Central asks for politeness;
free tier, no key required). With ~200-400 unique artifacts this takes
5–10 minutes.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKDIR = REPO_ROOT / "workdir"
MANIFEST_DIR = REPO_ROOT / "manifests"
DATA_DIR = REPO_ROOT / "data"

_POM_NS = "http://maven.apache.org/POM/4.0.0"
_NS = {"m": _POM_NS}

# Minimum request interval (seconds) — be polite to Maven Central.
_MIN_INTERVAL_S = 1.0

# Maven Central search API.
_SEARCH_URL = "https://search.maven.org/solrsearch/select"

# Regex to detect property placeholder versions like ${spring.version}.
_PLACEHOLDER_RE = re.compile(r"\$\{")


# ---------------------------------------------------------------------------
# POM parsing
# ---------------------------------------------------------------------------

def _find_text(el, tag: str) -> str | None:
    """Find direct child tag in both namespaced and bare form."""
    t = el.find(f"m:{tag}", _NS)
    if t is not None and t.text:
        return t.text.strip()
    t = el.find(tag)
    if t is not None and t.text:
        return t.text.strip()
    return None


def collect_artifacts(pom_path: Path) -> set[tuple[str, str]]:
    """Parse *pom_path* and return set of (groupId, artifactId) for all deps.

    Skips:
    - Dependencies without an explicit version (inherited from parent BOM)
    - Versions that are property placeholders (${...})
    - groupId/artifactId values that are themselves placeholders
    """
    try:
        tree = ET.parse(pom_path)
    except ET.ParseError:
        return set()

    root = tree.getroot()
    result: set[tuple[str, str]] = set()

    for dep in root.iter():
        local = dep.tag.split("}")[-1] if "}" in dep.tag else dep.tag
        if local != "dependency":
            continue
        g = _find_text(dep, "groupId")
        a = _find_text(dep, "artifactId")
        if not g or not a:
            continue
        if _PLACEHOLDER_RE.search(g) or _PLACEHOLDER_RE.search(a):
            continue
        result.add((g, a))

    return result


def collect_all_artifacts(manifest_name: str = "reporting_50.json") -> set[tuple[str, str]]:
    """Collect all (groupId, artifactId) pairs from all repos in the manifest."""
    manifest = json.loads((MANIFEST_DIR / manifest_name).read_text())
    all_artifacts: set[tuple[str, str]] = set()
    for entry in manifest:
        repo = entry["repo"]
        safe = repo.replace("/", "__")
        repo_dir = WORKDIR / safe
        if not repo_dir.is_dir():
            continue
        for pom in repo_dir.rglob("pom.xml"):
            all_artifacts |= collect_artifacts(pom)
    return all_artifacts


# ---------------------------------------------------------------------------
# Maven Central query
# ---------------------------------------------------------------------------

def query_latest_version(group_id: str, artifact_id: str) -> str | None:
    """Query Maven Central for the latest released version.

    Returns the version string or None if not found / error.
    """
    params = urllib.parse.urlencode({
        "q": f'g:"{group_id}" AND a:"{artifact_id}"',
        "rows": "1",
        "wt": "json",
    })
    url = f"{_SEARCH_URL}?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "migration-bench/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        docs = data.get("response", {}).get("docs", [])
        if docs:
            return docs[0].get("latestVersion") or docs[0].get("v")
        return None
    except (urllib.error.URLError, json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Main build logic
# ---------------------------------------------------------------------------

def build_index(
    manifest_name: str = "reporting_50.json",
    *,
    dry_run: bool = False,
    resume: bool = False,
) -> dict[str, str]:
    """Build the full version index.

    Returns dict mapping "groupId:artifactId" → latest_version.
    """
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / "version_index.json"

    # Load existing entries if resuming.
    existing: dict = {}
    if resume and out_path.exists():
        try:
            saved = json.loads(out_path.read_text())
            existing = saved.get("index", {})
            print(f"Resuming: {len(existing)} entries already saved.")
        except Exception:  # noqa: BLE001
            pass

    artifacts = collect_all_artifacts(manifest_name)
    print(f"Unique (groupId, artifactId) pairs: {len(artifacts)}")

    if dry_run:
        for g, a in sorted(artifacts):
            print(f"  {g}:{a}")
        return {}

    index: dict[str, str] = dict(existing)
    last_call = 0.0
    queried = 0
    skipped = 0
    failed = 0

    for g, a in sorted(artifacts):
        key = f"{g}:{a}"
        if key in index:
            skipped += 1
            continue

        # Rate limit.
        elapsed = time.monotonic() - last_call
        if elapsed < _MIN_INTERVAL_S:
            time.sleep(_MIN_INTERVAL_S - elapsed)
        last_call = time.monotonic()

        version = query_latest_version(g, a)
        if version:
            index[key] = version
            queried += 1
            print(f"  {key} = {version}")
        else:
            failed += 1
            print(f"  {key} = NOT FOUND")

        # Flush to disk every 20 queries so a crash loses little work.
        if (queried + failed) % 20 == 0:
            _write_index(out_path, index, manifest_name)

    _write_index(out_path, index, manifest_name)
    print(
        f"\nDone. queried={queried} skipped={skipped} failed={failed} "
        f"total={len(index)} entries written to {out_path}"
    )
    return index


def _write_index(path: Path, index: dict[str, str], manifest_name: str) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest": manifest_name,
        "count": len(index),
        "index": index,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="S20: build Maven dependency version index.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print artifacts without querying Maven Central.")
    parser.add_argument("--resume", action="store_true",
                        help="Skip artifacts already saved in version_index.json.")
    parser.add_argument("--manifest", default="reporting_50.json")
    args = parser.parse_args()

    build_index(args.manifest, dry_run=args.dry_run, resume=args.resume)
