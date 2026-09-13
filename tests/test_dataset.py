import json

from migration_agent.dataset import (
    DATASET_FILE,
    DEV_MANIFEST_FILE,
    REPORTING_MANIFEST_FILE,
)


def _load(path):
    return json.loads(path.read_text())


def test_dataset_has_300_rows():
    rows = _load(DATASET_FILE)
    assert len(rows) == 300
    assert {r["license"] for r in rows} <= {"MIT", "Apache-2.0"}


def test_manifest_sizes():
    assert len(_load(DEV_MANIFEST_FILE)) == 20
    assert len(_load(REPORTING_MANIFEST_FILE)) == 50


def test_manifest_entries_have_required_fields():
    for entry in _load(REPORTING_MANIFEST_FILE):
        assert entry["repo"]
        assert len(entry["base_commit"]) == 40
        assert entry["license"] in {"MIT", "Apache-2.0"}


def test_dev_slice_is_prefix_of_reporting_slice():
    dev = _load(DEV_MANIFEST_FILE)
    reporting = _load(REPORTING_MANIFEST_FILE)
    assert dev == reporting[: len(dev)]


def test_reporting_manifest_has_no_duplicate_repos():
    reporting = _load(REPORTING_MANIFEST_FILE)
    repos = [entry["repo"] for entry in reporting]
    assert len(repos) == len(set(repos))
