"""Tests for app/research/execution_handoff.py (Sprint 16).

Verifies the SignalHandoff immutability contract (I-102), provenance completeness
(I-105), evidence truncation, consumer note presence, and batch JSONL serialization.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.enums import AnalysisSource, MarketScope, SentimentLabel, SourceType
from app.research.execution_handoff import (
    _CONSUMER_NOTE,
    _MAX_EVIDENCE_CHARS,
    append_handoff_acknowledgement_jsonl,
    classify_delivery_for_route,
    create_handoff_acknowledgement,
    create_signal_handoff,
    get_signal_handoff_by_id,
    handoff_acknowledgement_from_dict,
    load_handoff_acknowledgements,
    load_signal_handoffs,
    save_signal_handoff,
    save_signal_handoff_batch_jsonl,
)
from app.research.signals import SignalCandidate
from tests.unit.factories import make_document

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    signal_id: str = "sig_1",
    document_id: str = "doc_1",
    target_asset: str = "BTC",
    direction_hint: str = "bullish",
    confidence: float = 0.85,
    priority: int = 9,
    analysis_source: str = "EXTERNAL_LLM",
    sentiment: SentimentLabel = SentimentLabel.BULLISH,
    market_scope: MarketScope = MarketScope.CRYPTO,
    affected_assets: list[str] | None = None,
    supporting_evidence: str = "Strong on-chain accumulation.",
    risk_notes: str = "spam_prob=0.01 scope=crypto",
    recommended_next_step: str = "Review signal - human decision required.",
    published_at: datetime | None = None,
) -> SignalCandidate:
    return SignalCandidate(
        signal_id=signal_id,
        document_id=document_id,
        target_asset=target_asset,
        direction_hint=direction_hint,
        confidence=confidence,
        supporting_evidence=supporting_evidence,
        contradicting_evidence="None found.",
        risk_notes=risk_notes,
        source_quality=0.9,
        recommended_next_step=recommended_next_step,
        analysis_source=analysis_source,
        priority=priority,
        sentiment=sentiment,
        affected_assets=affected_assets or ["BTC"],
        market_scope=market_scope,
        published_at=published_at,
    )


# ---------------------------------------------------------------------------
# create_signal_handoff — field mapping
# ---------------------------------------------------------------------------


def test_create_signal_handoff_maps_core_fields() -> None:
    candidate = _make_candidate()
    handoff = create_signal_handoff(candidate)

    assert handoff.signal_id == "sig_1"
    assert handoff.document_id == "doc_1"
    assert handoff.target_asset == "BTC"
    assert handoff.direction_hint == "bullish"
    assert handoff.priority == 9
    assert handoff.score == 0.85
    assert handoff.confidence == 0.85
    assert handoff.analysis_source == "EXTERNAL_LLM"
    assert handoff.path_type == "primary"
    assert handoff.delivery_class == "productive_handoff"
    assert handoff.consumer_visibility == "visible"
    assert handoff.audit_visibility == "visible"


def test_create_signal_handoff_has_unique_handoff_id() -> None:
    candidate = _make_candidate()
    h1 = create_signal_handoff(candidate)
    h2 = create_signal_handoff(candidate)

    assert h1.handoff_id != h2.handoff_id  # UUID generated per call


def test_create_signal_handoff_sets_handoff_at() -> None:
    before = datetime.now(UTC).isoformat()
    candidate = _make_candidate()
    handoff = create_signal_handoff(candidate)
    after = datetime.now(UTC).isoformat()

    assert before <= handoff.handoff_at <= after


def test_create_signal_handoff_direction_hint_preserved() -> None:
    for direction in ("bullish", "bearish", "neutral"):
        h = create_signal_handoff(_make_candidate(direction_hint=direction))
        assert h.direction_hint == direction


def test_create_signal_handoff_sentiment_string() -> None:
    h = create_signal_handoff(_make_candidate(sentiment=SentimentLabel.BEARISH))
    assert isinstance(h.sentiment, str)
    assert "bearish" in h.sentiment.lower()


def test_create_signal_handoff_market_scope_string() -> None:
    h = create_signal_handoff(_make_candidate(market_scope=MarketScope.CRYPTO))
    assert isinstance(h.market_scope, str)


def test_create_signal_handoff_published_at_none() -> None:
    h = create_signal_handoff(_make_candidate(published_at=None))
    assert h.published_at is None


def test_create_signal_handoff_published_at_iso() -> None:
    ts = datetime(2026, 3, 20, 12, 0, 0, tzinfo=UTC)
    h = create_signal_handoff(_make_candidate(published_at=ts))
    assert h.published_at is not None
    assert "2026-03-20" in h.published_at


# ---------------------------------------------------------------------------
# Evidence truncation (I-105: no full document text leakage)
# ---------------------------------------------------------------------------


def test_create_signal_handoff_truncates_long_evidence() -> None:
    long_evidence = "X" * (_MAX_EVIDENCE_CHARS + 200)
    h = create_signal_handoff(_make_candidate(supporting_evidence=long_evidence))
    assert len(h.evidence_summary) == _MAX_EVIDENCE_CHARS


def test_create_signal_handoff_preserves_short_evidence() -> None:
    short = "Short evidence text."
    h = create_signal_handoff(_make_candidate(supporting_evidence=short))
    assert h.evidence_summary == short


# ---------------------------------------------------------------------------
# recommended_next_step exclusion (internal KAI field — not forwarded)
# ---------------------------------------------------------------------------


def test_create_signal_handoff_excludes_recommended_next_step() -> None:
    h = create_signal_handoff(_make_candidate(recommended_next_step="BUY NOW"))
    assert not hasattr(h, "recommended_next_step")
    data = h.to_json_dict()
    assert "recommended_next_step" not in data


def test_create_signal_handoff_includes_document_provenance_metadata() -> None:
    document = make_document(
        is_analyzed=True,
        priority_score=9,
        sentiment_label=SentimentLabel.BULLISH,
        crypto_assets=["BTC"],
        analysis_source=AnalysisSource.EXTERNAL_LLM,
        provider="openai",
        source_name="CoinDesk",
        source_type=SourceType.RSS_FEED,
        url="https://example.com/btc",
    )

    h = create_signal_handoff(_make_candidate(document_id=str(document.id)), document=document)

    assert h.provider == "openai"
    assert h.route_path == "A.external_llm"
    assert h.path_type == "primary"
    assert h.delivery_class == "productive_handoff"
    assert h.source_name == "CoinDesk"
    assert h.source_type == "rss_feed"
    assert h.source_url == "https://example.com/btc"


def test_classify_delivery_for_route_unknown_path_is_fail_closed() -> None:
    classification = classify_delivery_for_route("Z.experimental")

    assert classification.path_type == "unknown"
    assert classification.delivery_class == "audit_only"
    assert classification.consumer_visibility == "hidden"
    assert classification.audit_visibility == "visible"


def test_create_signal_handoff_rejects_mismatched_document() -> None:
    document = make_document(is_analyzed=True)

    with pytest.raises(ValueError, match="must match"):
        create_signal_handoff(_make_candidate(document_id="other-doc"), document=document)


# ---------------------------------------------------------------------------
# Consumer note (I-101, I-104)
# ---------------------------------------------------------------------------


def test_create_signal_handoff_consumer_note_always_present() -> None:
    h = create_signal_handoff(_make_candidate())
    assert h.consumer_note == _CONSUMER_NOTE
    assert "not execution" in h.consumer_note.lower()


# ---------------------------------------------------------------------------
# Provenance completeness (I-105)
# ---------------------------------------------------------------------------


def test_create_signal_handoff_provenance_complete_when_all_fields_present() -> None:
    h = create_signal_handoff(
        _make_candidate(signal_id="sig_1", document_id="doc_1", analysis_source="RULE")
    )
    assert h.provenance_complete is True


def test_create_signal_handoff_provenance_incomplete_when_signal_id_empty() -> None:
    h = create_signal_handoff(_make_candidate(signal_id=""))
    assert h.provenance_complete is False


def test_create_signal_handoff_provenance_incomplete_when_analysis_source_empty() -> None:
    h = create_signal_handoff(_make_candidate(analysis_source=""))
    assert h.provenance_complete is False


# ---------------------------------------------------------------------------
# Immutability (I-102)
# ---------------------------------------------------------------------------


def test_signal_handoff_is_frozen() -> None:
    h = create_signal_handoff(_make_candidate())
    with pytest.raises((FrozenInstanceError, TypeError)):
        h.priority = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_json_dict_has_required_fields() -> None:
    h = create_signal_handoff(_make_candidate())
    data = h.to_json_dict()

    for key in (
        "report_type",
        "handoff_id",
        "signal_id",
        "document_id",
        "target_asset",
        "direction_hint",
        "priority",
        "score",
        "confidence",
        "analysis_source",
        "provider",
        "route_path",
        "path_type",
        "delivery_class",
        "consumer_visibility",
        "audit_visibility",
        "source_name",
        "source_type",
        "source_url",
        "sentiment",
        "market_scope",
        "affected_assets",
        "evidence_summary",
        "risk_notes",
        "extracted_at",
        "handoff_at",
        "provenance_complete",
        "consumer_note",
    ):
        assert key in data, f"Missing key: {key}"

    assert data["report_type"] == "signal_handoff"


# ---------------------------------------------------------------------------
# save_signal_handoff
# ---------------------------------------------------------------------------


def test_save_signal_handoff_writes_json(tmp_path: Path) -> None:
    h = create_signal_handoff(_make_candidate())
    out = tmp_path / "handoff.json"
    result = save_signal_handoff(h, out)

    assert result == out
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["report_type"] == "signal_handoff"
    assert data["signal_id"] == "sig_1"


def test_save_signal_handoff_creates_parent_dirs(tmp_path: Path) -> None:
    h = create_signal_handoff(_make_candidate())
    out = tmp_path / "deep" / "subdir" / "handoff.json"
    save_signal_handoff(h, out)
    assert out.exists()


# ---------------------------------------------------------------------------
# save_signal_handoff_batch_jsonl
# ---------------------------------------------------------------------------


def test_save_signal_handoff_batch_jsonl_empty(tmp_path: Path) -> None:
    out = tmp_path / "batch.jsonl"
    result = save_signal_handoff_batch_jsonl([], out)
    assert result == out
    assert out.read_text(encoding="utf-8") == ""


def test_save_signal_handoff_batch_jsonl_single(tmp_path: Path) -> None:
    h = create_signal_handoff(_make_candidate())
    out = tmp_path / "batch.jsonl"
    save_signal_handoff_batch_jsonl([h], out)

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["report_type"] == "signal_handoff"


def test_save_signal_handoff_batch_jsonl_multiple(tmp_path: Path) -> None:
    h1 = create_signal_handoff(_make_candidate(signal_id="sig_1", target_asset="BTC"))
    h2 = create_signal_handoff(_make_candidate(signal_id="sig_2", target_asset="ETH"))
    out = tmp_path / "batch.jsonl"
    save_signal_handoff_batch_jsonl([h1, h2], out)

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assets = [json.loads(line)["target_asset"] for line in lines]
    assert "BTC" in assets
    assert "ETH" in assets


def test_load_signal_handoffs_roundtrip_json(tmp_path: Path) -> None:
    handoff = create_signal_handoff(_make_candidate())
    out = tmp_path / "handoff.json"
    save_signal_handoff(handoff, out)

    loaded = load_signal_handoffs(out)

    assert len(loaded) == 1
    assert loaded[0].handoff_id == handoff.handoff_id
    assert loaded[0].signal_id == handoff.signal_id


def test_load_signal_handoffs_roundtrip_jsonl(tmp_path: Path) -> None:
    handoff = create_signal_handoff(_make_candidate())
    out = tmp_path / "handoffs.jsonl"
    save_signal_handoff_batch_jsonl([handoff], out)

    loaded = load_signal_handoffs(out)

    assert len(loaded) == 1
    assert loaded[0].handoff_id == handoff.handoff_id





def test_get_signal_handoff_by_id_raises_on_missing() -> None:
    handoff = create_signal_handoff(_make_candidate())

    with pytest.raises(ValueError, match="not found"):
        get_signal_handoff_by_id([handoff], "missing-handoff")


def test_create_handoff_acknowledgement_maps_visible_handoff() -> None:
    handoff = create_signal_handoff(_make_candidate())

    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="collector-agent",
        notes="received",
    )

    assert acknowledgement.handoff_id == handoff.handoff_id
    assert acknowledgement.signal_id == handoff.signal_id
    assert acknowledgement.consumer_agent_id == "collector-agent"
    assert acknowledgement.path_type == "primary"
    assert acknowledgement.delivery_class == "productive_handoff"
    assert acknowledgement.consumer_visibility == "visible"
    assert acknowledgement.status == "acknowledged"


def test_create_handoff_acknowledgement_rejects_hidden_handoff() -> None:
    handoff = handoff_acknowledgement_from_dict(
        {
            "handoff_id": "legacy-handoff",
            "signal_id": "legacy-signal",
            "consumer_agent_id": "legacy-agent",
            "acknowledged_at": datetime.now(UTC).isoformat(),
            "path_type": "shadow",
            "delivery_class": "audit_only",
            "consumer_visibility": "hidden",
            "audit_visibility": "visible",
        }
    )
    visible_handoff = create_signal_handoff(_make_candidate())
    hidden_handoff = visible_handoff.__class__(
        handoff_id=visible_handoff.handoff_id,
        signal_id=visible_handoff.signal_id,
        document_id=visible_handoff.document_id,
        target_asset=visible_handoff.target_asset,
        direction_hint=visible_handoff.direction_hint,
        priority=visible_handoff.priority,
        score=visible_handoff.score,
        confidence=visible_handoff.confidence,
        analysis_source=visible_handoff.analysis_source,
        provider=visible_handoff.provider,
        route_path="B.companion",
        path_type=handoff.path_type,
        delivery_class=handoff.delivery_class,
        consumer_visibility=handoff.consumer_visibility,
        audit_visibility=handoff.audit_visibility,
        source_name=visible_handoff.source_name,
        source_type=visible_handoff.source_type,
        source_url=visible_handoff.source_url,
        sentiment=visible_handoff.sentiment,
        market_scope=visible_handoff.market_scope,
        affected_assets=visible_handoff.affected_assets,
        evidence_summary=visible_handoff.evidence_summary,
        risk_notes=visible_handoff.risk_notes,
        published_at=visible_handoff.published_at,
        extracted_at=visible_handoff.extracted_at,
        handoff_at=visible_handoff.handoff_at,
        provenance_complete=visible_handoff.provenance_complete,
        consumer_note=visible_handoff.consumer_note,
    )

    with pytest.raises(PermissionError, match="consumer-visible"):
        create_handoff_acknowledgement(
            hidden_handoff,
            consumer_agent_id="collector-agent",
        )


def test_load_handoff_acknowledgements_roundtrip_jsonl(tmp_path: Path) -> None:
    handoff = create_signal_handoff(_make_candidate())
    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="collector-agent",
    )
    out = tmp_path / "consumer_acknowledgements.jsonl"

    append_handoff_acknowledgement_jsonl(acknowledgement, out)
    loaded = load_handoff_acknowledgements(out)

    assert len(loaded) == 1
    assert loaded[0].handoff_id == acknowledgement.handoff_id
    assert loaded[0].consumer_agent_id == "collector-agent"


def test_load_handoff_acknowledgements_skips_malformed_rows(tmp_path: Path) -> None:
    handoff = create_signal_handoff(_make_candidate())
    acknowledgement = create_handoff_acknowledgement(
        handoff,
        consumer_agent_id="collector-agent",
    )
    out = tmp_path / "consumer_acknowledgements.jsonl"

    append_handoff_acknowledgement_jsonl(acknowledgement, out)
    with out.open("a", encoding="utf-8") as fh:
        fh.write("{bad json}\n")
        fh.write('{"handoff_id": "missing-fields"}\n')

    loaded = load_handoff_acknowledgements(out)

    assert len(loaded) == 1
    assert loaded[0].signal_id == acknowledgement.signal_id
