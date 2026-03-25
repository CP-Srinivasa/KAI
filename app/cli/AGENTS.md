# AGENTS.md - app/cli/

## Purpose

Typer CLI for the production-facing operator path.
Keep command surface small, explicit, and testable.

## Public Interface

```bash
python -m app.cli.main --help
```

| Command group | Purpose | Status |
|---|---|---|
| `pipeline-run` | Canonical single-call end-to-end run | active |
| `ingest` | Manual RSS ingest entrypoint | active |
| `analyze` | Analyze pending documents | active |
| `signals` | Extract signal candidates from analyzed documents | active |
| `alerts` | Alert dispatch + PH5 hold metrics/annotation ops | active |

Compatibility aliases (hidden from default help):
- `pipeline run`
- `query analyze-pending`

Current `analyze` commands:
- `pending`

Current `signals` commands:
- `extract`

Current `alerts` commands:
- `send-test`
- `evaluate-pending`
- `hold-report`
- `pending-annotations`
- `annotate`
- `auto-check`

## Constraints

- CLI commands must not duplicate business logic from `app/core`, `app/pipeline`, or `app/alerts`
- Use Rich output for operator-facing summaries
- No hardcoded config values; always use `AppSettings` and explicit flags
- Commands must be safe by default (`dry-run` where applicable)
- Keep canonical CLI focused on the production path; avoid reintroducing research-only orchestration surface

## Tests

```bash
python -m pytest tests/unit/cli
```
