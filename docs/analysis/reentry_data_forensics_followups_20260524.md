# Re-Entry Data Forensics Follow-ups — 2026-05-24

Context: Codex worktree `codex/reentry-data-forensics-20260524`, follow-up
to the 2026-05-24 source-reliability and sentiment-drift pass.

## 1. Commit / Sign-off Gap

Status before this note: source-reliability patch was local-only in the Codex
worktree. Sign-off must be based on a committed branch state, not on an
uncommitted working tree.

Required before merge:

- commit the source-reliability fallback patch and tests;
- run patch/diff review against the committed SHA;
- keep the main worktree untouched.

## 2. `unknown` Source Bucket

The local source-mapping audit showed:

- 372 hard-resolved audit rows in the 90d window;
- 75 rows mapped by `source_name` only;
- 372 rows mapped by `source_name` or persisted `provenance.source`;
- 120 / 372 rows still resolve to the literal source string `unknown`.

This is not the same as fully attributed source recovery. It only means those
rows have a non-empty source label. A literal `unknown` bucket can distort
future Wilson-tier calculation if treated like a normal source.

Follow-up ticket:

- decide whether `unknown` should be excluded from source reliability scoring
  or retained as an explicit non-promotable / insufficient pseudo-source;
- add a regression test for the chosen behavior;
- regenerate `monitor/source_reliability.json` on Pi after the decision.

## 3. Forensics Persistence

The working notes and JSON audit were created under `artifacts/`, which is
gitignored. That is fine for local runtime evidence, but not sufficient if the
30.05. decision pack needs a durable, reviewable record.

This tracked note preserves the actionable follow-ups in Git. If the full
sentiment-drift tables are needed for the 30.05. decision pack, move or
summarize the relevant parts into a tracked `docs/analysis/` memo before the
decision review.

## Out of Scope

Phase-0 PRE-A / PRE-C remains intentionally held back. It is live-adjacent work
and should not be bundled into this data-forensics patch.
