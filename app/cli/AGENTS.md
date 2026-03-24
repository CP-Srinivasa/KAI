# AGENTS.md - app/cli/

## Purpose
Typer CLI for operator commands. Human and script-friendly interface.
Mirrors API capabilities but for terminal use.

## Public Interface

```bash
python -m app.cli.main --help
```

| Command group | Purpose | Status |
|---|---|---|
| `ingest` | Trigger source ingestion manually | planned Phase 2 |
| `analyze` | Run analysis on pending documents | planned Phase 3 |
| `sources` | List/add/disable sources | planned Phase 2 |
| `query` | Run DSL query against documents | planned Phase 2 |
| `alerts` | Test alert delivery | planned Phase 4 |
| `research` | Watchlists, briefs, signals, signal handoff, handoff collector/ack audit, operational readiness summary with provider health, distribution drift, protective gate summary, remediation recommendations, escalation/blocking/operator-action summaries, action-queue/blocking-actions/prioritized-actions/review-required-actions, operator decision-pack summary plus compatibility alias, operator runbook summary and next steps, artifact inventory/rotation, artifact retention, cleanup eligibility, protected artifact and review-required summaries, dataset export, offline evaluation, saved-report comparison, companion benchmarking, tuning artifacts, training job records, promotion records, upgrade-cycle orchestration | active Sprint 4-31 |

## Constraints

- CLI commands must not duplicate business logic from `app/core` or `app/ingestion`
- Use Rich for output formatting (tables, progress bars)
- No hardcoded config - use `AppSettings`
- Commands should be testable without side effects (use `--dry-run` where applicable)
- Runbook-facing command references must resolve to actually registered canonical `research` commands only
- Research command inventory drift must be locked by tests for final names, compatibility aliases, and superseded names

## Tests

```bash
pytest tests/unit/test_cli.py
```
