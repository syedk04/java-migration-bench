"""Fetch migration-bench-java-selected and build fixed-seed manifests.

Uses HuggingFace's datasets-server REST API over stdlib urllib instead of
the `datasets` package, since we only need 300 small rows once.
"""

import json
import random
import urllib.request
from pathlib import Path

DATASET = "AmazonScience/migration-bench-java-selected"
SPLIT = "test"
PAGE_SIZE = 100
API_URL = "https://datasets-server.huggingface.co/rows"

MANIFEST_SEED = 42
DEV_SIZE = 20
REPORTING_SIZE = 50

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
MANIFEST_DIR = REPO_ROOT / "manifests"
DATASET_FILE = DATA_DIR / "migration_bench_java_selected.json"
DEV_MANIFEST_FILE = MANIFEST_DIR / "dev_20.json"
REPORTING_MANIFEST_FILE = MANIFEST_DIR / "reporting_50.json"


def fetch_dataset() -> list[dict]:
    """Pull all rows of the selected subset from the HF dataset-viewer API."""
    rows = []
    offset = 0
    while True:
        params = (
            f"dataset={DATASET}&config=default&split={SPLIT}"
            f"&offset={offset}&length={PAGE_SIZE}"
        )
        url = f"{API_URL}?{params}"
        with urllib.request.urlopen(url) as response:
            payload = json.load(response)
        page = [entry["row"] for entry in payload["rows"]]
        rows.extend(page)
        offset += PAGE_SIZE
        if offset >= payload["num_rows_total"]:
            break
    return rows


def build_manifest(rows: list[dict], size: int) -> list[dict]:
    """Deterministically sample `size` repos, fixed seed so the slice is stable."""
    shuffled = list(rows)
    random.Random(MANIFEST_SEED).shuffle(shuffled)
    sample = shuffled[:size]
    return [
        {
            "repo": r["repo"],
            "base_commit": r["base_commit"],
            "license": r["license"],
        }
        for r in sample
    ]


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    MANIFEST_DIR.mkdir(exist_ok=True)

    rows = fetch_dataset()
    if len(rows) != 300:
        raise SystemExit(f"expected 300 rows, got {len(rows)}")
    DATASET_FILE.write_text(json.dumps(rows, indent=2) + "\n")

    dev_manifest = build_manifest(rows, DEV_SIZE)
    reporting_manifest = build_manifest(rows, REPORTING_SIZE)
    DEV_MANIFEST_FILE.write_text(json.dumps(dev_manifest, indent=2) + "\n")
    REPORTING_MANIFEST_FILE.write_text(json.dumps(reporting_manifest, indent=2) + "\n")

    print(f"wrote {len(rows)} rows to {DATASET_FILE}")
    print(f"wrote {len(dev_manifest)}-repo dev manifest to {DEV_MANIFEST_FILE}")
    print(f"wrote {len(reporting_manifest)}-repo reporting manifest to {REPORTING_MANIFEST_FILE}")


if __name__ == "__main__":
    main()
