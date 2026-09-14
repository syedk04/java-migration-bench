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

## Completed steps (this session)

### S12 — results store + README table (commit `b92bcb3`)
`src/migration_agent/results.py`: reads T0/T1 JSON logs, computes Wilson 95%
CIs, writes `results/t0_summary.json` + `results/t1_summary.json`, regenerates
`README.md` Results section (live table + paper reference rows). 11 tests.

### S13 — Gemini client (commit `a70453b`)
`src/migration_agent/gemini_client.py`: stdlib-only urllib, 14 RPM token
bucket, 1400 RPD daily counter with date-rollover reset, 429/503 exponential
backoff (6 retries, cap 60s), JSONL log per call. 9 tests.

### S14 — tool layer (commit `9789521`)
`src/migration_agent/tools.py`: read/write/list/grep/apply_patch/run_maven/
run_command. Path-traversal guard, 256 KB read cap, output truncated head+tail,
Maven goal allowlist, run_command allowlist + shell metacharacter rejection.
25 tests.

### S15 — agent loop (commit `ee0ed47`)
`src/migration_agent/agent_loop.py`: 40-call budget, JSONL trajectory per repo
(every turn logged), terminal record written after verification, resume skips
completed repos, incremental JSON writes. 16 tests.

### S16 prep — T2 naive runner (commit `82464af`)
`src/migration_agent/migrate_t2.py`: naive system prompt (task + tools, no
playbook), --pilot runs first 5 + prints throughput projection. Ready to run
once API key is available.

### S19 — T3 engineered prompt (commit `82464af`)
`src/migration_agent/migrate_t3.py`: system prompt with maximal criterion,
anti-test-disabling rule, Java 8→17 playbook (JAXB, javax→jakarta, Nashorn,
--add-opens, removed APIs, dep bumps).

### S20 — version index built (commit `503db7b` + `746a6fa`)
`src/migration_agent/version_index.py`: 650 artifacts → 625 hits from Maven
Central (2026-09-14T20:35:52Z). `data/version_index.json` committed.
Activates S9 maximal checker for T2/T3/T4. 9 tests.

### S21 — T4 retrieval runner (commit `49e1bc7`)
`src/migration_agent/migrate_t4.py`: per-repo version context injected into
system prompt from version_index.json. Hypothesis: version lookup drives the
RAG gain more than reasoning.

### S22 — transcript auditor (commit `82464af`)
`src/migration_agent/transcript_audit.py`: GENUINE/RETRIEVED/GAMED classifier,
reads trajectory + git diff blind to pass/fail. 8 tests.

### S23 — final report generator (commit `746a6fa`)
`src/migration_agent/final_report.py`: Wilson CIs for all tracks, audited
maximal, paper reference table, writes `results/final_report.json` + updates
README.md. 8 tests.

### S25 — GitHub Actions CI (commit `4a20e26`)
`.github/workflows/ci.yml`: ruff + mypy + pytest on push/PR.
`.github/workflows/verify-diffs.yml`: workflow_dispatch re-verifier.
`pyproject.toml`: mypy added to dev deps. All ruff errors resolved.

---

## Previously completed steps

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

### T1 (OpenRewrite) — RUNNING (23/50 done, 3 PASS so far)
PID `317`, started 2026-09-14. 23/50 repos processed. 3 PASS (~13% so far,
on track for ~16.33% target).

Log: `workdir/_logs/t1_batch_output.log` (gitignored).
JSON: `workdir/_logs/t1_reporting_50.json` written after every repo.

- Liveness: `kill -0 317 2>/dev/null && echo running`
- Progress: `python -c "import json; d=json.load(open('workdir/_logs/t1_reporting_50.json')); print(f'{len(d)}/50, {sum(1 for r in d if r[\"minimal\"])} PASS')"`

---

## Immediate next actions (resume here)

### 1. Wait for T1 to finish (PID 317)
```bash
kill -0 317 2>/dev/null && echo running || echo done
python -c "import json; d=json.load(open('workdir/_logs/t1_reporting_50.json')); print(f'{len(d)}/50, {sum(1 for r in d if r[chr(34)+\"minimal\"+chr(34)]) } PASS')"
```
If PID 317 died, restart (resume mode will skip already-done repos):
```bash
export PATH="$PATH:/c/Users/6ix4o/AppData/Local/Programs/DockerDesktop/resources/bin"
cd "C:/Users/6ix4o/Documents/PersonalProjects/AI Repository Migration Agent"
nohup uv run python -u -m migration_agent.migrate_openrewrite \
  --batch --manifest reporting_50.json \
  > workdir/_logs/t1_batch_output.log 2>&1 &
disown && echo "PID: $!"
```

### 2. S11 HARD GATE — check calibration numbers
When T1 finishes (`tail -5 workdir/_logs/t1_batch_output.log`):
Target: **~16.33% minimal, ~2.00% maximal** (±5 pp expected at n=50).
If way off, stop and investigate the verifier before proceeding.

### 3. Commit T1 results
```bash
uv run python -m migration_agent.results
uv run python -m migration_agent.final_report
git add results/ README.md && git commit -m "S12: T1 results — X/50 minimal"
git push
```

### 4. S16 HARD GATE — T2 pilot (need GEMINI_API_KEY)
```bash
export GEMINI_API_KEY=<key>
export PATH="$PATH:/c/Users/6ix4o/AppData/Local/Programs/DockerDesktop/resources/bin"
uv run python -m migration_agent.migrate_t2 --pilot --manifest reporting_50.json
```
**STOP and show user** calls/tokens + throughput projection before running full 50.

### 5. T2 full → T3 pilot → T3 full → T4 pilot → T4 full
Each: pilot first, show numbers, get approval, then full run.
After each full run: `uv run python -m migration_agent.final_report && git commit ...`

### 6. S22: Audit passing trajectories
```bash
uv run python -m migration_agent.transcript_audit --track T2
```
Then hand-label 30 with the user, report Cohen's κ.

---

## Remaining steps

- **S17** Network isolation: proxy allowlisting repo1.maven.org, log blocked
  requests. Rerun T2 isolated, check if number moved. (Can skip if T2 shows
  clean Maven-only traffic.)
- **S18** Failure taxonomy from T2 logs. Build after seeing actual failures.
- **S24** Second model arm: Flash-Lite on T3/T4.
- **S26** PR generator for 3 non-benchmark forks.
- **S27** GitHub Pages results page.

S12–S23, S25 complete. S16/T2 blocked on API key. S17/S18/S24/S26/S27 remain.

S1–S23 is the actual project. S24–S27 is packaging.

---

## What NOT to re-litigate

- Model choice (Gemini 2.5 Flash / Flash-Lite), rate limits (14 RPM /
  1400 RPD), call cutoff (40, not 80), Docker architecture (one shared
  base image + one shared `.m2` volume), no embeddings/vector DB,
  hand-rolled agent loop.
- Repo name (`java-migration-bench`) and package name (`migration_agent`).
