# Build progress and handoff notes

Internal working doc, not part of the public README. Written so another
agent (or me, in a future session) can pick this project up without
re-deriving context. Read this before touching anything.

## What this project is

Reproducing the [MigrationBench](https://arxiv.org/abs/2505.09569) Java 8 to
Java 17 migration ladder (Amazon Science, Apache-2.0) on zero-cost
infrastructure, with an evaluation harness designed to catch results that
look right but aren't (tamper gate, coverage check, blind transcript audit).
This is a portfolio piece — the number reported at the end has to be
defensible, not flattering.

Repo: https://github.com/syedk04/java-migration-bench (public)
Local path: `C:\Users\6ix4o\Documents\PersonalProjects\AI Repository Migration Agent`
GitHub account: syedk04

## Hard constraints (do not relitigate these)

- **Zero spend.** No paid API, compute, or storage. If a step needs money,
  stop and ask — don't assume the user will pay.
- **Small increments.** Every step: build it, run it on real data, commit,
  push. Never batch multiple steps into one commit. Main must build and
  pass tests at every single push.
- **Two hard gates — stop and show numbers, do not proceed on autopilot:**
  - **S11** (OpenRewrite calibration): must land near 16.33% minimal / 2.00%
    maximal on the reporting slice. If it doesn't, the harness is wrong and
    every later number is worthless.
  - **S16** (first real Gemini run, 5 repos): report calls/tokens per repo
    and project daily throughput before running the full 50.
- Stack is decided (Python 3.12 + uv + ruff + pytest, Gemini 2.5 Flash /
  Flash-Lite free tier, Docker locally + GitHub Actions for LLM-free tracks,
  hand-rolled agent loop, no LangChain/embeddings). Don't re-litigate unless
  something is actually broken. No new dependency without stating what it
  buys and what it costs.

## Verified against the source (2026-09-12)

The user supplied a results table before any code was written. It was
checked against the actual paper text (not a web-fetch summary — the first
automated fetch fabricated a plausible-looking but wrong table; caught by
cross-referencing the abstract's own numbers, then reading the real PDF).
The table is accurate. Selected subset, n=300, Claude-4.5-Sonnet, 80-call
cutoff:

| method | minimal | maximal | avg calls/repo |
|---|---|---|---|
| OpenRewrite (static) | 16.33% | 2.00% | - |
| Strands agent baseline | 71.67% | 15.33% | 33.68 |
| + prompt engineering | - | 45.67% | 49.22 |
| + PE + RAG | - | 53.33% | 59.22 |
| hybrid (static + agent) | - | 53.33% | 52.55 |

Definitions (paper Section 4): **minimal** = `mvn clean verify` green (r1) +
compiled bytecode major version 61 (r2) + test-method AST invariance, no
renamed/disabled/removed `@Test` methods (r3) + non-decreasing test count
(r4). **Maximal** = minimal + every dependency at its latest major version
per a frozen Nov-2024 Maven Central snapshot (r5). Call cutoff is 80 turns.

Also confirmed: Wilson 95% CI arithmetic checks out. At n=300 the 45.67%
and 53.33% rows have overlapping confidence intervals (~[40,51] vs
~[48,59]) — that gap is shaky even in the source. At n=50 (our reporting
slice), a Wilson interval around p=0.5 is about ±13.4 points. The paper's
own ablation (dropping `mvn verify` for `mvn compile`, i.e. skipping tests)
inflates minimal migration from 71.67% to 97.67% — good real-world
justification for the S7 tamper gate and S22 blind audit.

## Environment specifics for THIS machine

Windows 11, PowerShell + Git Bash both used. A few things that will bite a
fresh agent if not known:

1. **Docker Desktop was installed per-user**, not machine-wide, and its
   `docker.exe` is NOT on the default PATH in already-open shell sessions
   (PATH env var was updated by the installer, but existing shell processes
   don't pick it up until restarted). Location:
   `C:\Users\6ix4o\AppData\Local\Programs\DockerDesktop\resources\bin`
   A fresh shell session should have it on PATH automatically (confirmed
   added to the user-level `Path` env var). If `docker` isn't found, prepend
   that dir to PATH for the session rather than reinstalling anything.

2. **Git Bash mangles Unix-style paths passed to non-shell-aware commands.**
   Any `docker run` invoked directly from a Git Bash command line with
   Unix-style paths (e.g. `-v name:/root/.m2`, or a literal path argument
   like `/root/.m2/x` inside an inline `bash -c '...'`) needs
   `MSYS_NO_PATHCONV=1` set first, or Git Bash silently rewrites the
   container-side path into a Windows path and things fail confusingly.
   This does NOT affect Python's `subprocess` calls to `docker` — those
   don't go through Git Bash's arg-rewriting layer, which is why
   `runner.py` and `warm_m2.py` don't need this env var internally. Only
   matters for ad-hoc `docker run ...` typed directly into a Bash tool call.

3. **`git config --global core.longpaths true` is now set on this
   machine.** Needed because several benchmark repos have deeply nested
   Java package paths that exceed Windows' 260-char MAX_PATH, which made
   `git checkout` fail with "Filename too long" (hit on
   `blue-veery-gmbh/spring-rest-2-ts`). This is a global git config change,
   not scoped to this repo — standard, low-risk, commonly recommended for
   Windows dev machines, but flagging it since it's outside the project
   directory. If a fresh machine hits the same "Filename too long" error
   during cloning, set this again.

4. **`uv` was not preinstalled** on this machine; it was installed via the
   official `astral.sh/uv/install.ps1` script to
   `C:\Users\6ix4o\.local\bin`. Same PATH caveat as Docker — a fresh shell
   picks it up automatically, an already-open one may not.

5. **Python's stdout buffering hides progress when output is redirected to
   a file.** `warm_m2.py`'s `print()` progress lines (`[i/50] repo ...`,
   `-> green/FAILED`) get block-buffered and won't show up in a `tail` of
   the redirected log until the buffer flushes or the process exits, even
   though the underlying `git`/`mvn` subprocess output (inherited stdout)
   appears immediately. Don't mistake "no progress markers yet" for "stuck"
   — check `grep -c "Cloning into" <log>` against manifest size for a truer
   progress signal, or check the raw commit/log for git status.

6. Background shell commands run via the Bash tool's `run_in_background`
   have flaky behavior around long timeouts in this environment — a wrapper
   wait-loop (`until ! kill -0 $pid; do sleep; done`) was killed by
   something in the tool infrastructure well before the real job finished,
   even though the real job (a separate `nohup`'d, `disown`'d process) kept
   running unaffected. **Do not trust a killed wrapper as a signal that the
   underlying job died** — always check the actual PID (`kill -0 <pid>`)
   and the log file directly before assuming failure. A `Monitor` tool call
   polling the same PID was used as a second, more durable watcher.

## Step-by-step log (S1-S11 code done; S10/S11 batches running)

### S1 — repo skeleton (commit `1fd1c4c`)
- `pyproject.toml`: package name `migration-agent` (import name
  `migration_agent`), Python >=3.12, ruff + pytest as dev deps, hatchling
  build backend, `src/` layout.
- `src/migration_agent/__init__.py`, `tests/test_smoke.py`.
- `.gitignore`: assistant configs (CLAUDE.md, .claude/, .cursor/, .aider*,
  .continue/), Python artifacts, `/workdir/` (scratch clone dir), `/.m2/`.
- `LICENSE` (MIT), `README.md` stub with the project framing and stated
  constraints (zero spend, 40-call budget, n=50 Wilson CI caveat, Google
  free-tier data-use disclosure).
- Verified: `uv run ruff check .` and `uv run pytest -q` both green.
- Created the GitHub repo via `gh repo create ... --push`.

### S2 — dataset loader and manifests (commit `6b28c25`)
- `src/migration_agent/dataset.py`: pulls all 300 rows of
  `AmazonScience/migration-bench-java-selected` via HuggingFace's
  **dataset-viewer REST API** (`https://datasets-server.huggingface.co/rows`,
  paginated 100 at a time) using stdlib `urllib` only — deliberately avoided
  the `datasets` package (heavy, pulls pyarrow) since we only need ~36KB of
  JSON once. Split is `test`, not `train` — check this if the fetch 404s.
- Fixed seed `42`, `random.Random(42).shuffle()` over the full 300-row list
  fetched in HF row order. Dev manifest = first 20 of the shuffle, reporting
  manifest = first 50 of the *same* shuffle, so dev is always a prefix of
  reporting (consistent if the reporting slice is extended later — spec
  says slices are append-only).
- Outputs (all committed):
  - `data/migration_bench_java_selected.json` — full 300-row snapshot, the
    reproducibility anchor (future manifest rebuilds don't need to hit HF
    again, they can reshuffle this file).
  - `manifests/dev_20.json` — 20 repos, **never used for reported numbers**,
    iteration only.
  - `manifests/reporting_50.json` — 50 repos, this is where every real
    number in the README comes from.
  - Entry shape: `{"repo": "owner/name", "base_commit": "<40-char sha>",
    "license": "MIT"|"Apache-2.0"}`.
- `tests/test_dataset.py`: 300-row count, manifest sizes, required fields,
  dev-is-prefix-of-reporting invariant, no duplicate repos in reporting.
  These tests read the *committed* JSON files, no network call — CI-safe.

### S3 — base Docker image (commit `eb1a4a7`)
- `docker/Dockerfile`: Ubuntu 22.04, both `openjdk-8-jdk-headless` and
  `openjdk-17-jdk-headless` via apt, Maven 3.9.6 pinned by downloading the
  official tarball from `archive.apache.org` (not apt's version, which
  drifts by distro). Default `JAVA_HOME` is JDK 8 (matches every repo's
  base state before migration).
  - **Gotcha hit and fixed:** installing both JDKs in a single
    `apt-get install` line fails with a dpkg dependency-ordering error
    (`openjdk-17-jre-headless is not configured yet`). Fixed by splitting
    into separate sequential `apt-get install` calls (curl/git/ca-certs
    first, then JDK 8, then JDK 17).
- `docker/use-java.sh`: sourceable script (`. use-java.sh 17` or `8`) to
  switch the active JDK inside a running container. Rewrites `JAVA_HOME`
  and strips/re-adds the JVM bin dir from `PATH`.
- `VOLUME /root/.m2` declared as the shared Maven cache mount point.
- Image tagged locally as `migration-agent-base:latest` (built with
  `docker build -t migration-agent-base:latest -f docker/Dockerfile docker/`
  — not yet pushed to any registry, and doesn't need to be; it's rebuilt
  locally / in CI as needed since Actions has Docker preinstalled).
- Verified: default `java -version` shows 1.8, `mvn -version` reports 3.9.6
  bound to JDK 8, `. use-java.sh 17` correctly switches, switching back to 8
  works. Named volume `migration-agent-m2` created and confirmed to persist
  a file written in one container run and read in the next.

### S4 — clone-and-run (commit `99b6b23`)
- `src/migration_agent/runner.py`:
  - `clone_at_commit(repo, base_commit, dest)`: `git clone` the repo, `git
    checkout <base_commit>` (detached), then **delete `.git` entirely and
    reinit as a single fresh commit** ("base commit: fresh snapshot, no
    upstream history"). This is deliberate: an agent that can see real
    history can find the actual upstream Java 17 fix commit and copy it
    instead of migrating anything — Cursor measured this at 9% of
    SWE-bench Pro solves (retrieval, not derivation). Not cleanup, a
    control.
    - **Gotcha hit and fixed:** `shutil.rmtree(dest / ".git")` fails on
      Windows with `PermissionError` because git pack files (`*.pack`,
      `*.idx`) are written read-only. Fixed with a `_force_remove_readonly`
      onerror handler that clears `stat.S_IWRITE` before retrying delete.
  - `run_maven_verify(repo_dir, java_version)`: runs
    `docker run --rm -v <repo_dir>:/workspace -v migration-agent-m2:/root/.m2
    migration-agent-base:latest bash -c "[. use-java.sh N &&] cd /workspace
    && mvn -B clean verify"` via `subprocess.run`, captures stdout/stderr,
    returns the `CompletedProcess`.
  - CLI entrypoint: `uv run python -m migration_agent.runner --repo
    owner/name --base-commit <sha> --java 8`.
- Verified end to end against `fridujo/spring-automocker` (dev slice):
  history collapsed to exactly one commit (checked with
  `git log --oneline --all`), `mvn clean verify` green under Java 8 inside
  the container, exit code 0.

### S5 — warm the shared `.m2` across the 50-repo slice (DONE, commit TBD)
- `src/migration_agent/warm_m2.py`: iterates a manifest, calls
  `clone_at_commit` + `run_maven_verify(java_version=8)` per repo, catches
  and logs any exception per-repo rather than aborting the batch, writes a
  JSON summary to `workdir/_logs/warm_<manifest>.json` (gitignored —
  ephemeral, not the committed results store; that comes in S12).
  CLI: `uv run python -m migration_agent.warm_m2 --manifest
  reporting_50.json`.
- **`.m2` volume size before this run: 57MB** (from the earlier S4 smoke
  test on one repo). Final size after the full 50-repo warm-up still needs
  to be captured and reported — that's the next concrete action.
- **Gotcha hit and fixed:** `blue-veery-gmbh/spring-rest-2-ts` failed
  during `git checkout` with `Filename too long` — a real Windows
  MAX_PATH limit, not a code bug. Fixed globally with
  `git config --global core.longpaths true` (see Environment section
  above). Re-verified that repo individually after the fix: green,
  ~1m16s build time.
- **Final result: 43/50 green under Java 8.** `.m2` volume: 2.5 GB.
  (commit `1a363fa`)

### S6 — verifier v1 (commit `561f0e1`)
- `src/migration_agent/verifier.py`: `verify(repo_dir)` runs
  `mvn clean verify` under Java 17 (r1) and checks compiled `.class`
  major version == 61 (r2). Reads class headers from host filesystem
  using `\\?\` long-path prefix on Windows. CLI for standalone use.
- 7 unit tests. Verified on `fridujo/spring-automocker`: r1=FAIL
  (expected — unmigrated Java 8 code), tamper gate clean.

### S7 — tamper gate (commit `0e3fde2`)
- `src/migration_agent/tamper.py`: `snapshot_tests(repo_dir)` captures
  test method state. `check_tamper(snapshot, migrated_dir)` checks r3
  (method presence, no @Disabled added, body hash unchanged) + r4
  (count non-decreasing) + pom (no skipTests/excludes).
- 10 unit tests. Smoke-tested on `fridujo/spring-automocker`: 51 methods
  detected, self-check clean.

### S8 — JaCoCo coverage check (commit `2e765a1`)
- `src/migration_agent/coverage.py`: `measure_coverage(repo_dir,
  java_version)` injects JaCoCo 0.8.11 via `prepare-agent + verify +
  report`. Parses `target/site/jacoco/jacoco.xml` (multi-module aware).
  If measurement unavailable, passes inconclusive.
- 8 unit tests. Integration verified: 25.8% Java 8 line coverage on
  `fridujo/spring-automocker`.

### S9 — maximal check (commit `e79d005`)
- `src/migration_agent/maximal.py`: `check_maximal(repo_dir, index)`
  parses pom.xml files, compares declared major versions against a frozen
  index. Unknown deps skipped (not failed). `load_version_index()` reads
  `data/version_index.json` (built in S20; returns {} until then).
- 9 unit tests.

### S10 — T0 migration, batch runner (commit `1d876d0`)
- `src/migration_agent/migrate_t0.py`: `apply_t0(repo_dir)` patches every
  pom.xml to set compiler source/target/release to 17. Handles property
  style, plugin config style, and missing settings (injects into
  `<properties>`). Idempotent.
- Batch runner: `--batch --manifest` writes `workdir/_logs/t0_<manifest>.json`.
- Integration verified: `fridujo/spring-automocker` — T0 patched 1 pom,
  injected 12, r1=False (API incompatibilities beyond compiler bump; correct).
- **T0 batch running** (PID 479, 2026-09-14). Results pending.

### S11 — OpenRewrite T1 batch runner (commit `9c028bd`, batch pending)
- `src/migration_agent/migrate_openrewrite.py`: `run_one()` clones,
  runs `UpgradeToJava17` recipe via `rewrite-maven-plugin`, runs full
  pipeline (verifier + tamper + maximal). Batch runner writes
  `workdir/_logs/t1_<manifest>.json`.
- **HARD GATE:** batch not yet run — starts after T0 finishes.
- Two runs were needed: first run (PID 193) died at ~22/50; restarted as
  PID 251, completed all 50.
- **WinError 3 bug found and fixed in `runner.py`:** `shutil.rmtree` fails
  on Windows for Maven `target/` trees with paths > 260 chars. Five repos
  initially showed as FAILED (0s) with WinError 3. Fixed by using
  `cmd /c rmdir /s /q` on Windows. All 5 are actually green once the fix
  is applied (verified via targeted recheck run).
- **7 genuine failures** (will be recorded as unverifiable in results store):
  - 6 × Maven build failure on Java 8: Erudika/para, aws-cloudformation/
    cloudformation-cli-java-plugin (9483s before failing — budget hog),
    rht-labs/sonar-auth-openshift, spotify/apollo (test timeout),
    springdoc/springdoc-openapi, cloudiator/visor
  - 1 × bad base commit: ProgrammerAnthony/SentinelC (commit no longer
    exists upstream — history rewrite after dataset was cut)
- Final merged results at `workdir/_logs/warm_reporting_50_final.json`
  (gitignored — ephemeral).

## Immediate next actions (resume here)

**S10 T0 batch is running** (PID 479, started 2026-09-14). Monitor task
`bazlcn758` watches it. Log: `workdir/_logs/t0_batch_output.log`.
Liveness: `kill -0 479 2>/dev/null && echo running`.

When T0 batch finishes:
1. Read `workdir/_logs/t0_reporting_50.json` — record T0 minimal count.
2. Start T1 (OpenRewrite) batch:
   ```
   export PATH="$PATH:/c/Users/6ix4o/AppData/Local/Programs/DockerDesktop/resources/bin"
   cd "C:/Users/.../AI Repository Migration Agent"
   nohup uv run python -m migration_agent.migrate_openrewrite --batch \
     --manifest reporting_50.json > workdir/_logs/t1_batch_output.log 2>&1 &
   disown
   ```
3. **HARD GATE (S11):** When T1 batch finishes, read both numbers from
   `workdir/_logs/t1_reporting_50.json`:
   - minimal should be near **16.33%** (~8/50)
   - maximal should be near **2.00%** (~1/50)
   **Stop and show these numbers before proceeding.** If they're materially
   off, the harness is wrong.
4. If numbers check out: proceed to S12 (results store + README table).

## Remaining steps (S6-S27, not started)

Full detail is in the original build spec the user gave; summarized here
for quick reference. Each one: build it, run it on real data, commit,
push, before moving to the next. Do not batch.

- **S6** Verifier v1: `mvn clean verify` passes + compiled `.class` major
  version == 61 (this is r1+r2 from the paper's definitions).
- **S7** Tamper gate: test method bodies byte-identical pre/post migration
  (AST compare, not string compare), test count not decreased, no newly
  added `@Disabled`/`@Ignore`/surefire `<excludes>`/`<skipTests>`/
  `maven.test.failure.ignore`. A green build that trips any of these is
  recorded as TAMPERED, not PASS, with the specific violation logged.
- **S8** JaCoCo coverage check: line coverage must not drop >5pp vs the
  Java 8 baseline (FreshBrew added this after finding agents exclude
  failing tests to fake a pass).
- **S9** Maximal check: every pom dependency at its latest major version,
  resolved against a **frozen, date-stamped** Maven Central index snapshot
  (reproducibility — the criterion shouldn't drift week to week).
- **S10** T0: seed-only migration (bump `maven.compiler.source/target` to
  17 in every pom). Run on all 50, push results.
- **S11 — HARD GATE.** T1: OpenRewrite `UpgradeToJava17` recipe, run on all
  50. Should land near 16.33% minimal / 2.00% maximal. **Stop and show both
  numbers before continuing** — if the calibration is off, the harness is
  wrong and every later number is worthless. Don't proceed just because
  told to; actually check the numbers make sense first.
- **S12** Results store + README table generator. First real numbers land
  in the README here, at zero LLM cost.
- **S13** Gemini client: 14 RPM / 1400 RPD token bucket, 429 backoff, full
  request/response/token logging. Test standalone before wiring into
  anything else.
- **S14** Tool layer: `read_file`, `list_dir`, `grep`, `write_file`,
  `apply_patch` (both log a diff), `run_maven(compile|test|verify)` with
  truncated head+tail output, `run_command` restricted to an explicit
  allowlist (java/javac/mvn/ls/find/cat/head/tail/grep/sed/diff/git
  diff/git status) — no raw bash, deliberately, for tamper-detection and
  transcript-auditing tractability.
- **S15** Agent loop: hand-rolled (no LangChain/smolagents/OpenHands), 40
  calls, temperature 0, JSONL trajectory per repo (every message/tool
  call/result/token count/latency), resumable, per-repo result JSON so a
  finished repo never reruns.
- **S16 — HARD GATE.** T2: naive prompt, 5 repos first. Report calls and
  tokens per repo, project daily throughput at the 14 RPM / 1400 RPD
  budget. Show this before running the full 50 overnight.
- **S17** Network isolation: proxy allowlisting only `repo1.maven.org`,
  logs every blocked request with URL (a finding to report, not just a
  control). Rerun T2 isolated, check if the number moved.
- **S18** Failure taxonomy derived from actual T2 logs (not assumptions).
  Show it before building S19 against it.
- **S19** T3: engineered prompt — maximal criterion stated explicitly, a
  Java 8→17 breaking-change playbook (javax→jakarta, JAXB/JAX-WS removal,
  Nashorn removal, `sun.misc.Unsafe`, illegal reflective access/
  `--add-opens`, SecurityManager deprecation), the S18 taxonomy mapping
  Maven error signatures to fixes, explicit instruction that
  modifying/disabling/excluding tests is failure, not progress.
- **S20** Dependency version index: query Maven Central's REST API for the
  latest major of every groupId:artifactId in the slice, snapshot to disk
  with a date stamp. Plain dict, no embeddings.
- **S21** T4: retrieval — playbook + version index served locally (no
  internet needed at agent runtime, reproducible). Hypothesis to test: the
  version index (a lookup) is what actually drives the RAG gain in the
  paper, more than reasoning. Split T4 vs T3 failures by
  dependency-vs-language cause to check this; report honestly if wrong.
- **S22** Transcript auditor: for every verifier-passed run, a second model
  reads the full trajectory **blind to pass/fail** and classifies genuine /
  retrieved / gamed (Cursor's SWE-bench Pro method — found 63% of "passes"
  were retrieved, not derived). Validate by hand-labeling 30 trajectories
  together and reporting agreement.
- **S23** Wilson 95% intervals on every rate. Final tables: track, minimal,
  maximal, AUDITED maximal, CI, calls/success, wall-clock/success,
  equivalent list-price cost (labeled as such — zero actual spend).
- **S24** (packaging, not core) Second model arm: Gemini 2.5 Flash-Lite on
  T3/T4, report efficacy-per-call tradeoff.
- **S25** (packaging) GitHub Actions: ruff/mypy/pytest on every PR, plus a
  `workflow_dispatch` matrix re-verifying stored diffs across the slice.
- **S26** (packaging) PR generator for real migrations on 3 non-benchmark
  forks — plain-language summary, dependency table, test/coverage delta,
  risk flags, trajectory link. Screenshot for the README.
- **S27** (packaging) GitHub Pages results page off the committed results
  JSON.

S1-S23 is the actual project. S24-S27 is packaging — don't let scope creep
from packaging back into the core evaluation work.

## What NOT to re-litigate

- Model choice (Gemini 2.5 Flash / Flash-Lite), rate limits (14 RPM / 1400
  RPD), call cutoff (40, not 80), Docker architecture (one shared base
  image + one shared `.m2` volume, no per-repo images), no
  embeddings/vector DB, hand-rolled agent loop. These were decided upfront
  by the user and restated as "don't re-litigate unless it's broken."
- Repo name (`java-migration-bench`) and package name (`migration_agent`)
  were the only two open naming questions; both were asked and answered at
  the start of S1.
