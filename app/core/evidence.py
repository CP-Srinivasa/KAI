"""Canonical evidence objects for auditable signal and decision inputs.

Evidence objects are small, typed facts or claims extracted from a source.
They are not signals, trades, or decisions. Downstream engines can score,
combine, accept, or reject them, but the original evidence payload stays
audit-friendly and deterministic.

Example input:

    EvidenceObject(
        claim="SEC approved a spot Bitcoin ETF filing",
        polarity=EvidencePolarity.SUPPORTS,
        kind=EvidenceKind.FACT,
        confidence=0.92,
        source=EvidenceSourceRef(
            source_type=SourceType.NEWS_API,
            source_name="newsdata",
            url="https://example.test/sec-bitcoin-etf",
        ),
        observed_at=datetime(2026, 5, 9, tzinfo=UTC),
        extractor="newsdata_adapter:v1",
        assets=["BTC/USDT"],
    )

Example output fields:

    {
        "evidence_id": "ev_...",
        "claim": "SEC approved a spot Bitcoin ETF filing",
        "polarity": "supports",
        "kind": "fact",
        "confidence": 0.92,
        "source": {"source_type": "news_api", ...},
        "assets": ["BTC/USDT"]
    }
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.core.enums import SourceType

EvidenceMetadataValue = str | int | float | bool | None


class EvidencePolarity(StrEnum):
    """How the evidence relates to a thesis or candidate signal."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class EvidenceKind(StrEnum):
    """Evidence shape, used before any scoring or decision gate."""

    FACT = "fact"
    METRIC = "metric"
    QUOTE = "quote"
    MARKET_DATA = "market_data"
    MODEL_INFERENCE = "model_inference"
    OPERATOR_INPUT = "operator_input"


class EvidenceSourceRef(BaseModel):
    """Minimal source locator for an evidence item.

    At least one locator (`source_id`, `source_name`, `url`, or
    `document_id`) must be present. This prevents untraceable evidence from
    entering audit paths while still supporting manual/operator evidence.
    """

    model_config = ConfigDict(strict=True, validate_assignment=True, extra="forbid")

    source_type: SourceType
    source_id: str | None = Field(default=None, min_length=1)
    source_name: str | None = Field(default=None, min_length=1)
    url: str | None = Field(default=None, min_length=1)
    document_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_locator(self) -> EvidenceSourceRef:
        if any((self.source_id, self.source_name, self.url, self.document_id)):
            return self
        raise ValueError("evidence source requires source_id, source_name, url, or document_id")


class EvidenceObject(BaseModel):
    """Single auditable evidence object.

    `evidence_id` is deterministic when omitted: it is derived from stable
    source and claim fields, not from ingestion time. `captured_at` records
    when KAI created the object and is intentionally excluded from the ID.
    """

    model_config = ConfigDict(strict=True, validate_assignment=True, extra="forbid")

    evidence_id: str | None = Field(default=None, min_length=4)
    claim: str = Field(min_length=1)
    polarity: EvidencePolarity
    kind: EvidenceKind
    confidence: float = Field(ge=0.0, le=1.0)
    source: EvidenceSourceRef
    observed_at: datetime
    extractor: str = Field(min_length=1)

    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_text: str | None = Field(default=None, min_length=1)
    assets: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, EvidenceMetadataValue] = Field(default_factory=dict)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _normalize_and_identify(self) -> EvidenceObject:
        claim = self.claim.strip()
        extractor = self.extractor.strip()
        if not claim:
            raise ValueError("evidence claim must not be blank")
        if not extractor:
            raise ValueError("evidence extractor must not be blank")

        object.__setattr__(self, "claim", claim)
        object.__setattr__(self, "extractor", extractor)
        object.__setattr__(self, "assets", _normalize_tokens(self.assets))
        object.__setattr__(self, "tags", _normalize_tokens(self.tags, lower=True))
        object.__setattr__(self, "limitations", _normalize_tokens(self.limitations, lower=True))

        if self.observed_at.tzinfo is None:
            observed_at = self.observed_at.replace(tzinfo=UTC)
            object.__setattr__(self, "observed_at", observed_at)
        if self.captured_at.tzinfo is None:
            captured_at = self.captured_at.replace(tzinfo=UTC)
            object.__setattr__(self, "captured_at", captured_at)

        if self.evidence_id is None:
            object.__setattr__(self, "evidence_id", build_evidence_id(self))
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_fingerprint(self) -> str:
        """Stable content hash for tamper checks and deduplication."""
        return _stable_hash(
            [
                self.claim,
                self.polarity.value,
                self.kind.value,
                self.source.source_type.value,
                self.source.source_id or "",
                self.source.source_name or "",
                self.source.url or "",
                self.source.document_id or "",
                self.observed_at.isoformat(),
                self.extractor,
            ]
        )

    def to_json_dict(self) -> dict[str, Any]:
        """Return JSON-safe output for API responses and JSONL audit trails."""
        return self.model_dump(mode="json")


def build_evidence_id(evidence: EvidenceObject) -> str:
    """Build a deterministic audit ID from stable evidence fields."""
    return f"ev_{evidence.content_fingerprint[:24]}"


def _stable_hash(parts: list[str]) -> str:
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_tokens(values: list[str], *, lower: bool = False) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token:
            continue
        if lower:
            token = token.lower()
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(token)
    return normalized
