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

Nothing yet. First numbers land after the OpenRewrite calibration run
(zero LLM calls) - see the build log.

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
