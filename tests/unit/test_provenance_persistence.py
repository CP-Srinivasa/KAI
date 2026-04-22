"""D-125 / SAT-C-PROV-20260422-001 — provenance persistence round-trips.

Invariants:
- AlertAuditRecord & AlertOutcomeAnnotation serialise ``provenance`` into the
  JSONL and re-parse it back identically (all 6 SAT-fields round-trip).
- ``SignalProvenance.verify_hash`` round-trips via ``with_hash`` under the same
  secret, and rejects tampering across any of the 5 HMAC-covered fields.
- ``latest_provenance_by_document_id`` returns the most recent provenance
  (redispatch / re-audit scenario) and skips rows without provenance.
"""

from __future__ import annotations

from pathlib import Path

from app.alerts.audit import (
    AlertAuditRecord,
    AlertOutcomeAnnotation,
    append_alert_audit,
    append_outcome_annotation,
    latest_provenance_by_document_id,
    load_alert_audits,
    load_outcome_annotations,
)
from app.signals.models import SignalProvenance

SECRET = "unit-test-secret"


def _full_provenance() -> SignalProvenance:
    """Provenance with all 6 SAT fields populated (hash computed)."""
    return SignalProvenance(
        source="rss",
        version="rss-1",
        signal_path_id="sp_test_001",
        auth_method="n/a",
        ingest_event_id="doc_abc123",
    ).with_hash(SECRET)


def test_audit_record_round_trips_full_provenance(tmp_path: Path) -> None:
    prov = _full_provenance()
    record = AlertAuditRecord(
        document_id="doc_abc123",
        channel="telegram",
        message_id="msg_1",
        is_digest=False,
        dispatched_at="2026-04-22T10:00:00+00:00",
        sentiment_label="bullish",
        provenance=prov,
    )
    append_alert_audit(record, tmp_path)

    loaded = load_alert_audits(tmp_path)
    assert len(loaded) == 1
    loaded_prov = loaded[0].provenance
    assert loaded_prov is not None
    assert loaded_prov == prov
    assert loaded_prov.verify_hash(SECRET) is True


def test_outcome_annotation_round_trips_full_provenance(tmp_path: Path) -> None:
    prov = _full_provenance()
    ann = AlertOutcomeAnnotation(
        document_id="doc_abc123",
        outcome="hit",
        annotated_at="2026-04-22T11:00:00+00:00",
        asset="BTC",
        provenance=prov,
    )
    append_outcome_annotation(ann, tmp_path)

    loaded = load_outcome_annotations(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].provenance == prov
    assert loaded[0].provenance is not None
    assert loaded[0].provenance.verify_hash(SECRET) is True


def test_provenance_hash_rejects_any_field_tamper() -> None:
    prov = _full_provenance()
    # Each tampered variant drops provenance_hash=None then re-injects the
    # ORIGINAL hash, mimicking an attacker who flipped a field but could not
    # recompute the HMAC because they lack the secret.
    tampered_variants = [
        prov.to_dict() | {"source": "tradingview_webhook"},
        prov.to_dict() | {"version": "rss-2"},
        prov.to_dict() | {"signal_path_id": "sp_attacker"},
        prov.to_dict() | {"auth_method": "hmac"},
        prov.to_dict() | {"ingest_event_id": "doc_attacker"},
    ]
    for raw in tampered_variants:
        reparsed = SignalProvenance.from_dict(raw)
        assert reparsed is not None
        assert reparsed.verify_hash(SECRET) is False


def test_latest_provenance_wins_on_redispatch(tmp_path: Path) -> None:
    """Two audit rows for the same doc: the LAST provenance is authoritative."""
    doc_id = "doc_redispatched"
    first = SignalProvenance(
        source="rss", version="rss-0", ingest_event_id=doc_id,
    ).with_hash(SECRET)
    second = SignalProvenance(
        source="rss", version="rss-1", ingest_event_id=doc_id,
    ).with_hash(SECRET)
    for prov in (first, second):
        append_alert_audit(
            AlertAuditRecord(
                document_id=doc_id,
                channel="telegram",
                message_id=None,
                is_digest=False,
                dispatched_at="2026-04-22T10:00:00+00:00",
                provenance=prov,
            ),
            tmp_path,
        )

    lookup = latest_provenance_by_document_id(tmp_path)
    assert lookup[doc_id].version == "rss-1"


def test_latest_provenance_skips_untagged_rows(tmp_path: Path) -> None:
    """Legacy rows without provenance must not appear in the lookup."""
    append_alert_audit(
        AlertAuditRecord(
            document_id="doc_legacy",
            channel="telegram",
            message_id=None,
            is_digest=False,
            dispatched_at="2026-04-22T09:00:00+00:00",
            # no provenance
        ),
        tmp_path,
    )
    prov = _full_provenance()
    append_alert_audit(
        AlertAuditRecord(
            document_id="doc_tagged",
            channel="telegram",
            message_id=None,
            is_digest=False,
            dispatched_at="2026-04-22T10:00:00+00:00",
            provenance=prov,
        ),
        tmp_path,
    )

    lookup = latest_provenance_by_document_id(tmp_path)
    assert "doc_legacy" not in lookup
    assert lookup["doc_tagged"] == prov


def test_provenance_hash_empty_secret_is_fail_open() -> None:
    """Empty secret → no hash computed, verify returns False (stays unbound)."""
    prov = SignalProvenance(
        source="rss", version="rss-1", ingest_event_id="doc_x",
    ).with_hash("")
    assert prov.provenance_hash is None
    assert prov.verify_hash("") is False
    assert prov.verify_hash("any-secret") is False
