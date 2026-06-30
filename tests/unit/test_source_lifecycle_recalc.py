"""Unit tests for the source-lifecycle recalc engine (scripts.source_lifecycle_recalc).

The engine DETECTS + RANKS + FLAGS (Phase 2); it must not rotate. These tests pin
the load-bearing behaviour:
- determinism of the lifecycle enrichment in (inputs, now);
- silent detection at the signal level (last directional dispatch);
- the pin mechanism is evidence-gated and stays INERT for provisional sources;
- rotation is only FLAGGED, gated on n >= demote floor (or silence);
- every emitted audit event is an FSM-legal hop (illegal jumps decomposed).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.source_lifecycle_recalc import (
    PIN_MIN_CONSECUTIVE_RUNS,
    _last_signal_by_source,
    _legal_path,
    _load_prior_ranking,
    _logical_status,
    _prior_status,
    build_lifecycle_ranking,
    main,
)

from app.alerts.audit import AlertAuditRecord
from app.core.enums import SourceStatus

_NOW = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)


def _entry(
    source: str,
    *,
    rank: int,
    n: int,
    provisional: bool,
    wilson: float | None,
    reliability_tier: str = "neutral",
) -> dict:
    return {
        "source_name": source,
        "rank": rank,
        "lifecycle_tier": "top10" if rank <= 10 else "ranked",
        "provisional": provisional,
        "wilson_lower_95": wilson,
        "n": n,
        "hits": n // 2,
        "point_estimate": 0.5,
        "reliability_tier": reliability_tier,
    }


# ── pure helpers ───────────────────────────────────────────────────────────


def test_logical_status_precedence() -> None:
    assert _logical_status(silent=False, pinned=True) is SourceStatus.PINNED
    assert _logical_status(silent=True, pinned=True) is SourceStatus.PINNED  # pin wins
    assert _logical_status(silent=True, pinned=False) is SourceStatus.SILENT
    assert _logical_status(silent=False, pinned=False) is SourceStatus.ACTIVE


def test_legal_path_direct_and_noop() -> None:
    assert _legal_path(SourceStatus.ACTIVE, SourceStatus.ACTIVE) == []
    assert _legal_path(SourceStatus.ACTIVE, SourceStatus.SILENT) == [SourceStatus.SILENT]
    assert _legal_path(SourceStatus.SILENT, SourceStatus.ACTIVE) == [SourceStatus.ACTIVE]


def test_legal_path_decomposes_illegal_pinned_to_silent() -> None:
    """pinned->silent is FSM-illegal; it must route through active."""
    path = _legal_path(SourceStatus.PINNED, SourceStatus.SILENT)
    assert path == [SourceStatus.ACTIVE, SourceStatus.SILENT]


def test_last_signal_takes_max_and_skips_digest() -> None:
    audits = [
        AlertAuditRecord(
            document_id="d1",
            channel="telegram",
            message_id="m",
            is_digest=False,
            dispatched_at="2026-06-20T10:00:00+00:00",
            source_name="Src",
        ),
        AlertAuditRecord(
            document_id="d2",
            channel="telegram",
            message_id="m",
            is_digest=False,
            dispatched_at="2026-06-22T10:00:00+00:00",  # newer
            source_name="Src",
        ),
        AlertAuditRecord(
            document_id="d3",
            channel="telegram",
            message_id="m",
            is_digest=True,  # excluded even though newest
            dispatched_at="2026-06-23T10:00:00+00:00",
            source_name="Src",
        ),
    ]
    out = _last_signal_by_source(audits, {})
    assert out["Src"] == datetime(2026, 6, 22, 10, 0, tzinfo=UTC)


def test_prior_status_defaults_active_for_new_source() -> None:
    assert _prior_status(None) is SourceStatus.ACTIVE
    assert _prior_status({"logical_status": "silent"}) is SourceStatus.SILENT
    assert _prior_status({"logical_status": "bogus"}) is SourceStatus.ACTIVE


# ── build_lifecycle_ranking ────────────────────────────────────────────────


def test_recent_active_source_is_not_silent_no_event() -> None:
    ranked = [_entry("Fresh", rank=1, n=30, provisional=True, wilson=0.45)]
    last = {"Fresh": _NOW - timedelta(hours=2)}
    out, counts, events = build_lifecycle_ranking(ranked, last, {}, _NOW)
    assert out[0]["silent"] is False
    assert out[0]["logical_status"] == "active"
    assert counts["silent"] == 0
    assert events == []  # prior defaults to active; no change


def test_silent_source_flags_rotation_and_emits_event() -> None:
    ranked = [
        _entry("Quiet", rank=1, n=30, provisional=True, wilson=0.40, reliability_tier="watch")
    ]
    last = {"Quiet": _NOW - timedelta(days=30)}  # well past silent window
    out, counts, events = build_lifecycle_ranking(ranked, last, {}, _NOW)
    assert out[0]["silent"] is True
    assert out[0]["rotation_flagged"] is True
    assert out[0]["consecutive_top_runs"] == 0
    assert counts["silent"] == 1
    assert counts["rotation_flagged"] == 1
    assert [(e.from_status, e.to_status) for e in events] == [("active", "silent")]


def test_delivering_docs_keeps_source_active_despite_signal_silence() -> None:
    # cointelegraph case (2026-06-28): a high-volume context/news source with no
    # recent DIRECTIONAL signal but still delivering DOCUMENTS must NOT be
    # silenced — silencing it for lack of trades is the Kontext!=Signal lens-trap
    # (ADR 0012). Document delivery keeps it active and un-flagged.
    ranked = [
        _entry(
            "CtxNews", rank=3, n=18, provisional=True, wilson=0.386, reliability_tier="insufficient"
        )
    ]
    last = {"CtxNews": _NOW - timedelta(days=30)}  # no directional signal in window
    docs = {"CtxNews": _NOW - timedelta(hours=3)}  # but still delivering documents
    out, counts, events = build_lifecycle_ranking(ranked, last, {}, _NOW, last_document=docs)
    assert out[0]["silent"] is False
    assert out[0]["rotation_flagged"] is False
    assert out[0]["logical_status"] == "active"
    assert counts["silent"] == 0
    assert events == []


def test_no_signal_and_no_docs_is_still_silent() -> None:
    # A source dark on BOTH signals and documents has genuinely gone quiet →
    # still silent (the doc-awareness only spares sources that still deliver).
    ranked = [_entry("Dark", rank=1, n=30, provisional=True, wilson=0.40, reliability_tier="watch")]
    last = {"Dark": _NOW - timedelta(days=30)}
    docs = {"Dark": _NOW - timedelta(days=30)}  # documents also stale
    out, counts, events = build_lifecycle_ranking(ranked, last, {}, _NOW, last_document=docs)
    assert out[0]["silent"] is True
    assert counts["silent"] == 1


def test_provisional_source_never_pins_even_with_streak() -> None:
    """Rail 5: a provisional source ranks but can never be pinned."""
    ranked = [_entry("SmallButHot", rank=1, n=25, provisional=True, wilson=0.90)]
    last = {"SmallButHot": _NOW - timedelta(hours=1)}
    prior = {
        "SmallButHot": {
            "consecutive_top_runs": PIN_MIN_CONSECUTIVE_RUNS + 5,
            "logical_status": "active",
        }
    }
    out, counts, _ = build_lifecycle_ranking(ranked, last, prior, _NOW)
    assert out[0]["pinned"] is False
    assert counts["pinned"] == 0


def test_validated_top_source_pins_after_streak() -> None:
    """Validated (n>=floor) + high Wilson + enough consecutive top runs → pinned."""
    ranked = [_entry("Anchor", rank=1, n=60, provisional=False, wilson=0.72)]
    last = {"Anchor": _NOW - timedelta(hours=1)}
    prior = {
        "Anchor": {"consecutive_top_runs": PIN_MIN_CONSECUTIVE_RUNS - 1, "logical_status": "active"}
    }
    out, counts, events = build_lifecycle_ranking(ranked, last, prior, _NOW)
    assert out[0]["pinned"] is True
    assert out[0]["consecutive_top_runs"] == PIN_MIN_CONSECUTIVE_RUNS
    assert out[0]["logical_status"] == "pinned"
    assert counts["pinned"] == 1
    assert [(e.from_status, e.to_status) for e in events] == [("active", "pinned")]


def test_pinned_going_silent_decomposes_into_two_events() -> None:
    ranked = [_entry("WasAnchor", rank=1, n=60, provisional=False, wilson=0.72)]
    last = {"WasAnchor": _NOW - timedelta(days=30)}  # now silent
    prior = {"WasAnchor": {"consecutive_top_runs": 9, "logical_status": "pinned"}}
    out, _, events = build_lifecycle_ranking(ranked, last, prior, _NOW)
    assert out[0]["pinned"] is False
    assert out[0]["silent"] is True
    assert [(e.from_status, e.to_status) for e in events] == [
        ("pinned", "active"),
        ("active", "silent"),
    ]


def test_rotation_flag_requires_min_n_when_not_silent() -> None:
    """A weak but recent source below the demote floor is not yet flagged."""
    ranked = [
        _entry("TooFresh", rank=1, n=10, provisional=True, wilson=0.20, reliability_tier="watch"),
        _entry("Judged", rank=2, n=25, provisional=True, wilson=0.20, reliability_tier="watch"),
    ]
    last = {"TooFresh": _NOW - timedelta(hours=1), "Judged": _NOW - timedelta(hours=1)}
    out, counts, _ = build_lifecycle_ranking(ranked, last, {}, _NOW)
    by_name = {e["source_name"]: e for e in out}
    assert by_name["TooFresh"]["rotation_flagged"] is False  # n < demote floor
    assert by_name["Judged"]["rotation_flagged"] is True
    assert counts["rotation_flagged"] == 1


# ── main() end-to-end ──────────────────────────────────────────────────────


def test_main_writes_ranking_and_audit(tmp_path: Path, monkeypatch) -> None:
    import scripts.source_lifecycle_recalc as mod

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monitor = tmp_path / "monitor"
    audit_rows = [
        {
            "document_id": "d1",
            "channel": "telegram",
            "message_id": "m",
            "is_digest": False,
            "dispatched_at": "2026-06-22T10:00:00+00:00",
            "source_name": "decrypt",
        },
        {
            "document_id": "d2",
            "channel": "telegram",
            "message_id": "m",
            "is_digest": False,
            "dispatched_at": "2026-06-22T11:00:00+00:00",
            "source_name": "decrypt",
        },
    ]
    (artifacts / "alert_audit.jsonl").write_text(
        "\n".join(json.dumps(r) for r in audit_rows) + "\n", encoding="utf-8"
    )
    (artifacts / "alert_outcomes.jsonl").write_text(
        "\n".join(
            json.dumps(
                {"document_id": doc, "outcome": oc, "annotated_at": "2026-06-22T12:00:00+00:00"}
            )
            for doc, oc in (("d1", "hit"), ("d2", "miss"))
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "_resolve_monitor_dir", lambda: monitor)

    rc = main()
    assert rc == 0

    payload = json.loads((monitor / "source_ranking.json").read_text(encoding="utf-8"))
    assert payload["report_type"] == "source_ranking"
    names = [e["source_name"] for e in payload["ranked"]]
    assert "decrypt" in names
    # Only 2 resolved → provisional, never pinned.
    decrypt = next(e for e in payload["ranked"] if e["source_name"] == "decrypt")
    assert decrypt["provisional"] is True
    assert decrypt["pinned"] is False


def test_main_missing_inputs_returns_1(tmp_path: Path, monkeypatch) -> None:
    import scripts.source_lifecycle_recalc as mod

    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)  # no artifacts/ dir
    assert main() == 1


def test_load_prior_ranking_tolerates_missing_and_corrupt(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    assert _load_prior_ranking(missing) == {}
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert _load_prior_ranking(corrupt) == {}
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"ranked": [{"source_name": "x", "consecutive_top_runs": 3}]}), encoding="utf-8"
    )
    assert _load_prior_ranking(good)["x"]["consecutive_top_runs"] == 3
