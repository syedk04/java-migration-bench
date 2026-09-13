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
