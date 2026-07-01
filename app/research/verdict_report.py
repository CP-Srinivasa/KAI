"""Attested verdict reports — the falsification platform's consumable artifact.

ADR 0012 makes KAI a truth platform: its product is not a trade signal but the
auditable VERDICT ("we tested hypothesis H under pre-registered criteria C and it
passed/failed"). This module turns any evaluator's JSON result into that product:

  * a canonical, attested payload (SHA-256 over sorted-key JSON via
    :mod:`app.truth.attestation`) any third party can re-hash and verify,
  * linked to its pre-registration (``prereg_id``) so the pass bar is provably
    fixed BEFORE the data was seen,
  * stamped with the code version that produced it (reproducibility pointer),
  * rendered as machine JSON + human markdown.

PURE except for the explicit writer: report building takes ``generated_at``/
``code_version`` as inputs so tests are deterministic. Read-only research output;
nothing here gates trades, sizing, or deploys.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.truth.attestation import compute_attestation

SCHEMA_VERSION = 1


def build_verdict_report(
    result: dict[str, Any],
    *,
    hypothesis: str,
    prereg_id: str | None,
    verdict: str,
    params: dict[str, Any],
    code_version: str,
    generated_at: datetime,
) -> dict[str, Any]:
    """Wrap an evaluator result into an attested, prereg-linked verdict record.

    ``verdict`` is the caller's one-line conclusion measured AGAINST the
    pre-registered criteria (e.g. ``"FAILED at pre-registered 24h horizon"``) —
    the report makes the claim auditable, it does not re-derive it.
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "hypothesis": hypothesis,
        "prereg_id": prereg_id,
        "verdict": verdict,
        "params": params,
        "result": result,
        "code_version": code_version,
        "generated_at_utc": generated_at.isoformat(),
    }
    return {"payload": payload, "attestation": compute_attestation(payload)}


def render_verdict_md(report: dict[str, Any]) -> str:
    """Human-readable companion to the attested JSON (hash + verify recipe)."""
    p = report["payload"]
    att = report["attestation"]
    lines = [
        f"# Verdict: {p['hypothesis']}",
        "",
        f"**{p['verdict']}**",
        "",
        f"- prereg_id: `{p['prereg_id'] or 'NOT PRE-REGISTERED (exploratory)'}`",
        f"- generated_at: {p['generated_at_utc']}",
        f"- code_version: `{p['code_version']}`",
        f"- attestation: `{att['algo']}:{att['hash']}`",
        "",
        "Verify: recompute SHA-256 over the canonical (sorted-keys, compact) JSON of",
        "`payload` in the sibling `.json` file and compare to the hash above",
        "(`app.truth.attestation.verify_attestation`).",
        "",
        "## Parameters",
        "```json",
        json.dumps(p["params"], indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## Result",
        "```json",
        json.dumps(p["result"], indent=2, sort_keys=True, default=str),
        "```",
    ]
    return "\n".join(lines)


def write_verdict_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Persist the report as ``<ts>_<hypothesis>.json`` + ``.md``; returns both paths."""
    p = report["payload"]
    ts = str(p["generated_at_utc"])[:19].replace(":", "").replace("-", "").replace("T", "_")
    stem = f"{ts}_{p['hypothesis']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_verdict_md(report) + "\n", encoding="utf-8")
    return json_path, md_path


def resolve_code_version(repo_root: Path | None = None) -> str:
    """Best-effort short git SHA of the running code; ``"unknown"`` if unavailable."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 — version stamp must never break a report
        return "unknown"


__all__ = [
    "SCHEMA_VERSION",
    "build_verdict_report",
    "render_verdict_md",
    "resolve_code_version",
    "write_verdict_report",
]
