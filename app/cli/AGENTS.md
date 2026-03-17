# AGENTS.md — app/cli/

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

## Constraints

- CLI commands must not duplicate business logic from `app/core` or `app/ingestion`
- Use Rich for output formatting (tables, progress bars)
- No hardcoded config — use `AppSettings`
- Commands should be testable without side effects (use `--dry-run` where applicable)

## Tests

```bash
pytest tests/unit/test_cli.py
```
