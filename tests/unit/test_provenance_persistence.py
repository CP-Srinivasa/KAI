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
        source="rss",
        version="rss-0",
        ingest_event_id=doc_id,
    ).with_hash(SECRET)
    second = SignalProvenance(
        source="rss",
        version="rss-1",
        ingest_event_id=doc_id,
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
        source="rss",
        version="rss-1",
        ingest_event_id="doc_x",
    ).with_hash("")
    assert prov.provenance_hash is None
    assert prov.verify_hash("") is False
    assert prov.verify_hash("any-secret") is False


# --- V8.3 / SAT-C-V8-002: zero-downtime secret rotation ---


def _bare_provenance(signal_path_id: str = "sp_rot_001") -> SignalProvenance:
    return SignalProvenance(
        source="rss",
        version="rss-1",
        signal_path_id=signal_path_id,
        auth_method="n/a",
        ingest_event_id="doc_rot",
    )


def test_verify_hash_accepts_next_secret_during_rotation() -> None:
    """Row signed under OLD secret must verify during grace period when the
    operator has promoted a NEW secret and moved OLD to *_next."""
    old_secret = "old-provenance-secret"
    new_secret = "new-provenance-secret"
    sealed_under_old = _bare_provenance().with_hash(old_secret)

    # Phase 3 of rollover: primary=new, next=old. Historical rows still verify.
    assert sealed_under_old.verify_hash(new_secret, secret_next=old_secret) is True
    # Sanity: same row does NOT verify under new alone.
    assert sealed_under_old.verify_hash(new_secret) is False
    # Sanity: still verifies under old alone (rotation not yet completed).
    assert sealed_under_old.verify_hash(old_secret) is True


def test_verify_hash_rejects_after_rotation_window_closes() -> None:
    """Phase 4 (operator clears *_next) — old-key rows are unverifiable by
    design. That is the rotation being complete, not a regression."""
    old_secret = "old-provenance-secret"
    new_secret = "new-provenance-secret"
    sealed_under_old = _bare_provenance().with_hash(old_secret)

    # Phase 4: next cleared. Old rows fail verification.
    assert sealed_under_old.verify_hash(new_secret, secret_next="") is False
    assert sealed_under_old.verify_hash(new_secret) is False


def test_verify_hash_next_secret_symmetric_during_phase1() -> None:
    """Phase 1 of rollover: primary=old, next=new — rows written before and
    after the operator set *_next must both verify regardless of order."""
    old_secret = "old-provenance-secret"
    new_secret = "new-provenance-secret"

    # Row written BEFORE operator set *_next: signed under old (primary).
    sealed_pre = _bare_provenance("sp_pre").with_hash(old_secret)
    assert sealed_pre.verify_hash(old_secret, secret_next=new_secret) is True

    # Hypothetical row signed under new (e.g. after operator promoted in
    # Phase 3) — must verify when caller supplies new as primary, old as next.
    sealed_post = _bare_provenance("sp_post").with_hash(new_secret)
    assert sealed_post.verify_hash(new_secret, secret_next=old_secret) is True

    # Cross-check: swapping primary/next does not break verification.
    assert sealed_pre.verify_hash(new_secret, secret_next=old_secret) is True
    assert sealed_post.verify_hash(old_secret, secret_next=new_secret) is True


def test_verify_hash_rejects_tampering_even_with_next_secret() -> None:
    """Rotation must not weaken tamper-detection. A row whose signed fields
    were altered must fail under BOTH primary and next."""
    old_secret = "old-provenance-secret"
    new_secret = "new-provenance-secret"
    sealed = _bare_provenance().with_hash(old_secret)

    from dataclasses import replace

    tampered = replace(sealed, signal_path_id="sp_attacker_chosen")

    assert tampered.verify_hash(old_secret) is False
    assert tampered.verify_hash(new_secret, secret_next=old_secret) is False
    assert tampered.verify_hash(old_secret, secret_next=new_secret) is False


def test_verify_hash_next_secret_alone_does_not_bypass_primary_requirement() -> None:
    """Empty primary + non-empty next must NOT accept anything — primary is
    still required. This guards against a misconfiguration where the operator
    only sets *_next and leaves the primary empty."""
    secret = "some-secret"
    sealed = _bare_provenance().with_hash(secret)
    assert sealed.verify_hash("", secret_next=secret) is False
