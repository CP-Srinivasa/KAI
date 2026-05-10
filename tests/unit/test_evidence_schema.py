from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.enums import SourceType
from app.core.evidence import (
    EvidenceKind,
    EvidenceObject,
    EvidencePolarity,
    EvidenceSourceRef,
)


def _source_ref() -> EvidenceSourceRef:
    return EvidenceSourceRef(
        source_type=SourceType.NEWS_API,
        source_name="newsdata",
        url="https://example.test/sec-bitcoin-etf",
        document_id="doc-123",
    )


def test_evidence_object_builds_deterministic_id_and_json_output() -> None:
    observed_at = datetime(2026, 5, 9, 10, 0, tzinfo=UTC)
    first = EvidenceObject(
        claim=" SEC approved a spot Bitcoin ETF filing ",
        polarity=EvidencePolarity.SUPPORTS,
        kind=EvidenceKind.FACT,
        confidence=0.92,
        quality_score=0.81,
        source=_source_ref(),
        observed_at=observed_at,
        extractor="newsdata_adapter:v1",
        assets=[" BTC/USDT ", "btc/usdt", "", "ETH/USDT"],
        tags=[" ETF ", "etf", "Regulatory"],
        limitations=[" headline_only ", "headline_only"],
        metadata={"provider_rank": 2, "human_reviewed": False},
    )
    second = EvidenceObject(
        claim="SEC approved a spot Bitcoin ETF filing",
        polarity=EvidencePolarity.SUPPORTS,
        kind=EvidenceKind.FACT,
        confidence=0.92,
        quality_score=0.81,
        source=_source_ref(),
        observed_at=observed_at,
        extractor="newsdata_adapter:v1",
    )

    assert first.evidence_id == second.evidence_id
    assert first.evidence_id is not None
    assert first.evidence_id.startswith("ev_")
    assert first.claim == "SEC approved a spot Bitcoin ETF filing"
    assert first.assets == ["BTC/USDT", "ETH/USDT"]
    assert first.tags == ["etf", "regulatory"]
    assert first.limitations == ["headline_only"]

    payload = first.to_json_dict()
    assert payload["polarity"] == "supports"
    assert payload["kind"] == "fact"
    assert payload["source"]["source_type"] == "news_api"
    assert payload["observed_at"] == "2026-05-09T10:00:00Z"
    assert isinstance(payload["content_fingerprint"], str)


def test_evidence_source_requires_traceable_locator() -> None:
    with pytest.raises(ValidationError, match="evidence source requires"):
        EvidenceSourceRef(source_type=SourceType.UNRESOLVED_SOURCE)


def test_evidence_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        EvidenceObject(
            claim="Market depth increased",
            polarity=EvidencePolarity.CONTEXT,
            kind=EvidenceKind.METRIC,
            confidence=1.01,
            source=_source_ref(),
            observed_at=datetime(2026, 5, 9, tzinfo=UTC),
            extractor="market_depth:v1",
        )


def test_evidence_rejects_untyped_nested_metadata() -> None:
    with pytest.raises(ValidationError):
        EvidenceObject(
            claim="Operator pasted signal context",
            polarity=EvidencePolarity.CONTEXT,
            kind=EvidenceKind.OPERATOR_INPUT,
            confidence=0.7,
            source=EvidenceSourceRef(
                source_type=SourceType.MANUAL_SOURCE,
                source_name="operator",
            ),
            observed_at=datetime(2026, 5, 9, tzinfo=UTC),
            extractor="telegram_bridge:v1",
            metadata={"nested": {"not": "audit-flat"}},
        )


def test_evidence_accepts_naive_datetimes_as_utc() -> None:
    evidence = EvidenceObject(
        claim="BTC funding rate rose",
        polarity=EvidencePolarity.CONTEXT,
        kind=EvidenceKind.MARKET_DATA,
        confidence=0.8,
        source=EvidenceSourceRef(
            source_type=SourceType.NEWS_DOMAIN,
            source_name="exchange_status",
        ),
        observed_at=datetime(2026, 5, 9, 12, 30),
        extractor="funding_adapter:v1",
    )

    assert evidence.observed_at.tzinfo is not None
    assert evidence.observed_at.isoformat() == "2026-05-09T12:30:00+00:00"
