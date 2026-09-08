# Research Brief — ten working days

One row per actual use. `?` means unknown, never zero. Efficiency trial, not
revenue evidence. Use the existing `/research/brief` endpoint with a real
watchlist and `window_hours=24` (1–720 supported). No new ingestion required.

`BRIEF_GENERATED`: generated_at timestamp from the response; NO if unavailable.
`DOCUMENTS_USED`: response document_count (not the number of fetches).
`KAI_RUNTIME_SECONDS`: measured request duration if available, otherwise `?`.
Estimate manual time before using the brief. Workflow minutes include reading,
verification, corrections and any manual replacement research after failure.
`MINUTES_SAVED = MANUAL_RESEARCH_MINUTES_ESTIMATE - KAI_WORKFLOW_MINUTES`;
retain negative values. Empty/no_current_data output is not a successful brief.
Missing inputs leave MINUTES_SAVED unknown. No fabricated baseline or monthly
extrapolation. The API candidate scan is bounded to limit*5 documents; it does
not claim exhaustive watchlist coverage. Naive stored source times use UTC,
explicitly reported in source_timestamp_policy.

| DATE | WATCHLIST | BRIEF_GENERATED | DOCUMENTS_USED | KAI_RUNTIME_SECONDS | MANUAL_RESEARCH_MINUTES_ESTIMATE | KAI_WORKFLOW_MINUTES | MINUTES_SAVED | USEFUL YES/NO | FAILURE_OR_MISSING_INFORMATION | OPTIONAL_NOTE |
|---|---|---|---|---|---|---|---|---|---|---|
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |
| ? | ? | ? | ? | ? | ? | ? | ? | ? | ? | |

After ten actual working days: net minutes saved ?, useful days ?/10,
additional maintenance minutes ?, missing observations ?, KEEP/REDUCE/STOP ?.
Saved leisure time is not cash income. Do not subtract daily workflow time
twice when adding separate maintenance time.
