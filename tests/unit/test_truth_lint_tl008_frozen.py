"""TL-008 darf nicht zum Daueralarm werden (Befund 2026-08-02).

Die Wurzel ist seit #592 (`90d540da`, gemerged 2026-07-11T11:48:19Z) geschlossen:
der RSS-Pfad setzt seitdem eine stabile ``signal_path_id``. Die 10 Bestands-Rows
davor werden bewusst NICHT backfilled (``app/alerts/service.py``: „kein
erfundener Beweis"). Live gemessen ist die jüngste betroffene Zeile
``2026-07-11T06:27:09`` — also vor dem Fix.

Ohne Trennung bleibt TL-008 damit auf ewig WARNING und der Truth-Lint dauerhaft
DEGRADED. Ein Alarm, der nichts mehr auslösen kann, erzieht dazu, den Lint zu
ignorieren. Darum: Rows NACH dem Fix sind eine echte Verletzung (WARNING), Rows
davor ein sichtbarer, eingefrorener Bestand (INFO — geht in den Digest, nicht in
den Status).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.truth.lint import (
    RSS_SIGNAL_PATH_FIX_UTC,
    LintContext,
    Severity,
    _check_missing_provenance,
)

PRE_FIX = "2026-07-11T06:27:09.883855+00:00"
POST_FIX = "2026-07-20T09:00:00+00:00"


def _write(art: Path, rows: list[dict]) -> LintContext:
    art.mkdir(parents=True, exist_ok=True)
    (art / "alert_outcomes.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    return LintContext(artifacts_dir=art)


def _row(annotated_at: str, *, path_id: str | None = None, asset: str = "BTC/USDT") -> dict:
    prov: dict = {"source": "wu_blockchain", "version": "rss-1"}
    if path_id:
        prov["signal_path_id"] = path_id
    return {"outcome": "hit", "asset": asset, "annotated_at": annotated_at, "provenance": prov}


def test_fix_cutoff_is_the_merge_of_the_root_cause_fix() -> None:
    assert RSS_SIGNAL_PATH_FIX_UTC == "2026-07-11T11:48:19+00:00"


def test_only_legacy_rows_report_as_frozen_info_not_warning(tmp_path: Path) -> None:
    ctx = _write(tmp_path, [_row(PRE_FIX), _row("2026-07-02T08:56:24+00:00")])

    violations = _check_missing_provenance(ctx)

    assert len(violations) == 1
    v = violations[0]
    assert v.severity is Severity.INFO
    assert v.evidence["legacy_count"] == 2
    assert v.evidence["post_fix_count"] == 0
    # Sprachregel 2026-07-12 (bindend): NICHT "kann nicht mehr wachsen", sondern
    # erwarteter Neuzuwachs 0 bei weiterhin AKTIVER Regel.
    assert "erwarteter Neuzuwachs 0" in v.message
    assert "Regel bleibt aktiv" in v.message
    assert "kann nicht mehr wachsen" not in v.message


def test_a_row_after_the_fix_is_a_real_violation(tmp_path: Path) -> None:
    ctx = _write(tmp_path, [_row(POST_FIX)])

    violations = _check_missing_provenance(ctx)

    assert len(violations) == 1
    v = violations[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["post_fix_count"] == 1
    assert v.evidence["legacy_count"] == 0


def test_mixed_reports_warning_and_keeps_both_counts_visible(tmp_path: Path) -> None:
    ctx = _write(tmp_path, [_row(PRE_FIX), _row(PRE_FIX), _row(POST_FIX)])

    violations = _check_missing_provenance(ctx)

    v = violations[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["legacy_count"] == 2
    assert v.evidence["post_fix_count"] == 1
    assert v.evidence["count"] == 3


def test_rows_with_a_path_id_are_clean(tmp_path: Path) -> None:
    ctx = _write(tmp_path, [_row(POST_FIX, path_id="rsspath_news_v1")])

    assert _check_missing_provenance(ctx) == []


def test_unparseable_timestamp_stays_fail_closed_as_a_real_violation(tmp_path: Path) -> None:
    """Unlesbare Zeit darf sich nicht in den eingefrorenen Bestand retten."""
    ctx = _write(tmp_path, [_row("not-a-timestamp")])

    violations = _check_missing_provenance(ctx)

    assert violations[0].severity is Severity.WARNING
    assert violations[0].evidence["post_fix_count"] == 1
