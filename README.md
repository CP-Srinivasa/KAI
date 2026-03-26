# AI Analyst Trading Bot

Lightweight paper-trading signal pipeline:
RSS ingestion -> LLM/rule analysis -> scoring -> alert dispatch (dry-run safe).

## Current State (2026-03-25)

| Field | Value |
|---|---|
| Phase | `PHASE 5` |
| Status | `HOLD` |
| Gate | `No new feature work until >=50 resolved directional alerts` |
| Next step | `Operate daily pipeline and annotate outcomes` |

## Active Product Path

1. `ingest rss`
2. `pipeline-run`
3. `analyze pending`
4. `alerts evaluate-pending`
5. `signals extract`
6. `alerts auto-check` + `alerts pending-annotations` + `alerts annotate`
7. `alerts hold-report`

## Daily Operations

Preferred routine:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ph5_daily_ops.ps1
python scripts/ph5_hold_metrics_report.py
```

Manual commands:

```bash
python -m app.cli.main pipeline-run https://cointelegraph.com/rss --source-id cointelegraph --source-name CoinTelegraph --top-n 5
python -m app.cli.main alerts auto-check --threshold-pct 5 --horizon-hours 24 --min-age-hours 24 --dry-run
python -m app.cli.main alerts hold-report
python -m app.cli.main alerts baseline-report --input-path artifacts/ph4b_tier3_shadow.jsonl
python -m app.cli.main alerts pending-annotations --limit 20 --min-age-hours 24
python -m app.cli.main alerts annotate <document_id> hit
python scripts/ph5_keyword_coverage_audit.py --limit 300 --target-coverage 80 --suggestions 30
```

Allowed outcomes for annotation are: `hit`, `miss`, `inconclusive`.

## Safety Minimum

- `I-13`: Tier1/rule-only fallback stays conservative (`actionable=False`)
- No live execution path enabled
- No secrets in repo
- Quality gate before new feature work remains active

## Canonical Living Docs

- `AGENTS.md` (operator constraints and current phase state)
- `RUNBOOK.md` (daily operator procedure)
- `docs/contracts.md` (core contracts and invariants)
- `DECISION_LOG.md` (compact decision history)

Historical governance artifacts are archived in `docs/archive/`.
