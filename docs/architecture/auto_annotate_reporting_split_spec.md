# Auto-Annotate Reporting Split Spec

Status: V5 follow-up specification only, 2026-05-21. No threshold tuning, no
eligibility-gate change, no SHADOW_ONLY change.

## Purpose

The auto-annotate learning layer currently reports too many outcomes as one
undifferentiated pool. `artifacts/operator_memos/auto_annotate_threshold_forensik_2026-05-21.md`
showed that the high `inconclusive` share is mainly a reporting/cohort problem:
raw append-only rows mix fresh auto annotations, backfill rows, re-evaluations,
and repeated annotations for the same `document_id`.

This spec defines a minimal reporting split so the operator can read the data
without changing the underlying outcome logic.

## Non-Goals

- Do not change `_scaled_threshold(...)`.
- Do not lower `move_threshold`.
- Do not relax `evaluate_directional_eligibility(...)`.
- Do not enable bearish directional tracking.
- Do not flip SHADOW_ONLY or Bayes sizing behavior.
- Do not rewrite `auto_annotator.py`.
- Do not mutate `alert_outcomes.jsonl` history.
- Do not write production artifacts from tests.

## Required Cohorts

### `fresh_auto`

Rows whose outcome note starts with `auto:`. This is the closest proxy for
normal forward operation.

Required counters:

- `total`
- `hit`
- `miss`
- `inconclusive`
- `resolved = hit + miss`
- `hit_rate_pct`, only when `resolved > 0`
- `inconclusive_pct`

### `backfill`

Rows whose outcome note starts with `backfill:`. This cohort must be reported
separately because stale windows use different effective threshold behavior and
can dominate the append-only stream.

Required counters match `fresh_auto`.

### `reeval`

Rows whose outcome note starts with `reeval:`. This cohort tracks whether old
`inconclusive` rows later resolve. It must not be mixed into fresh precision
without an explicit operator decision.

Required counters match `fresh_auto`.

### `other`

Rows with no recognized prefix. This includes manual, legacy, and
price-unavailable patterns. The report may further split `manual:` and
`price_unavailable`, but the minimum required bucket is `other`.

### `latest_per_doc`

A de-duplicated view keyed by `document_id`, keeping the row with the newest
`annotated_at`. This is the preferred operator view when asking, "what is the
current outcome state of each alert?"

Required fields:

- same counters as above
- `raw_rows`
- `unique_document_ids`
- `duplicate_rows_removed`

## Optional Dispatch Cohort

If `alert_audit.jsonl` is available, add a joined cohort:

- `fresh_dispatch`: latest-per-doc outcomes whose source alert was dispatched
  inside the requested time window.

This prevents a date query like "since 2026-05-16" from being dominated by
backfilled March/April alerts that were merely annotated after 2026-05-16.

Join rule:

- join `alert_outcomes.document_id` to `alert_audit.document_id`
- prefer `alert_audit.dispatched_at` for dispatch-time filtering
- if the audit row is missing, count the row under `missing_audit`

## Proposed Output Shape

The reporting function should return plain structured data, not formatted
terminal text:

```json
{
  "window": {
    "since": "2026-05-16T00:00:00+00:00",
    "until": "2026-05-21T23:59:59+00:00"
  },
  "raw_rows": 117,
  "cohorts": {
    "fresh_auto": {"total": 2, "hit": 0, "miss": 0, "inconclusive": 2},
    "backfill": {"total": 115, "hit": 0, "miss": 11, "inconclusive": 104},
    "reeval": {"total": 0, "hit": 0, "miss": 0, "inconclusive": 0},
    "other": {"total": 0, "hit": 0, "miss": 0, "inconclusive": 0},
    "latest_per_doc": {
      "raw_rows": 117,
      "unique_document_ids": 17,
      "duplicate_rows_removed": 100,
      "hit": 0,
      "miss": 11,
      "inconclusive": 6
    }
  }
}
```

The exact command/API surface is intentionally left open for implementation.
Acceptable surfaces are:

- a small pure helper in `app/alerts/` plus unit tests, or
- a CLI read-only report command, or
- a dashboard/API read model if an existing operator surface already consumes
  alert outcome metrics.

## Data Rules

- Parse JSONL using existing audit helpers where practical.
- Treat missing or unparsable `annotated_at` as excluded from date-window
  filtering, but count it in an `invalid_timestamp` diagnostic if a full-file
  report is requested.
- Never rewrite existing outcome rows.
- `inconclusive` remains unresolved; do not count it as hit or miss.
- `hit_rate_pct` is `null` when there are zero resolved rows.
- Percentages must disclose denominator.
- Date filtering must state whether it uses `annotated_at` or `dispatched_at`.

## Tests

Implementation must add deterministic tests with tmp JSONL fixtures:

1. Fresh auto rows are counted only in `fresh_auto`.
2. Backfill rows are counted only in `backfill`.
3. Reeval rows are counted only in `reeval`.
4. Unknown or legacy notes are counted under `other`.
5. `latest_per_doc` keeps the newest `annotated_at`.
6. `inconclusive` is excluded from resolved hit-rate.
7. Dispatch-window filtering does not count old alerts merely because they were
   annotated inside the window.
8. Missing audit joins increment `missing_audit` instead of crashing.

Tests must use `tmp_path`, must not read real `artifacts/`, and must not make
network calls.

## Acceptance Criteria

1. Reporting distinguishes `fresh_auto`, `backfill`, `reeval`, `other`, and
   `latest_per_doc`.
2. No threshold or eligibility behavior changes.
3. Raw append-only and latest-per-doc views are both visible.
4. Date-window denominator is explicit.
5. `inconclusive` remains unresolved.
6. Tests are deterministic and isolated.
7. Existing auto-annotate tests remain green.
8. No production artifact is written by tests.

## Review Gate For Auto-Annotate Changes

Every future Auto-Annotate change must answer:

1. Which cohort is affected: `fresh_auto`, `backfill`, `reeval`,
   `latest_per_doc`, or dispatch-window joined view?
2. Does the patch change thresholds, eligibility gates, bearish behavior,
   priority gates, source modifiers, SHADOW_ONLY, or Bayes sizing?
3. If yes to any tuning/gate change: where is the explicit operator sign-off?
4. Are tests using tmp fixtures instead of real `artifacts/`?
5. Is `inconclusive` still excluded from resolved hit-rate?
6. Does the report state denominator and timestamp basis?
7. Could the change make historical backfill look like fresh forward
   performance?
8. Are existing `app/alerts/auto_annotator.py` behavior tests still green?

Default decision without operator sign-off: reporting-only changes may proceed;
threshold/gate/model changes must stop.
