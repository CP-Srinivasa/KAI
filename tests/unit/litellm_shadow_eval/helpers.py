from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.litellm_shadow_eval.models import RUNTIME_PROOF_FLAGS, RuntimeEvidenceFlags
from scripts.litellm_shadow_eval.policy import runtime_flags_from_dict

#: Ein Tag vor dem festen Testzeitpunkt 2026-09-04: frisch genug fuer jede
#: Altersgrenze, die eine Politik vernuenftigerweise setzt.
PROVEN_AT = "2026-09-03T00:00:00+00:00"


def row(side: str, number: int = 0, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": "litellm-shadow-eval/v1",
        "evaluation_id": f"eval-{number}",
        "correlation_id": f"corr-{number}",
        "call_id": f"call-{number}",
        "logical_route": "standard",
        "purpose": "analysis",
        "side": side,
        "mode": "off" if side == "DIRECT" else "shadow",
        "requested_alias": "kai-standard",
        "actual_provider": "openai",
        "actual_model": "gpt-4o-mini",
        "identity_proven": True,
        "success": True,
        "schema_valid": True,
        "outcome": "success",
        "error_class": None,
        "fallback_used": False,
        "retry_count": 0,
        "attempt_count": 1,
        "latency_ms": 10.0 if side == "DIRECT" else 12.0,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.01 if side == "DIRECT" else 0.008,
        "cost_known": True,
        "response_fingerprint": f"fingerprint-{number}",
        "timestamp": "2026-09-04T00:00:00+00:00",
        "execution_authority": side == "DIRECT",
    }
    value.update(overrides)
    return value


def write_jsonl(path: Path, rows: list[object]) -> Path:
    path.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in rows), encoding="utf-8"
    )
    return path


def proof(flag: str, *, proven_at: str = PROVEN_AT) -> dict[str, Any]:
    """Ein referenzierter Betriebsnachweis in der Form, die das CLI liest."""
    return {
        "proven": True,
        "artifact": f"artifacts/litellm/runtime_proofs/{flag}.json",
        "artifact_sha256": hashlib.sha256(flag.encode("utf-8")).hexdigest(),
        "proven_at": proven_at,
        "version": "04046c68",
    }


def runtime_evidence(
    *, proven_at: str = PROVEN_AT, referenced: bool = True, **overrides: bool
) -> dict[str, Any]:
    """JSON-Form der Laufzeitbelege: jedes erbrachte ``*_proven`` als Objekt.

    ``referenced=False`` liefert die alte Form mit nackten Booleans -- genau
    die Form, die eine strenge Politik NICHT als Beleg gelten laesst.
    """
    values: dict[str, bool] = dict.fromkeys(RUNTIME_PROOF_FLAGS, True)
    values.update({"trading_gate_changed": False, "execution_gate_changed": False})
    values.update(overrides)
    return {
        name: (
            proof(name, proven_at=proven_at)
            if referenced and value is True and name in RUNTIME_PROOF_FLAGS
            else value
        )
        for name, value in values.items()
    }


def proven_flags(
    *, proven_at: str = PROVEN_AT, referenced: bool = True, **overrides: bool
) -> RuntimeEvidenceFlags:
    """Vollstaendig belegte Laufzeitnachweise -- ueber den echten Parser."""
    return runtime_flags_from_dict(
        runtime_evidence(proven_at=proven_at, referenced=referenced, **overrides)
    )
