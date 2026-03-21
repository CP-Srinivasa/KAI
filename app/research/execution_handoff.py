"""Signal handoff contract for external execution consumers.

Sprint 16 — defines the immutable SignalHandoff artifact and the hard boundary
between KAI's analyst layer and any external execution consumer.

Contract: docs/sprint16_execution_handoff_contract.md
Invariants: I-101–I-108

Core principle (I-101):
  KAI produces signals. KAI does NOT execute trades.
  Signal delivery ≠ execution.
  External agent ≠ trusted control plane.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.core.domain.document import CanonicalDocument
from app.core.enums import AnalysisSource
from app.research.signals import SignalCandidate

# Maximum evidence text forwarded to a consumer (I-105: no full document text leakage)
_MAX_EVIDENCE_CHARS: int = 500

# Mandatory disclaimer on every handoff artifact (I-101, I-104)
_CONSUMER_NOTE: str = (
    "Signal delivery is not execution (I-101). "
    "This handoff is advisory only. "
    "Consumption does not confirm trade intent (I-104). "
    "KAI does not execute trades."
)

HANDOFF_ACK_JSONL_FILENAME = "consumer_acknowledgements.jsonl"

# Provenance fields required for a complete handoff (I-105)
_REQUIRED_PROVENANCE_FIELDS = ("signal_id", "document_id", "analysis_source")


@dataclass(frozen=True)
class DeliveryClassification:
    """Route-aware delivery classification derived from route_path only."""

    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str


@dataclass(frozen=True)
class SignalHandoff:
    """Immutable signal delivery artifact for external consumption.

    Created from a SignalCandidate via create_signal_handoff().
    frozen=True enforces instance-level immutability (I-102).

    Deliberately excludes recommended_next_step — that is an internal
    KAI field and MUST NOT be forwarded to external consumers.
    """

    # Identity
    handoff_id: str  # UUID generated at handoff creation time
    signal_id: str
    document_id: str

    # Signal semantics
    target_asset: str
    direction_hint: str  # bullish | bearish | neutral — a HINT, not a confirmed direction
    priority: int  # 1–10
    score: float  # surfaced separately for external consumers; mirrors confidence
    confidence: float  # 0.0–1.0

    # Provenance (I-105)
    analysis_source: str  # RULE | INTERNAL | EXTERNAL_LLM
    provider: str
    route_path: str
    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str
    source_name: str | None
    source_type: str | None
    source_url: str | None

    # Context
    sentiment: str  # SentimentLabel string value
    market_scope: str  # MarketScope string value
    affected_assets: list[str]
    evidence_summary: str  # truncated supporting_evidence (max _MAX_EVIDENCE_CHARS)
    risk_notes: str

    # Timestamps (ISO 8601)
    published_at: str | None  # original document publication time
    extracted_at: str  # when the SignalCandidate was created
    handoff_at: str  # when this SignalHandoff was created

    # Audit
    provenance_complete: bool  # True if all _REQUIRED_PROVENANCE_FIELDS are non-empty
    consumer_note: str = field(default=_CONSUMER_NOTE)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "signal_handoff",
            "handoff_id": self.handoff_id,
            "signal_id": self.signal_id,
            "document_id": self.document_id,
            "target_asset": self.target_asset,
            "direction_hint": self.direction_hint,
            "priority": self.priority,
            "score": self.score,
            "confidence": self.confidence,
            "analysis_source": self.analysis_source,
            "provider": self.provider,
            "route_path": self.route_path,
            "path_type": self.path_type,
            "delivery_class": self.delivery_class,
            "consumer_visibility": self.consumer_visibility,
            "audit_visibility": self.audit_visibility,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "sentiment": self.sentiment,
            "market_scope": self.market_scope,
            "affected_assets": list(self.affected_assets),
            "evidence_summary": self.evidence_summary,
            "risk_notes": self.risk_notes,
            "published_at": self.published_at,
            "extracted_at": self.extracted_at,
            "handoff_at": self.handoff_at,
            "provenance_complete": self.provenance_complete,
            "consumer_note": self.consumer_note,
        }


@dataclass(frozen=True)
class HandoffAcknowledgement:
    """Immutable audit-only acknowledgement for an existing SignalHandoff."""

    handoff_id: str
    signal_id: str
    consumer_agent_id: str
    acknowledged_at: str
    path_type: str
    delivery_class: str
    consumer_visibility: str
    audit_visibility: str
    notes: str = ""
    status: str = "acknowledged"

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "handoff_acknowledgement",
            "handoff_id": self.handoff_id,
            "signal_id": self.signal_id,
            "consumer_agent_id": self.consumer_agent_id,
            "acknowledged_at": self.acknowledged_at,
            "path_type": self.path_type,
            "delivery_class": self.delivery_class,
            "consumer_visibility": self.consumer_visibility,
            "audit_visibility": self.audit_visibility,
            "notes": self.notes,
            "status": self.status,
        }


def _require_non_empty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(value: object, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    return value


def _required_int(value: object, *, label: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{label} must be an int")
    return value


def _required_string(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _required_float(value: object, *, label: str) -> float:
    if not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    return float(value)


def _required_bool(value: object, *, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a bool")
    return value


def _required_string_list(value: object, *, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{label} entries must be strings")
        result.append(item)
    return result


def create_signal_handoff(
    candidate: SignalCandidate,
    document: CanonicalDocument | None = None,
) -> SignalHandoff:
    """Create an immutable SignalHandoff from a SignalCandidate (I-102).

    Does NOT include recommended_next_step — that is an internal KAI field.
    Evidence is truncated to _MAX_EVIDENCE_CHARS to prevent full document leakage.
    provenance_complete is False if any required provenance field is empty.
    """
    evidence = (candidate.supporting_evidence or "")[:_MAX_EVIDENCE_CHARS]

    published_at = (
        candidate.published_at.isoformat()
        if candidate.published_at is not None
        else None
    )

    sentiment_val = (
        candidate.sentiment.value
        if hasattr(candidate.sentiment, "value")
        else str(candidate.sentiment)
    )
    market_scope_val = (
        candidate.market_scope.value
        if hasattr(candidate.market_scope, "value")
        else str(candidate.market_scope)
    )

    provenance_complete = all(
        bool(getattr(candidate, f, None)) for f in _REQUIRED_PROVENANCE_FIELDS
    )

    if document is not None and str(document.id) != candidate.document_id:
        raise ValueError(
            "SignalHandoff source document must match candidate.document_id: "
            f"{candidate.document_id}"
        )

    route_path = _resolve_primary_route_path(candidate, document)
    delivery = classify_delivery_for_route(route_path)

    return SignalHandoff(
        handoff_id=str(uuid.uuid4()),
        signal_id=candidate.signal_id,
        document_id=candidate.document_id,
        target_asset=candidate.target_asset,
        direction_hint=candidate.direction_hint,
        priority=candidate.priority,
        score=candidate.confidence,
        confidence=candidate.confidence,
        analysis_source=candidate.analysis_source,
        provider=_resolve_signal_provider_name(candidate, document),
        route_path=route_path,
        path_type=delivery.path_type,
        delivery_class=delivery.delivery_class,
        consumer_visibility=delivery.consumer_visibility,
        audit_visibility=delivery.audit_visibility,
        source_name=document.source_name if document is not None else None,
        source_type=(
            document.source_type.value
            if document is not None and document.source_type is not None
            else None
        ),
        source_url=document.url if document is not None else None,
        sentiment=sentiment_val,
        market_scope=market_scope_val,
        affected_assets=list(candidate.affected_assets),
        evidence_summary=evidence,
        risk_notes=candidate.risk_notes,
        published_at=published_at,
        extracted_at=candidate.extracted_at.isoformat(),
        handoff_at=datetime.now(UTC).isoformat(),
        provenance_complete=provenance_complete,
    )


def _resolve_signal_provider_name(
    candidate: SignalCandidate,
    document: CanonicalDocument | None,
) -> str:
    provider = ""
    if document is not None:
        provider = (document.provider or "").strip()
    if provider:
        return provider

    normalized_source = candidate.analysis_source.strip().lower()
    if normalized_source == AnalysisSource.RULE.value:
        return "rule"
    if normalized_source == AnalysisSource.INTERNAL.value:
        return "internal"
    return "external_llm"


def _resolve_primary_route_path(
    candidate: SignalCandidate,
    document: CanonicalDocument | None,
) -> str:
    normalized_source = candidate.analysis_source.strip().lower()
    if document is not None and document.effective_analysis_source is not None:
        normalized_source = document.effective_analysis_source.value

    if normalized_source == AnalysisSource.RULE.value:
        return "A.rule"
    if normalized_source == AnalysisSource.INTERNAL.value:
        return "A.internal"
    return "A.external_llm"


def classify_delivery_for_route(route_path: str) -> DeliveryClassification:
    """Classify delivery visibility from the canonical route path.

    Unknown paths fail closed and are hidden from external consumers.
    """
    path_prefix = route_path.strip().split(".", 1)[0].upper()
    if path_prefix == "A":
        return DeliveryClassification(
            path_type="primary",
            delivery_class="productive_handoff",
            consumer_visibility="visible",
            audit_visibility="visible",
        )
    if path_prefix == "B":
        return DeliveryClassification(
            path_type="shadow",
            delivery_class="audit_only",
            consumer_visibility="hidden",
            audit_visibility="visible",
        )
    if path_prefix == "C":
        return DeliveryClassification(
            path_type="control",
            delivery_class="comparison_only",
            consumer_visibility="hidden",
            audit_visibility="visible",
        )
    return DeliveryClassification(
        path_type="unknown",
        delivery_class="audit_only",
        consumer_visibility="hidden",
        audit_visibility="visible",
    )


def signal_handoff_from_dict(payload: dict[str, object]) -> SignalHandoff:
    """Rehydrate a persisted SignalHandoff artifact from JSON."""
    return SignalHandoff(
        handoff_id=_require_non_empty_string(payload.get("handoff_id"), label="handoff_id"),
        signal_id=_require_non_empty_string(payload.get("signal_id"), label="signal_id"),
        document_id=_require_non_empty_string(payload.get("document_id"), label="document_id"),
        target_asset=_require_non_empty_string(
            payload.get("target_asset"), label="target_asset"
        ),
        direction_hint=_require_non_empty_string(
            payload.get("direction_hint"), label="direction_hint"
        ),
        priority=_required_int(payload.get("priority"), label="priority"),
        score=_required_float(payload.get("score"), label="score"),
        confidence=_required_float(payload.get("confidence"), label="confidence"),
        analysis_source=_require_non_empty_string(
            payload.get("analysis_source"), label="analysis_source"
        ),
        provider=_require_non_empty_string(payload.get("provider"), label="provider"),
        route_path=_require_non_empty_string(payload.get("route_path"), label="route_path"),
        path_type=_require_non_empty_string(payload.get("path_type"), label="path_type"),
        delivery_class=_require_non_empty_string(
            payload.get("delivery_class"), label="delivery_class"
        ),
        consumer_visibility=_require_non_empty_string(
            payload.get("consumer_visibility"), label="consumer_visibility"
        ),
        audit_visibility=_require_non_empty_string(
            payload.get("audit_visibility"), label="audit_visibility"
        ),
        source_name=_optional_string(payload.get("source_name"), label="source_name"),
        source_type=_optional_string(payload.get("source_type"), label="source_type"),
        source_url=_optional_string(payload.get("source_url"), label="source_url"),
        sentiment=_require_non_empty_string(payload.get("sentiment"), label="sentiment"),
        market_scope=_require_non_empty_string(payload.get("market_scope"), label="market_scope"),
        affected_assets=_required_string_list(
            payload.get("affected_assets"), label="affected_assets"
        ),
        evidence_summary=_required_string(
            payload.get("evidence_summary"), label="evidence_summary"
        ),
        risk_notes=_optional_string(payload.get("risk_notes"), label="risk_notes") or "",
        published_at=_optional_string(payload.get("published_at"), label="published_at"),
        extracted_at=_require_non_empty_string(
            payload.get("extracted_at"), label="extracted_at"
        ),
        handoff_at=_require_non_empty_string(payload.get("handoff_at"), label="handoff_at"),
        provenance_complete=_required_bool(
            payload.get("provenance_complete"), label="provenance_complete"
        ),
        consumer_note=_optional_string(payload.get("consumer_note"), label="consumer_note")
        or _CONSUMER_NOTE,
    )


def handoff_acknowledgement_from_dict(
    payload: dict[str, object],
) -> HandoffAcknowledgement:
    """Rehydrate a persisted HandoffAcknowledgement artifact from JSON."""
    return HandoffAcknowledgement(
        handoff_id=_require_non_empty_string(payload.get("handoff_id"), label="handoff_id"),
        signal_id=_require_non_empty_string(payload.get("signal_id"), label="signal_id"),
        consumer_agent_id=_require_non_empty_string(
            payload.get("consumer_agent_id"), label="consumer_agent_id"
        ),
        acknowledged_at=_require_non_empty_string(
            payload.get("acknowledged_at"), label="acknowledged_at"
        ),
        path_type=_optional_string(payload.get("path_type"), label="path_type") or "primary",
        delivery_class=_optional_string(
            payload.get("delivery_class"), label="delivery_class"
        )
        or "productive_handoff",
        consumer_visibility=_optional_string(
            payload.get("consumer_visibility"), label="consumer_visibility"
        )
        or "visible",
        audit_visibility=_optional_string(
            payload.get("audit_visibility"), label="audit_visibility"
        )
        or "visible",
        notes=_optional_string(payload.get("notes"), label="notes") or "",
        status=_optional_string(payload.get("status"), label="status") or "acknowledged",
    )


def load_signal_handoffs(path: Path | str) -> list[SignalHandoff]:
    """Load persisted SignalHandoff artifacts from JSON or JSONL."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(f"Signal handoff file not found: {source_path}")

    if source_path.suffix.lower() == ".json":
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid SignalHandoff JSON: {source_path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("SignalHandoff JSON must be an object")
        return [signal_handoff_from_dict(dict(payload))]

    handoffs: list[SignalHandoff] = []
    for line_no, raw_line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid SignalHandoff JSONL at line {line_no}: {source_path}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"SignalHandoff row {line_no} must be an object")
        handoffs.append(signal_handoff_from_dict(dict(payload)))
    return handoffs


def save_signal_handoff(handoff: SignalHandoff, path: Path | str) -> Path:
    """Save a single SignalHandoff as a JSON artifact (I-105).

    Returns the resolved path written to.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(handoff.to_json_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return p


def save_signal_handoff_batch_jsonl(
    handoffs: list[SignalHandoff],
    path: Path | str,
) -> Path:
    """Save multiple SignalHandoffs as JSONL for batch consumption (I-108: pull-only).

    Each line is one JSON-serialized SignalHandoff. An empty list produces an empty file.
    Returns the resolved path written to.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "\n".join(json.dumps(h.to_json_dict()) for h in handoffs) + "\n"
        if handoffs
        else ""
    )
    p.write_text(content, encoding="utf-8")
    return p

def get_signal_handoff_by_id(
    handoffs: list[SignalHandoff],
    handoff_id: str,
) -> SignalHandoff:
    """Resolve a single handoff by id from a loaded artifact set."""
    for handoff in handoffs:
        if handoff.handoff_id == handoff_id:
            return handoff
    raise ValueError(f"SignalHandoff not found: {handoff_id}")


def create_handoff_acknowledgement(
    handoff: SignalHandoff,
    *,
    consumer_agent_id: str,
    notes: str = "",
) -> HandoffAcknowledgement:
    """Create an audit-only HandoffAcknowledgement for a consumer-visible SignalHandoff.

    Raises PermissionError for non-visible handoffs (shadow/control paths).
    Acknowledgement is NOT an execution trigger — receipt confirmation only (I-116).
    """
    if handoff.consumer_visibility != "visible":
        raise PermissionError(
            f"Only consumer-visible handoffs can be acknowledged — "
            f"handoff {handoff.handoff_id!r} has "
            f"consumer_visibility={handoff.consumer_visibility!r}."
        )
    return HandoffAcknowledgement(
        handoff_id=handoff.handoff_id,
        signal_id=handoff.signal_id,
        consumer_agent_id=consumer_agent_id,
        acknowledged_at=datetime.now(UTC).isoformat(),
        path_type=handoff.path_type,
        delivery_class=handoff.delivery_class,
        consumer_visibility=handoff.consumer_visibility,
        audit_visibility=handoff.audit_visibility,
        notes=notes,
    )


def append_handoff_acknowledgement_jsonl(
    ack: HandoffAcknowledgement,
    path: Path | str,
) -> Path:
    """Append a HandoffAcknowledgement as a JSONL record (append-only, I-120)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ack.to_json_dict()) + "\n")
    return p


def load_handoff_acknowledgements(path: Path | str) -> list[HandoffAcknowledgement]:
    """Load HandoffAcknowledgement records from JSONL, skipping malformed lines."""
    p = Path(path)
    if not p.exists():
        return []
    acks: list[HandoffAcknowledgement] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            acks.append(handoff_acknowledgement_from_dict(dict(payload)))
        except (ValueError, json.JSONDecodeError):
            continue
    return acks
