"""Write-on-change für alert_outcomes.jsonl (Quoten-Sprint W1).

Befund Quoten-Audit 2026-07-29: 9,15 Zeilen pro Dokument, 85,1 % aller Zeilen im
obersten Dezil, ein Dokument mit 840 Zeilen. Ursache: der Auto-Annotator schreibt
bei JEDEM Lauf eine Zeile — auch wenn der Outcome unverändert bleibt.

Der Fix darf den Terminal-Cap nicht beschädigen: ``_MAX_INCONCLUSIVE_REEVAL_ATTEMPTS``
zählt heute genau diese Wiederholungszeilen. Wird das Schreiben ersatzlos
unterdrückt, erreicht der Zähler nie 3 → Endlos-Re-Eval + unbegrenzte CoinGecko-
Calls. Deshalb steht der Cap-Regressionstest hier gleichberechtigt neben dem
Sparsamkeits-Test.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.alerts.audit import (
    ALERT_AUDIT_JSONL_FILENAME,
    ALERT_OUTCOMES_JSONL_FILENAME,
    AlertAuditRecord,
    append_alert_audit,
)
from app.alerts.auto_annotator import auto_annotate_pending


def _make_audit(
    doc_id: str,
    *,
    hours_ago: float,
    sentiment: str = "bullish",
    directional_confidence: float | None = None,
    priority: int | None = None,
) -> AlertAuditRecord:
    ts = datetime.now(UTC) - timedelta(hours=hours_ago)
    return AlertAuditRecord(
        document_id=doc_id,
        channel="telegram",
        message_id="dry_run",
        is_digest=False,
        dispatched_at=ts.isoformat(),
        sentiment_label=sentiment,
        affected_assets=["BTC/USDT"],
        directional_eligible=True,
        directional_confidence=directional_confidence,
        priority=priority,
    )


def _seed_outcomes(path: Path, doc_id: str, count: int, hours_ago: float = 20.0) -> None:
    """Schreibt ``count`` inconclusive-Zeilen für ein Dokument."""
    lines = []
    for i in range(count):
        lines.append(
            json.dumps(
                {
                    "document_id": doc_id,
                    "outcome": "inconclusive",
                    "annotated_at": (
                        datetime.now(UTC) - timedelta(hours=hours_ago + i)
                    ).isoformat(),
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len([ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()])


def _last_record(path: Path) -> dict:
    rows = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return json.loads(rows[-1])


def _patched_adapter(pct: float, start: float = 65000.0, end: float = 65100.0):
    """CoinGecko-Adapter, der eine feste Preisänderung liefert."""
    ctx = patch("app.alerts.auto_annotator.CoinGeckoAdapter")
    mock_cls = ctx.__enter__()
    adapter = mock_cls.return_value
    adapter.get_ticker = AsyncMock(return_value=None)
    adapter.get_price_change_between = AsyncMock(return_value=(start, end, pct))
    return ctx


async def test_unchanged_inconclusive_appends_no_second_line(tmp_path: Path) -> None:
    """Re-Eval bestätigt inconclusive → keine neue Zeile (das ist die Amplifikation)."""
    append_alert_audit(
        _make_audit("stable-doc", hours_ago=30.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    _seed_outcomes(outcomes, "stable-doc", count=1)
    before = _line_count(outcomes)

    ctx = _patched_adapter(pct=0.15)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert len(results) == 1
    assert results[0].outcome == "inconclusive"
    assert _line_count(outcomes) == before, "unveränderter Outcome darf keine zweite Zeile erzeugen"


async def test_changed_outcome_still_appends(tmp_path: Path) -> None:
    """inconclusive → hit ist eine echte Zustandsänderung und MUSS geschrieben werden."""
    append_alert_audit(
        _make_audit("flip-doc", hours_ago=30.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    _seed_outcomes(outcomes, "flip-doc", count=1)
    before = _line_count(outcomes)

    ctx = _patched_adapter(pct=3.1, end=67000.0)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert results[0].outcome == "hit"
    assert _line_count(outcomes) == before + 1


async def test_transition_to_hit_carries_resolved_at(tmp_path: Path) -> None:
    """Auflösung nach hit/miss setzt resolved_at — sonst ist die Kadenz unmessbar."""
    append_alert_audit(
        _make_audit("resolve-doc", hours_ago=30.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    _seed_outcomes(outcomes, "resolve-doc", count=1)

    ctx = _patched_adapter(pct=3.1, end=67000.0)
    try:
        await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    rec = _last_record(outcomes)
    assert rec["outcome"] == "hit"
    assert rec.get("resolved_at"), "hit/miss-Zeile muss resolved_at tragen"


async def test_inconclusive_carries_no_resolved_at(tmp_path: Path) -> None:
    """inconclusive ist keine Auflösung — resolved_at bleibt leer."""
    append_alert_audit(
        _make_audit("open-doc", hours_ago=30.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME

    ctx = _patched_adapter(pct=0.15)
    try:
        await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    rec = _last_record(outcomes)
    assert rec["outcome"] == "inconclusive"
    assert "resolved_at" not in rec


async def test_confidence_and_priority_are_propagated(tmp_path: Path) -> None:
    """Beide Felder existieren im AlertAuditRecord und gehören in die Outcome-Zeile."""
    append_alert_audit(
        _make_audit("conf-doc", hours_ago=30.0, directional_confidence=0.75, priority=8),
        tmp_path / ALERT_AUDIT_JSONL_FILENAME,
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME

    ctx = _patched_adapter(pct=3.1, end=67000.0)
    try:
        await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    rec = _last_record(outcomes)
    assert rec.get("directional_confidence") == 0.75
    assert rec.get("priority") == 8


async def test_reeval_cap_still_terminates_with_write_on_change(tmp_path: Path) -> None:
    """REGRESSIONSSCHUTZ: der Terminal-Cap muss trotz gesparter Zeilen greifen.

    Naives write-on-change würde ``inconclusive_attempts`` nie auf 3 bringen →
    Endlos-Re-Eval. Ein vollständig abgelaufenes Dokument mit erreichtem Cap darf
    NICHT erneut evaluiert werden.
    """
    append_alert_audit(
        _make_audit("capped-doc", hours_ago=200.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    _seed_outcomes(outcomes, "capped-doc", count=3, hours_ago=30.0)
    before = _line_count(outcomes)

    ctx = _patched_adapter(pct=0.15)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert results == [], "Cap erreicht → kein Re-Queue mehr"
    assert _line_count(outcomes) == before


async def test_reeval_attempt_counter_persists_for_capping(tmp_path: Path) -> None:
    """Der Cap braucht eine Zählbasis, die write-on-change überlebt.

    Ein vollständig abgelaufenes Dokument wird weiterhin bestätigend geschrieben
    (cap-relevant) und trägt einen expliziten Versuchszähler, damit der Cap nicht
    auf die Anzahl roher Zeilen angewiesen bleibt.
    """
    append_alert_audit(
        _make_audit("elapsed-doc", hours_ago=200.0), tmp_path / ALERT_AUDIT_JSONL_FILENAME
    )
    outcomes = tmp_path / ALERT_OUTCOMES_JSONL_FILENAME
    _seed_outcomes(outcomes, "elapsed-doc", count=1, hours_ago=30.0)

    ctx = _patched_adapter(pct=0.15)
    try:
        results = await auto_annotate_pending(tmp_path, min_age_hours=4)
    finally:
        ctx.__exit__(None, None, None)

    assert len(results) == 1
    rec = _last_record(outcomes)
    assert rec["outcome"] == "inconclusive"
    assert rec.get("reeval_attempt", 0) >= 2, "Versuchszähler muss fortgeschrieben werden"
