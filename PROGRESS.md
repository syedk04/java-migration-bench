# Build progress and handoff notes

Internal working doc. Written so another agent (or me, in a future
session) can pick this project up without re-deriving context. Read this
before touching anything.

---

## What this project is

Reproducing the [MigrationBench](https://arxiv.org/abs/2505.09569) Java 8
to Java 17 migration ladder (Amazon Science, Apache-2.0) on zero-cost
infrastructure, with an evaluation harness designed to catch results that
look right but aren't (tamper gate, coverage check, blind transcript
audit). This is a portfolio piece — the number reported at the end has to
be defensible, not flattering.

Repo: https://github.com/syedk04/java-migration-bench (public)
Local: `C:\Users\6ix4o\Documents\PersonalProjects\AI Repository Migration Agent`
GitHub account: syedk04

---

## Hard constraints (do not relitigate)

- **Zero spend.** No paid API, compute, or storage. Stop and ask if a
  step needs money.
- **Small increments.** Every step: build it, run it on real data,
  commit, push. Never batch steps into one commit. Main must build and
  pass tests at every push.
- **Two hard gates — stop and show numbers, do not proceed on autopilot:**
  - **S11** (OpenRewrite calibration): must land near 16.33% minimal /
    2.00% maximal. If off, the harness is wrong and every later number
    is worthless.
  - **S16** (first real Gemini run, 5 repos): report calls/tokens per
    repo and project daily throughput before running the full 50.
- Stack decided: Python 3.12 + uv + ruff + pytest, Gemini 2.5 Flash /
  Flash-Lite free tier, Docker locally + GitHub Actions for LLM-free
  tracks, hand-rolled agent loop, no LangChain/embeddings. Don't
  re-litigate unless something is actually broken.

---

## Reference numbers (verified against paper PDF, 2026-09-12)

Selected subset, n=300, Claude-4.5-Sonnet, 80-call cutoff:

| method | minimal | maximal | avg calls/repo |
|---|---|---|---|
| OpenRewrite (static) | 16.33% | 2.00% | — |
| Strands agent baseline | 71.67% | 15.33% | 33.68 |
| + prompt engineering | — | 45.67% | 49.22 |
| + PE + RAG | — | 53.33% | 59.22 |
| hybrid (static + agent) | — | 53.33% | 52.55 |

**Definitions (paper Section 4):**
- **minimal** = r1 (`mvn clean verify` green) + r2 (bytecode major 61) +
  r3 (test AST invariance) + r4 (non-decreasing test count)
- **maximal** = minimal + r5 (every dep at latest major version, frozen
  Nov-2024 Maven Central snapshot)
- Call cutoff: 80 turns (ours: 40, a quota constraint)

At n=50: Wilson 95% CI ≈ ±13.4 pp around 50%. Enough to distinguish
2% from 45%; not enough to distinguish 45% from 53%.

---

## Environment specifics for THIS machine

Windows 11. Things that will bite a fresh agent:

1. **Docker not on PATH in open shells.** Prepend
   `/c/Users/6ix4o/AppData/Local/Programs/DockerDesktop/resources/bin`
   before any `docker` call in a Bash tool session.

2. **Git Bash mangles Unix-style paths for non-shell commands.** Set
   `MSYS_NO_PATHCONV=1` before ad-hoc `docker run` with volume paths
   typed directly in Git Bash. Python `subprocess` calls to `docker` are
   unaffected.

3. **`git config --global core.longpaths true` is set.** Needed for repos
   with deeply nested Java packages exceeding Windows 260-char MAX_PATH.

4. **`shutil.rmtree` fails on paths > 260 chars** (WinError 3) even with
   `core.longpaths=true` (that flag only helps git, not Python Win32
   APIs). Fixed in `runner.py`: uses `cmd /c rmdir /s /q` on Windows.

5. **Python stdout is block-buffered when redirected to a file.** Progress
   `print()` lines won't appear in `tail -f` until the buffer flushes.
   Use `grep -c "Cloning into" <log>` as a progress proxy instead.

6. **Background nohup+disown processes outlive the Bash tool session.**
   Never trust a killed monitor wrapper as a signal that the underlying
   job died — always check `kill -0 <pid>` and the log directly.

---

## Completed steps

### S1 — repo skeleton (commit `1fd1c4c`)
`pyproject.toml`, `src/migration_agent/__init__.py`, `tests/test_smoke.py`,
`.gitignore`, `LICENSE`, `README.md` stub. `uv run ruff check .` and
`uv run pytest -q` both green. GitHub repo created and pushed.

### S2 — dataset loader and manifests (commit `6b28c25`)
`src/migration_agent/dataset.py`: fetches 300 rows from HuggingFace
dataset-viewer API (stdlib urllib only, no heavy deps), fixed seed 42,
writes `data/migration_bench_java_selected.json` (reproducibility anchor),
`manifests/dev_20.json` (iteration only, never reported),
`manifests/reporting_50.json` (all reported numbers come from here).
`tests/test_dataset.py`: 300-row count, manifest sizes, dev-is-prefix
invariant, no duplicate repos — CI-safe, no network call.

### S3 — base Docker image (commit `eb1a4a7`)
`docker/Dockerfile`: Ubuntu 22.04, JDK 8 + JDK 17 (separate apt calls —
single-line install fails with dpkg ordering error), Maven 3.9.6 pinned
from Apache archive. `docker/use-java.sh`: sourceable JDK switcher.
Named volume `migration-agent-m2` at `/root/.m2`. Image tagged
`migration-agent-base:latest`.

### S4 — clone-and-run (commit `99b6b23`)
`src/migration_agent/runner.py`: `clone_at_commit` clones at base commit,
collapses history to one fresh commit (deliberate — prevents agents
retrieving upstream Java 17 fix, measured at 9% of SWE-bench Pro solves).
`run_maven_verify` runs `mvn clean verify` inside the sandbox container.
Bug fixed: `shutil.rmtree` on git pack files fails on Windows (read-only);
added `_force_remove_readonly` onerror handler. Verified e2e on
`fridujo/spring-automocker`: single commit, Java 8 build green.

### S5 — warm the `.m2` cache (commit `1a363fa`)
`src/migration_agent/warm_m2.py`: runs `clone_at_commit` +
`run_maven_verify(java=8)` across the full manifest, catches per-repo
errors, writes JSON summary. **Result: 43/50 green under Java 8.**
`.m2` volume: 2.5 GB. 7 failures: 6 genuine Maven build errors on Java 8
(paper's F3 filter missed them), 1 bad base commit
(ProgrammerAnthony/SentinelC — force-pushed upstream). Bug found and
fixed: `shutil.rmtree` fails on Maven `target/` trees with paths > 260
chars (WinError 3); fixed using `cmd /c rmdir /s /q` on Windows.

### S6 — verifier v1 (commit `561f0e1`)
`src/migration_agent/verifier.py`: `verify(repo_dir)` → `VerificationResult`
with r1 (`mvn verify` exit 0 under Java 17) and r2 (sampled `.class`
files at major version 61). Reads class headers from host filesystem with
`\\?\` long-path prefix on Windows. 7 unit tests. CLI verified on
`fridujo/spring-automocker`: r1=FAIL (correct — unmigrated code).

### S7 — tamper gate (commit `0e3fde2`)
`src/migration_agent/tamper.py`: `snapshot_tests` + `check_tamper`.
Checks r3 (all baseline @Test methods present, no @Disabled added, body
hash unchanged) + r4 (count non-decreasing) + pom (no skipTests /
testFailureIgnore / surefire excludes). 10 unit tests. Smoke-tested: 51
test methods detected on `fridujo/spring-automocker`, self-check clean.

### S8 — JaCoCo coverage check (commit `2e765a1`)
`src/migration_agent/coverage.py`: `measure_coverage` injects JaCoCo
0.8.11 via `prepare-agent + verify + report`; `check_coverage` returns
pass if drop ≤ 5 pp. Strips DOCTYPE before ET parse. If either
measurement unavailable, passes inconclusive. 8 unit tests. Integration
verified: 25.8% Java 8 baseline on `fridujo/spring-automocker`.

### S9 — maximal check (commit `e79d005`)
`src/migration_agent/maximal.py`: `check_maximal(repo_dir, index)` parses
pom.xml files, compares declared major version against frozen index. Skips
property-placeholder versions and unknown artifacts. `load_version_index()`
returns `{}` until S20 builds `data/version_index.json` (check is a no-op
until then). 9 unit tests.

### S10 — T0 migration + batch runner (commit `1d876d0`)
`src/migration_agent/migrate_t0.py`: `apply_t0(repo_dir)` patches every
pom.xml to set `maven.compiler.source/target/release` to 17. Handles
property style, plugin config style, and absent settings (injects into
`<properties>`, creating block if needed). Idempotent. Batch runner writes
`workdir/_logs/t0_reporting_50.json`. 9 unit tests.

### S11 — OpenRewrite T1 code (commit `9c028bd`)
`src/migration_agent/migrate_openrewrite.py`: `run_one()` clones, applies
`UpgradeToJava17` recipe via `rewrite-maven-plugin`, runs full pipeline
(verifier + tamper + maximal). Batch runner writes
`workdir/_logs/t1_reporting_50.json` and prints calibration numbers.
**Code only — batch not yet run.**

**Current test count: 46 tests, all passing.**

---

## In progress

### T0 — DONE
**Result: 9/50 = 18% minimal pass.** Log at
`workdir/_logs/t0_batch_output.log`, JSON at
`workdir/_logs/t0_reporting_50.json` (both gitignored).
These 9 repos were already Java-17-compatible with only a compiler bump.
This is the floor — every LLM track should beat it.

### T1 (OpenRewrite) — RUNNING
PID `548`, started 2026-09-14. Log: `workdir/_logs/t1_batch_output.log`
(gitignored). Monitor task `b4a89yee0` fires on PASS/FAIL lines.
- Liveness: `kill -0 548 2>/dev/null && echo running`
- Progress: `grep -c "Cloning into" workdir/_logs/t1_batch_output.log`

This batch will take several hours (OpenRewrite downloads recipe JARs
on first run, then each repo takes a few minutes).

---

## Immediate next actions (resume here)

1. **Check if T1 batch (PID 548) is still running or finished:**
   ```bash
   kill -0 548 2>/dev/null && echo running || echo done
   ```
   If it died early, rerun:
   ```bash
   export PATH="$PATH:/c/Users/6ix4o/AppData/Local/Programs/DockerDesktop/resources/bin"
   cd "C:/Users/6ix4o/Documents/PersonalProjects/AI Repository Migration Agent"
   nohup uv run python -m migration_agent.migrate_openrewrite \
     --batch --manifest reporting_50.json \
     > workdir/_logs/t1_batch_output.log 2>&1 &
   disown
   ```

2. **When T1 finishes**, read the final lines of the log:
   ```bash
   tail -5 workdir/_logs/t1_batch_output.log
   ```
   It will print:
   ```
   T1 results (50 repos):
     minimal: X/50 = Y.YY%
     maximal: X/50 = Y.YY%
     (paper calibration target: ~16.33% minimal, ~2.00% maximal)
   ```

3. **HARD GATE — S11: Stop and show the user both numbers.**
   Do not proceed if they are materially off (roughly ±5 pp from targets
   given n=50 vs paper's n=300). If they look reasonable, say so and ask
   whether to continue to S12.

4. **If S11 passes:** commit T0+T1 results summary to README (S12),
   then proceed to S13 (Gemini client).

---

## Remaining steps (S12–S27)

Each step: build it, run it on real data, commit, push. Do not batch.

- **S12** Results store + README table generator. Read T0 and T1 JSON
  logs, write committed results to `results/` dir, generate the README
  table. First real numbers land here at zero LLM cost.

- **S13** Gemini client: 14 RPM / 1400 RPD token bucket, 429 backoff,
  full request/response/token logging. Test standalone before wiring into
  anything else.

- **S14** Tool layer: `read_file`, `list_dir`, `grep`, `write_file`,
  `apply_patch`, `run_maven(compile|test|verify)` with truncated
  head+tail output, `run_command` restricted to an explicit allowlist
  (java/javac/mvn/ls/find/cat/head/tail/grep/sed/diff/git diff/git
  status). No raw bash — deliberate, for tamper-detection tractability.

- **S15** Agent loop: hand-rolled, 40 calls, temperature 0, JSONL
  trajectory per repo (every message/tool call/result/token/latency),
  resumable, per-repo result JSON (finished repo never reruns).

- **S16 — HARD GATE.** T2: naive prompt, 5 repos first. Report calls and
  tokens per repo, project daily throughput at 14 RPM / 1400 RPD. Show
  before running full 50.

- **S17** Network isolation: proxy allowlisting `repo1.maven.org` only,
  log every blocked request. Rerun T2 isolated, check if number moved.

- **S18** Failure taxonomy from actual T2 logs. Show before building S19.

- **S19** T3: engineered prompt — maximal criterion explicit, Java 8→17
  breaking-change playbook (javax→jakarta, JAXB/JAX-WS removal, Nashorn,
  `--add-opens`, SecurityManager), S18 taxonomy mapped to Maven error
  signatures, explicit instruction that disabling/excluding tests = FAIL.

- **S20** Dependency version index: query Maven Central REST API for
  latest major of every dep in the slice, snapshot to `data/version_index.json`
  with date stamp. Activates the S9 maximal check.

- **S21** T4: retrieval — playbook + version index served locally.
  Hypothesis: version index (lookup) drives the RAG gain more than
  reasoning. Split T4 vs T3 failures by dep-vs-language cause; report
  honestly if hypothesis is wrong.

- **S22** Transcript auditor: second model reads every passing trajectory
  blind to pass/fail, classifies genuine / retrieved / gamed. Validate
  by hand-labelling 30 trajectories with the user and reporting agreement.

- **S23** Wilson 95% intervals on every rate. Final tables: track,
  minimal, maximal, AUDITED maximal, CI, calls/success, wall-clock/success,
  equivalent list-price cost (labelled — zero actual spend).

- **S24** (packaging) Second model arm: Gemini 2.5 Flash-Lite on T3/T4.

- **S25** (packaging) GitHub Actions: ruff/mypy/pytest on every PR +
  `workflow_dispatch` matrix re-verifying stored diffs.

- **S26** (packaging) PR generator for real migrations on 3 non-benchmark
  forks.

- **S27** (packaging) GitHub Pages results page from committed results JSON.

S1–S23 is the actual project. S24–S27 is packaging.

---

## What NOT to re-litigate

- Model choice (Gemini 2.5 Flash / Flash-Lite), rate limits (14 RPM /
  1400 RPD), call cutoff (40, not 80), Docker architecture (one shared
  base image + one shared `.m2` volume), no embeddings/vector DB,
  hand-rolled agent loop.
- Repo name (`java-migration-bench`) and package name (`migration_agent`).
