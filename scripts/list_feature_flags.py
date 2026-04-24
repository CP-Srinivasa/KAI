#!/usr/bin/env python
"""List all KAI feature flags from AppSettings as a Markdown table.

Usage:
    python scripts/list_feature_flags.py                   # print to stdout
    python scripts/list_feature_flags.py > docs/feature_flags.md

Introspects :class:`app.core.settings.AppSettings` recursively, picks every
boolean field, and writes a table with env-var, default, and (trimmed)
description. Answers NEO-F-META-20260424-016 (feature-flag sprawl without
central inventory).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic.fields import FieldInfo

# Ensure project root on path when invoked from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import AppSettings  # noqa: E402


def _env_prefix(model: type[BaseModel]) -> str:
    """Return the SettingsConfigDict env_prefix for a model (empty string if none)."""
    cfg = getattr(model, "model_config", None)
    if isinstance(cfg, dict):
        prefix = cfg.get("env_prefix", "")
        return prefix if isinstance(prefix, str) else ""
    return ""


def _collect_bool_flags(
    model: type[BaseModel],
    parent_prefix: str = "",
) -> list[dict[str, Any]]:
    """Walk a pydantic model and yield bool fields with env-var names."""
    rows: list[dict[str, Any]] = []
    env_prefix = _env_prefix(model) or parent_prefix

    for name, field in model.model_fields.items():
        assert isinstance(field, FieldInfo)
        annotation = field.annotation

        # Recurse into nested settings models (e.g. AppSettings.alerts)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            rows.extend(_collect_bool_flags(annotation, parent_prefix=env_prefix))
            continue

        if annotation is not bool:
            continue

        env_var = f"{env_prefix}{name.upper()}"
        default = field.default
        description = (field.description or "").strip().replace("\n", " ")
        rows.append(
            {
                "env_var": env_var,
                "default": "true" if default is True else "false" if default is False else "?",
                "scope": model.__name__,
                "description": description or "(no description — see settings.py)",
            }
        )

    return rows


def _render_markdown(rows: list[dict[str, Any]]) -> str:
    """Render collected rows as a Markdown table."""
    rows_sorted = sorted(rows, key=lambda r: (r["scope"], r["env_var"]))
    lines = [
        "# KAI Feature Flags",
        "",
        "Auto-generated from `app.core.settings.AppSettings` — **do not edit by hand**.",
        "Regenerate with:",
        "",
        "```bash",
        "python scripts/list_feature_flags.py > docs/feature_flags.md",
        "```",
        "",
        "Answers [NEO-F-META-20260424-016](../artifacts/agents/neo/findings.jsonl): central inventory for the 100+ boolean flags across the codebase. For semantics, defaults, and rollout notes see `DECISION_LOG.md`.",
        "",
        f"**Total boolean flags**: {len(rows_sorted)}",
        "",
        "| Env var | Default | Scope | Description |",
        "|---|---|---|---|",
    ]
    for r in rows_sorted:
        desc = r["description"]
        if len(desc) > 120:
            desc = desc[:117] + "…"
        # Escape pipes in descriptions to keep Markdown table stable.
        desc = desc.replace("|", "\\|")
        lines.append(f"| `{r['env_var']}` | `{r['default']}` | `{r['scope']}` | {desc} |")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    rows = _collect_bool_flags(AppSettings)
    sys.stdout.write(_render_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
