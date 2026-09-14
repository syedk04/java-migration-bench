"""T3: Engineered-prompt Gemini agent.

Adds a Java 8→17 breaking-change playbook and maximal criterion instructions
to the naive T2 prompt. No retrieval (that's T4).

CLI (pilot 5 first — show numbers before full run):
    uv run python -m migration_agent.migrate_t3 \\
        --pilot --manifest reporting_50.json

CLI (full 50):
    uv run python -m migration_agent.migrate_t3 \\
        --batch --manifest reporting_50.json

Results: workdir/_logs/t3_reporting_50.json
Trajectories: workdir/trajectories/t3/
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from migration_agent.agent_loop import AgentResult, run_batch

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_DIR = REPO_ROOT / "manifests"
LOGS_DIR = REPO_ROOT / "workdir" / "_logs"
TRAJ_DIR = REPO_ROOT / "workdir" / "trajectories" / "t3"

# ---------------------------------------------------------------------------
# Engineered system prompt
# ---------------------------------------------------------------------------

ENGINEERED_SYSTEM_PROMPT = """\
You are an expert Java developer migrating Maven projects from Java 8 to Java 17.

## Goal
Make `mvn clean verify` pass under Java 17 with all original tests still
present and passing. Two success criteria:

**Minimal** (required): build green, bytecode at major version 61, all
original @Test methods present with unchanged bodies, test count non-decreasing.

**Maximal** (bonus): minimal, plus every dependency bumped to its latest
major version on Maven Central.

## Critical rule — tests must not be disabled
- Do NOT add @Disabled, @Ignore, or skipTests=true anywhere.
- Do NOT remove test methods or test classes.
- Do NOT add <exclude> patterns to the Surefire plugin.
Doing any of the above causes an automatic FAIL even if the build is green.

## Java 8 → 17 breaking-change playbook

### 1. Compiler source/target
Set in pom.xml (properties or plugin config):
  <maven.compiler.source>17</maven.compiler.source>
  <maven.compiler.target>17</maven.compiler.target>
  <!-- or: <maven.compiler.release>17</maven.compiler.release> -->

### 2. JAXB / JAX-WS removed from JDK (module java.se.ee dropped)
Add explicit dependencies:
  <dependency>
    <groupId>jakarta.xml.bind</groupId>
    <artifactId>jakarta.xml.bind-api</artifactId>
    <version>3.0.1</version>
  </dependency>
  <dependency>
    <groupId>com.sun.xml.bind</groupId>
    <artifactId>jaxb-impl</artifactId>
    <version>3.0.2</version>
  </dependency>
For JAX-WS: jakarta.xml.ws:jakarta.xml.ws-api + com.sun.xml.ws:rt

### 3. javax → jakarta namespace (EE 9+)
If the project uses Spring Boot 3+ or Jakarta EE 9+, imports like
`javax.servlet.*`, `javax.persistence.*` need renaming to `jakarta.*`.
Check Spring Boot parent version first — Boot 2.x still uses javax.

### 4. Nashorn removed (JDK 15+)
Replace `javax.script.ScriptEngineManager` Nashorn usage with GraalVM JS
or move script logic to Java. Add dependency:
  <groupId>org.graalvm.js</groupId><artifactId>js</artifactId>

### 5. Internal API access (strong encapsulation)
`InaccessibleObjectException` / `IllegalAccessException` at runtime:
- Add `--add-opens` to the Surefire plugin argLine, e.g.:
  <argLine>--add-opens java.base/java.lang=ALL-UNNAMED</argLine>
- Or refactor reflection calls to use supported APIs.

### 6. SecurityManager deprecated / removed
Code calling `System.setSecurityManager()` will fail at runtime in Java 17.
Remove or guard with `Runtime.version().feature() < 17`.

### 7. Deprecated APIs removed
- `sun.misc.BASE64Encoder/Decoder` → `java.util.Base64`
- `com.sun.image.*`, `com.sun.java.*` internal packages may be gone.
- Check Maven compiler output for `cannot find symbol` errors.

### 8. Dependency version bumps (for maximal score)
For every dependency, check if there is a newer major version on Maven
Central and update the version in pom.xml. Do not change groupId/artifactId
— just the version. Skip SNAPSHOT and RC versions.

## Workflow
1. List the repository root to understand the structure.
2. Read pom.xml (and parent pom.xml if present) to identify Java version
   settings and dependencies.
3. Apply the compiler bump first, then run `mvn compile` to get the
   initial error list.
4. Fix errors one category at a time (JAXB, javax→jakarta, internal APIs,
   etc.) using read_file / write_file / apply_patch.
5. Run `mvn test` to catch runtime failures.
6. Run `mvn verify` for a final check.
7. When green, optionally bump dependency versions for the maximal score.
8. Output DONE.

Do not waste calls reading files you don't need to change.
Use grep to find the relevant error patterns before reading whole files.
"""


def _projection(results: list[AgentResult]) -> str:
    if not results:
        return ""
    total_calls = sum(r.calls_used for r in results)
    avg_calls = total_calls / len(results)
    rpm = 14
    rpd = 1400
    secs_per_repo = avg_calls * (60.0 / rpm)
    lines = [
        f"  avg calls/repo : {avg_calls:.1f}",
        f"  @ 14 RPM       : {secs_per_repo/60:.1f} min/repo",
        f"  projected RPD  : {rpd} / {avg_calls:.1f} = {rpd/avg_calls:.0f} repos/day",
        f"  50 repos @ RPD : {50/(rpd/avg_calls)*24:.1f} hours",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="T3: engineered-prompt Gemini agent."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pilot", action="store_true",
                      help="Run first 5 repos only.")
    mode.add_argument("--batch", action="store_true",
                      help="Run all repos in manifest.")
    parser.add_argument("--manifest", default="reporting_50.json")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: Set GEMINI_API_KEY or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / args.manifest
    all_entries = json.loads(manifest_path.read_text())
    entries = all_entries[:5] if args.pilot else all_entries

    if args.pilot:
        tmp_manifest = LOGS_DIR / "t3_pilot_5.json"
        tmp_manifest.write_text(json.dumps(entries) + "\n")
        manifest_path = tmp_manifest
        out_path = LOGS_DIR / "t3_pilot_5_results.json"
    else:
        out_path = LOGS_DIR / f"t3_{args.manifest}"

    gemini_log = LOGS_DIR / "t3_gemini_calls.jsonl"

    print(f"T3 ({'pilot 5' if args.pilot else 'full batch'}) — {args.manifest}", flush=True)

    t0 = time.monotonic()
    results = run_batch(
        manifest_path,
        ENGINEERED_SYSTEM_PROMPT,
        track="T3",
        out_path=out_path,
        trajectory_dir=TRAJ_DIR,
        gemini_log=gemini_log,
        api_key=api_key,
    )

    elapsed = round(time.monotonic() - t0, 1)
    n = len(results)
    minimal = sum(1 for r in results if r.minimal)
    maximal = sum(1 for r in results if r.maximal)

    print(f"\nT3 results ({n} repos, {elapsed}s):")
    print(f"  minimal: {minimal}/{n} = {100*minimal/n:.2f}%")
    print(f"  maximal: {maximal}/{n} = {100*maximal/n:.2f}%")

    if args.pilot:
        print("\nThroughput projection:")
        print(_projection(results))

    print(f"\nResults written to {out_path}", flush=True)


if __name__ == "__main__":
    main()
