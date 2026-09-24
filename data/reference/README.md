# Paper reference versions for r5 (maximal)

`dependency_version.json` is copied verbatim from MigrationBench:

- Source: https://github.com/amazon-science/MigrationBench/blob/main/src/migration_bench/reference/dependency_version.json
- Upstream commit: `c42afb1172ab97cdfe5416835050a1f5bc6c0af3` (2025-05-15)
- License: Apache-2.0 (Amazon.com, Inc. or its affiliates)
- Fetched: 2026-09-23

The paper (Section 4.2) defines r5 as every dependency at the "stable and
latest major versions available on Maven Central as of November 2024",
listed for the 240 most frequent dependencies in the selected subset. Any
dependency not in this file is not checked, which matches the upstream
evaluator (`eval_utils.check_version`).

`data/version_index.json` (our own 2026 Maven Central crawl) is **not** the
r5 criterion. Its "latest" values include pre-releases and post-date the
paper's snapshot. It is only used to build T4's retrieval context.
