"""TL-004 darf nicht zum Daueralarm werden (Voll-Audit 2026-08-06, WP4).

Strukturgleich zu TL-008-legacy (test_truth_lint_tl008_frozen): das historische
Episoden-Maximum (Backlog-Batch 2026-07-06, ~150 Rows auf einem BTC-Move) lebt
in einer append-only-Datei ohne Rotations-Eintrag und ohne Zeitfenster im
Report — es kann nie wieder sinken. Als Dauer-WARNING färbt es den Truth-Lint
für immer DEGRADED und erzieht zum Ignorieren.

Der Diskriminator wird seit jeher berechnet (growth_since_last_run) und wird
jetzt statusfärbend — ergänzt um die Episoden-über-Schwelle-Zählung, damit
eine NEUE 41+-Episode UNTERHALB des alten Maximums nicht unsichtbar bleibt:

* Wachstum (Rows ODER neue Episoden über Schwelle) ⇒ WARNING.
* Kein Vorlauf mit Vergleichszählung ⇒ WARNING (fail-closed).
* Eingefrorener Altbestand ohne Neuzuwachs ⇒ INFO — Zitier-Auflage und alle
  Zahlen bleiben wörtlich in Message + Evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import app.observability.outcome_dedupe_report as dedupe_mod
from app.truth.lint import (
    _TL004_EPISODE_ROWS_MAX,
    LintContext,
    Severity,
    _check_cross_path_episode_inflation,
)


def _ctx(art: Path, *, prev_evidence: dict[str, Any] | None = None) -> LintContext:
    art.mkdir(parents=True, exist_ok=True)
    (art / "alert_outcomes.jsonl").write_text(
        json.dumps({"outcome": "hit", "document_id": "d1", "asset": "BTC/USDT"}) + "\n",
        encoding="utf-8",
    )
    if prev_evidence is not None:
        report_line = {"violations": [{"invariant_id": "TL-004", "evidence": prev_evidence}]}
        (art / "truth_lint_report.jsonl").write_text(
            json.dumps(report_line) + "\n", encoding="utf-8"
        )
    return LintContext(artifacts_dir=art)


def _fake_report(monkeypatch: pytest.MonkeyPatch, sizes: list[int]) -> None:
    fake = SimpleNamespace(
        largest_episode_size=max(sizes) if sizes else 0,
        episode_total=len(sizes),
        episode_sizes=tuple(sorted(sizes, reverse=True)),
    )
    monkeypatch.setattr(dedupe_mod, "build_episode_dedupe_report", lambda **kw: fake)


def test_threshold_is_a_named_constant() -> None:
    assert _TL004_EPISODE_ROWS_MAX == 40


def test_below_threshold_is_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_report(monkeypatch, [40, 12, 3])
    assert _check_cross_path_episode_inflation(_ctx(tmp_path)) == []


def test_growth_of_largest_episode_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_report(monkeypatch, [150, 10])
    ctx = _ctx(tmp_path, prev_evidence={"largest_episode_size": 120, "episodes_over_threshold": 1})
    violations = _check_cross_path_episode_inflation(ctx)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["growth_since_last_run"] == 30
    assert "episoden-dedupliziert" in v.message


def test_new_episode_over_threshold_below_old_max_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """largest bleibt 150 — aber eine NEUE 41er-Episode ist eine echte, frische
    Inflation und darf nicht als eingefroren durchgehen."""
    _fake_report(monkeypatch, [150, 41, 10])
    ctx = _ctx(tmp_path, prev_evidence={"largest_episode_size": 150, "episodes_over_threshold": 1})
    violations = _check_cross_path_episode_inflation(ctx)
    v = violations[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["new_episodes_over_threshold"] == 1


def test_frozen_historic_maximum_is_info_with_citation_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_report(monkeypatch, [150, 10])
    ctx = _ctx(tmp_path, prev_evidence={"largest_episode_size": 150, "episodes_over_threshold": 1})
    violations = _check_cross_path_episode_inflation(ctx)
    assert len(violations) == 1
    v = violations[0]
    assert v.severity is Severity.INFO
    # Sprachregel 2026-07-12 (bindend): aktiver Regel-Charakter, kein
    # "kann nicht mehr wachsen"; Zitier-Auflage bleibt wörtlich bestehen.
    assert "Erwarteter Neuzuwachs 0" in v.message
    assert "Regel bleibt aktiv" in v.message
    assert "kann nicht mehr wachsen" not in v.message
    assert "episoden-dedupliziert" in v.message
    # Nichts wird unterdrückt: alle Zahlen bleiben in der Evidence.
    assert v.evidence["largest_episode_size"] == 150
    assert v.evidence["episodes_over_threshold"] == 1
    assert v.evidence["threshold_rows"] == _TL004_EPISODE_ROWS_MAX


def test_missing_previous_run_stays_fail_closed_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_report(monkeypatch, [150, 10])
    violations = _check_cross_path_episode_inflation(_ctx(tmp_path))
    v = violations[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["growth_since_last_run"] is None
    assert "fail-closed" in v.message


def test_previous_run_without_over_count_stays_fail_closed_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alt-Reports vor der Zweiteilung kennen nur largest — ohne
    Vergleichszählung keine INFO-Einstufung (erst der Folgelauf friert ein)."""
    _fake_report(monkeypatch, [150, 10])
    ctx = _ctx(tmp_path, prev_evidence={"largest_episode_size": 150})
    v = _check_cross_path_episode_inflation(ctx)[0]
    assert v.severity is Severity.WARNING
    assert v.evidence["new_episodes_over_threshold"] is None
