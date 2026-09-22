"""Ratchet: ``.env.example`` gegen die Settings-Klassen (System-Audit 16.09., P1-20).

Verhalten, nicht Implementierung: keine NEUE Luecke zwischen Settings und
Vorlage; die Baseline traegt keine veralteten Eintraege (sonst ``--update``);
Secrets stehen in der Vorlage nur leer; die im Audit belegte Luecke
``TRADINGVIEW_WEBHOOK_SHARED_TOKEN`` bleibt geschlossen.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import env_example_drift as drift  # noqa: E402


def test_ableitung_kennt_prefix_alias_und_verschachtelung() -> None:
    expected = drift.expected_env_names()
    # Prefix + Feld
    origin = expected["TRADINGVIEW_WEBHOOK_SHARED_TOKEN"]
    assert origin == "TradingViewSettings.webhook_shared_token"
    # AliasChoices: beide Schreibweisen gelten
    assert "APP_CF_ACCESS_ALLOWED_EMAILS" in expected
    assert "CF_ACCESS_ALLOWED_EMAILS" in expected
    # Verschachtelte Settings sind keine Env-Namen
    assert "APP_ALERTS" not in expected
    assert "APP_DB" not in expected


def test_vorlage_parser_zaehlt_aktive_und_auskommentierte_zeilen(tmp_path: Path) -> None:
    example = tmp_path / ".env.example"
    example.write_text(
        "# Prosa mit Beispiel: FOO_BAR=1 zaehlt nicht\n"
        "AKTIV=wert\n"
        "# KOMMENTIERT=\n"
        "#  MIT_ABSTAND=x\n"
        "leer_klein=nein\n",
        encoding="utf-8",
    )
    entries = drift.example_entries(example)
    assert entries == {
        "AKTIV": (True, "wert"),
        "KOMMENTIERT": (False, ""),
        "MIT_ABSTAND": (False, "x"),
    }


def test_keine_neue_luecke_gegenueber_der_baseline() -> None:
    missing = set(drift.missing_names())
    baseline = drift.load_baseline()
    new = sorted(missing - baseline)
    assert new == [], (
        "Neue Settings-Schluessel ohne Eintrag in .env.example: "
        f"{new}. Entweder in .env.example dokumentieren (aktiv oder als '# NAME=') "
        "oder bewusst in die Baseline aufnehmen: python scripts/env_example_drift.py --update"
    )


def test_baseline_traegt_keine_veralteten_eintraege() -> None:
    missing = set(drift.missing_names())
    baseline = drift.load_baseline()
    stale = sorted(baseline - missing)
    assert stale == [], (
        f"Baseline-Eintraege, die keine Luecke mehr sind: {stale} — "
        "python scripts/env_example_drift.py --update (Ratchet schrumpft)"
    )


def test_secrets_stehen_in_der_vorlage_nur_leer() -> None:
    entries = drift.example_entries()
    offenders = sorted(
        name
        for name in drift.secret_env_names()
        if name in entries and entries[name][0] and entries[name][1] != ""
    )
    assert offenders == [], f"Secret-Schluessel mit Wert in .env.example: {offenders}"


def test_audit_luecke_shared_token_bleibt_geschlossen() -> None:
    entries = drift.example_entries()
    assert "TRADINGVIEW_WEBHOOK_SHARED_TOKEN" in entries
    assert entries["TRADINGVIEW_WEBHOOK_SHARED_TOKEN"] == (True, "")
