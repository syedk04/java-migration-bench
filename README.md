# java-migration-bench

Reproducing the [MigrationBench](https://arxiv.org/abs/2505.09569) Java 8 to
Java 17 migration ladder, on free-tier infrastructure only, with a harness
that tries to catch answers that look right but aren't.

Status: early build. Nothing to report yet. See the build log below for what
exists so far.

## What this is

MigrationBench (Amazon Science, Apache-2.0) scores LLM agents on migrating
real Maven repositories from Java 8 to Java 17. Two success bars matter:

- **minimal migration**: the repo builds and all its original tests pass on
  Java 17, and the compiled bytecode is actually major version 61.
- **maximal migration**: minimal, plus every dependency is bumped to its
  latest major version. This is the hard part. On the published `selected`
  subset (n=300, Claude 4.5 Sonnet, 80-call cutoff), maximal success drops
  from 71.67% (minimal) to 15.33% for a plain agent, and tops out at 53.33%
  with retrieval and an 80-call budget.

This project reruns that ladder under constraints: zero paid infrastructure,
a 40-call budget instead of 80, and a second verification pass that reads
each passing trajectory and asks whether the agent actually did the work or
just satisfied the checker.

## Constraints, stated up front

- Zero spend. Gemini 2.5 Flash / Flash-Lite via Google AI Studio's free
  tier, GitHub Actions on a public repo, local Docker.
- 40 LLM calls per repo, not the paper's 80. This is a quota constraint, not
  a design choice, and every number reported here should be read against it.
- A 50-repo reporting slice, fixed seed, manifest committed with commit
  hashes. At n=50 a Wilson 95% interval is about ±13 points around 50%. That
  is enough to tell 2% from 45%. It is not enough to tell 45% from 53% -
  worth noting that the paper's own 45.67% and 53.33% rows have overlapping
  95% intervals even at n=300.
- Google's free tier may use submitted requests to improve their models.
  Every repository in this benchmark is MIT or Apache-2.0 licensed public
  code, so nothing private is at stake, but it's disclosed here rather than
  left for someone to find.

## Results

<!-- RESULTS_TABLE_START -->
### Our results (n=50, Gemini 2.5 Flash, 40-call budget)

| track | n | minimal | 95% CI | maximal | 95% CI | avg calls |
|---|---|---|---|---|---|---|
| T0: compiler bump only | 50 | 18.0% | [9.8, 30.8] | 18.0% | [9.8, 30.8] | — |
| T1: OpenRewrite UpgradeToJava17 | 23 | 13.0% | [4.5, 32.1] | 13.0% | [4.5, 32.1] | — |

### Paper reference (n=300, Claude 4.5 Sonnet, 80-call budget)

| method | minimal | maximal | avg calls |
|---|---|---|---|
| OpenRewrite (paper, n=300) | 16.3% | 2.0% | — |
| Strands baseline (paper, n=300) | 71.7% | 15.3% | 33.68 |
| + prompt engineering (paper, n=300) | — | 45.7% | 49.22 |
| + PE + RAG (paper, n=300) | — | 53.3% | 59.22 |
| hybrid static+agent (paper, n=300) | — | 53.3% | 52.55 |

> **Note:** n=50 → Wilson 95% CI ≈ ±13 pp around 50%. Enough to distinguish 2% from 45%. Not enough to distinguish 45% from 53%.
> **Maximal check caveat:** r5 only inspects dependencies with an explicit `<version>` element in pom.xml. Dependencies managed through a parent POM or BOM import (common in Spring/Spring Boot projects) are not checked and pass vacuously. Repos with 0 explicit dep versions show '0 deps checked' in `maximal_detail`; their maximal=True is a vacuous pass, not a verified result.
<!-- RESULTS_TABLE_END -->

## Build log

- S1: repo skeleton.
- S2: dataset loader. Pulls `AmazonScience/migration-bench-java-selected` (300
  repos) from HuggingFace's dataset-viewer API and writes two fixed-seed
  manifests: `manifests/dev_20.json` (never reported, iteration only) and
  `manifests/reporting_50.json` (the slice actual numbers come from). The dev
  slice is a prefix of the reporting slice under the same seed, so extending
  the reporting slice later stays consistent. Both are committed alongside
  the full dataset snapshot in `data/`.
- S3: base Docker image (`docker/Dockerfile`). Ubuntu 22.04 with JDK 8 and
  JDK 17 both installed, Maven 3.9.6 pinned from the Apache archive (not
  whatever Ubuntu's apt happens to ship), and `use-java.sh` to switch the
  active JDK in a running container (`. use-java.sh 17`). Defaults to Java 8
  so a bare `mvn` invocation matches each repo's base state. One shared named
  volume (`migration-agent-m2`) mounts at `/root/.m2` and persists across
  container runs - built once in S3, warmed across the full slice in S5, and
  reused by every later run so dependencies aren't re-downloaded per repo.

  Dev note: on Windows with Git Bash, `docker run` with Unix-style paths
  (e.g. `-v name:/root/.m2`) needs `MSYS_NO_PATHCONV=1` set first, or Git
  Bash rewrites the container path into a Windows one and the mount fails
  silently with a "file not found" that has nothing to do with Docker.
- S4: clone-and-run (`src/migration_agent/runner.py`). Clones a repo at its
  `base_commit`, deletes `.git` and reinitializes as a single fresh commit
  (deliberate - agents that see real history can find the actual upstream
  Java 17 fix commit and copy it instead of migrating anything, which
  Cursor measured at 9% of SWE-bench Pro solves), then runs
  `mvn clean verify` under Java 8 in the sandbox container. Verified end to
  end against `fridujo/spring-automocker` from the dev slice: single-commit
  history confirmed, build green, exit code 0.
- S5: warm the shared `.m2` cache across all 50 reporting-slice repos.
  **43/50 green under Java 8.** `.m2` volume: 2.5 GB after the full run.

  The 7 failures break down as: 6 genuine Maven build failures on Java 8
  (the paper's F3 filter is supposed to exclude these but a few slip
  through), and 1 repo (`ProgrammerAnthony/SentinelC`) whose base commit
  no longer exists in the upstream repo - force-push or history rewrite
  after the dataset was cut. These 7 repos will be recorded as
  unverifiable in the results store and skipped in scoring (they contribute
  to the denominator as failures, same as the paper's own pipeline).

  One Windows-specific bug found and fixed: Python's `shutil.rmtree` fails
  with WinError 3 on Maven `target/` trees whose nested bytecode paths
  exceed Windows' 260-char limit, even with `git config core.longpaths
  true` (that flag only affects git, not Python's Win32 calls). Fixed in
  `runner.py` by using `cmd /c rmdir /s /q` on Windows. Without the fix,
  5 repos that are actually green would have been misreported as failures.
- S6–S9: verifier (r1 build + r2 bytecode-version check), tamper gate (r3
  test-body invariance + r4 non-decreasing count), JaCoCo coverage check,
  maximal dependency checker. All four are wired into every migration track.
- S10: T0 baseline — compiler bump only (sets `maven.compiler.source/target
  /release` to 17 in every pom.xml). **Result: 9/50 = 18.0%** minimal.
  These are repos that were already Java-17-compatible with just a compiler
  setting change. This is the floor for every later track.
- S11: T1 — OpenRewrite `UpgradeToJava17` recipe applied before the same
  verifier pipeline. Batch running; results pending.
- S12: results store (`results/`) + README table generator
  (`src/migration_agent/results.py`). Reads per-repo JSON logs, computes
  Wilson 95% CIs, writes committed summary JSON, regenerates the Results
  table above. Re-run after each batch completes.
- S13: Gemini 2.5 Flash client — stdlib urllib, 14 RPM token bucket, 1400
  RPD daily counter with date-rollover reset, 429/503 exponential backoff.
- S14: agent tool layer — read/write/list/grep/apply_patch/run_maven/
  run_command. Path-traversal guard, output truncation, Maven goal allowlist,
  shell metacharacter rejection.
- S15: hand-rolled agent loop — 40-call budget, JSONL trajectory per repo,
  terminal record written after verification, resumable on crash.
- S16 prep / S19: T2 naive runner + T3 engineered prompt (Java 8→17 playbook,
  maximal criterion, anti-test-disabling rule, JAXB/javax/Nashorn patterns).
- S17: network monitor — parse Maven download URLs, flag unapproved hosts.
- S18: failure taxonomy — pattern classifier for CLONE_ERROR, JAXB_MISSING,
  JAVAX_JAKARTA, COMPILE_ERROR, TEST_FAILURE, BYTECODE_WRONG, etc.
  T0: 9 PASS (18%), 37 BUILD_FAIL_UNKNOWN (74%), 2 BYTECODE_WRONG, 2 CLONE_ERROR.
- S20: dependency version index — 625 artifact→version entries from Maven
  Central REST API (snapshot 2026-09-14), committed to `data/version_index.json`.
- S21: T4 retrieval runner — per-repo version context injected into system
  prompt from version_index.json.
- S22: transcript auditor — GENUINE/RETRIEVED/GAMED classifier, reads JSONL
  trajectory + git diff blind to pass/fail outcome.
- S23: final report generator — Wilson CIs for all tracks, audited maximal
  section, paper reference table, writes `results/final_report.json`.
- S24: second model arm — Flash-Lite T3-lite and T4-lite runners via
  `GEMINI_MODEL` env var override.
- S25: GitHub Actions CI — ruff + mypy + pytest on every push/PR.
- S26: PR generator — renders migration diff as GitHub PR body, optionally
  opens real PRs via REST API (dry-run by default).
- S27: GitHub Pages static site — dark-theme HTML from `final_report.json`,
  served from `docs/index.html`.
