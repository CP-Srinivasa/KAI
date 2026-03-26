# RUNBOOK.md

## Scope

Canonical operator runbook for the active PH5 hold period.
Goal: run the pipeline daily, collect directional alerts, and annotate outcomes until the gate is met.

Gate: no new feature work until at least 50 directional alerts are resolved (`hit` or `miss`).

## 1. Baseline Check

```bash
python -m pytest
python -m ruff check .
```

## 2. Daily Core Routine

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ph5_daily_ops.ps1
python scripts/ph5_hold_metrics_report.py
```

The routine does:
- `pipeline-run`
- `alerts auto-check` (historical horizon check, default dry-run)
- `alerts hold-report`
- `alerts pending-annotations`

## 3. Manual Operator Commands

```bash
python -m app.cli.main pipeline-run <feed_url> --source-id <id> --source-name <name> --top-n 5
python -m app.cli.main analyze pending --limit 50
python -m app.cli.main signals extract --limit 20 --min-priority 8
python -m app.cli.main alerts evaluate-pending
python -m app.cli.main alerts auto-check --threshold-pct 5 --horizon-hours 24 --min-age-hours 24 --dry-run
python -m app.cli.main alerts hold-report
python -m app.cli.main alerts baseline-report --input-path artifacts/ph4b_tier3_shadow.jsonl
python -m app.cli.main alerts pending-annotations --limit 20 --min-age-hours 24
python -m app.cli.main alerts annotate <document_id> <hit|miss|inconclusive>
python scripts/ph5_keyword_coverage_audit.py --limit 300 --target-coverage 80 --suggestions 30
```

## 4. Hold Gate Review

Use the generated report under `artifacts/ph5_hold/`.

Primary checks:
- resolved directional alerts (`hit` + `miss`) >= 50
- alert precision from resolved outcomes
- paper-trading evidence present

If the gate is not met, continue daily operation and annotation only.

## 5. Guardrails

- Keep execution in paper/shadow-safe mode (no live execution)
- Do not add new sprint-contract documents
- Do not add new companion-ML feature work while hold is active
- Record decisions compactly in `DECISION_LOG.md`
