r"""Die Phantom-Schwelle stand doppelt im Code und ist auseinandergedriftet.

Befund 2026-08-18. Der Phantom-Close-Breaker existiert zweimal:

    Schreibpfad  app/execution/paper_engine.py     -- weist den Close ab
    Lesepfad     app/execution/phantom_filter.py   -- bereinigt Aggregate

``phantom_filter`` trug im Docstring die Zusage, die Schwelle "mirrors the
engine's MAX_CLOSE_RETURN_PCT". Als #722 den Motor von 200 % auf gemessene
20 % kalibrierte, blieb diese Kopie auf **2.0** stehen. Damit lief genau der
Pfad, der die Vergangenheit korrigieren soll, weiter mit der alten Marke.

Folge, live gemessen: die beiden ETH-Closes vom 11./12.08. mit dem
byte-identischen Exit ``3225.6863500000004`` (+72,11 % / +71,54 %, zusammen
+2.255,58 USD) blieben als *realisierter* Gewinn stehen und hielten das Buch
der Epoche bei **+396,73** statt **-1.853,45 USD**.

Die Kette, die davon haengt:

    portfolio_read.compute_realized_by_asset
      -> bayes_quarantine.is_corrupt_close
        -> phantom_filter.is_phantom_close   <-- hier stand 2.0

Eine Zahl repariert Dashboard, Lernen und Aggregate zugleich. Dieser Test
sorgt dafuer, dass sie nur noch EINMAL existiert -- und dass eine zu weite
Einstellung nicht wieder gruen durch die CI kommt.

Zur Wirkungsrichtung: der Lesepfad LOESCHT nichts. Ein Phantom wandert nach
``quarantined_pnl_usd`` und bleibt sichtbar. Ein Fehlalarm kostet hier keine
Information, nur eine Umbuchung -- deshalb ist die scharfe Schwelle auf diesem
Pfad ohne Kollateralschaden zu haben.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.execution.paper_engine import _max_close_return_pct
from app.execution.phantom_filter import is_phantom_close, phantom_return_threshold
from app.learning.verified_real_closes import VERIFIED_REAL_CLOSES

# Realvorfaelle aus artifacts/paper_execution_audit.jsonl (Pi, 2026-08-18).
# (Name, entry, exit, side, gemessener implied return)
KNOWN_ARTIFACTS: tuple[tuple[str, float, float, str], ...] = (
    # Der Fall, gegen den die 200 % ueberhaupt gesetzt wurden: delistetes
    # BitMEX-MATIC-Instrument, 0.40875 statt real ~0.088.
    ("MATIC 2026-05-28 +364,80 %", 0.087897927, 0.408545625, "long"),
    # +96,85 % -- lag UNTER 200 % und kam deshalb ungepruft durch.
    ("SOL 2026-07-08 +96,85 %", 100.0, 196.85, "long"),
    # -92,11 %: die -3.791,75 USD aus EINEM Trade.
    ("MKR 2026-07-09 -92,11 %", 100.0, 7.89, "long"),
    # Die zwei, die das Buch der Epoche gedreht haben. Byte-identischer Exit
    # an zwei verschiedenen Tagen -- kein Live-Feed kann das.
    ("ETH 2026-08-11 +72,11 %", 1874.24956227636, 3225.6863500000004, "long"),
    ("ETH 2026-08-12 +71,54 %", 1880.409735, 3225.6863500000004, "long"),
    ("ETH 2026-05-26 +55,21 %", 2100.0, 3259.9692, "long"),
    ("SOL 2026-08-12 -50,24 %", 100.0, 49.76, "long"),
)

# 2026-08-18 widerlegt: diese drei galten bei der Kalibrierung als Artefakt, sind
# aber BELEGT echt (Roh-Preis in der 1h-Kerze der Schliessungsstunde, Micro-Caps
# mit 17-30 h Haltedauer). Sie stehen in bayes_quarantine.VERIFIED_REAL_CLOSES und
# werden dort freigesprochen. Der Cap sieht sie weiterhin -- das ist Absicht:
# er bleibt scharf, der Freispruch ist die belegte Ausnahme.
# Die Entry-Preise der registrierten Ausnahmen (aus dem Audit-Stream). Alles
# uebrige — Identitaet, Symbol, Zeit, Exit — kommt aus VERIFIED_REAL_CLOSES, damit
# der Test nicht neben der Registrierung driftet.
_ENTRY_BY_FILL_ID: dict[str, float] = {
    "fill_fbd5580fab5c": 1.0011002999999998,  # CYS 2026-08-11 +38,82 %
    "fill_f83be51981e1": 0.38524252499999995,  # SLX 2026-06-27 +28,19 %
    "fill_446f84adb9e4": 1.7780761336479318,  # VELVET 2026-06-29 -21,18 %
}

# Gemessene Gegenseite: die groessten Closes, die KEIN Artefakt sind. Sie
# beweisen, dass die Schaerfung nichts Legitimes einfaengt.
MEASURED_LEGITIMATE: tuple[tuple[str, float, float, str], ...] = (
    ("groesster unverdaechtiger Close +17,16 %", 100.0, 117.16, "long"),
    ("ON 2026-05-09 +14,90 %", 100.0, 114.90, "long"),
    ("schlimmster legitimer Stop -14,80 %", 100.0, 85.20, "long"),
    ("p95 der Verteilung +7,70 %", 100.0, 107.70, "long"),
    ("Median der Verteilung +1,52 %", 100.0, 101.52, "long"),
    # Short-Seite spiegelbildlich pruefen, damit die Vorzeichenlogik mitgetestet
    # wird und nicht nur der Long-Fall.
    ("Short-Gewinn +17,16 %", 117.16, 100.0, "short"),
)


def test_write_and_read_path_share_one_threshold() -> None:
    """Die Zahl darf nur EINMAL existieren.

    Genau diese Drift hat die ETH-Artefakte im Buch gehalten: Motor 0.20,
    Lese-Seite 2.0.
    """
    assert _max_close_return_pct() == phantom_return_threshold()


def test_env_override_moves_both_paths_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auch ueber ``MAX_CLOSE_RETURN_PCT`` duerfen die Pfade nicht auseinanderlaufen."""
    for raw in ("0.05", "0.5", "3.0"):
        monkeypatch.setenv("MAX_CLOSE_RETURN_PCT", raw)
        assert _max_close_return_pct() == phantom_return_threshold() == float(raw)


def test_threshold_is_calibrated_not_guessed() -> None:
    """Die Schwelle muss oberhalb des groessten legitimen Closes (17,16 %) und
    unterhalb des kleinsten Artefakts (21,18 %) liegen.

    Das ist der eigentliche Waechter gegen den Fehler von #722: eine Schwelle,
    die gegen einen EINZELNEN Fall gesetzt wird, verletzt dieses Fenster.
    """
    cap = phantom_return_threshold()
    assert 0.1716 < cap < 0.2118, (
        f"Schwelle {cap} liegt ausserhalb des gemessenen Fensters "
        "(groesster legitimer Close 17,16 %, kleinstes Artefakt 21,18 %)"
    )


@pytest.mark.parametrize(("name", "entry", "exit_", "side"), KNOWN_ARTIFACTS)
def test_every_known_artifact_is_caught(name: str, entry: float, exit_: float, side: str) -> None:
    """Korpus echter Vorfaelle. Wird die Schwelle je wieder aufgeweitet, geht
    dieser Test rot -- und zwar mit dem Namen des konkreten Vorfalls, der dann
    wieder durchkaeme."""
    assert is_phantom_close(entry, exit_, side), f"{name} wuerde wieder gebucht"


@pytest.mark.parametrize(("name", "entry", "exit_", "side"), MEASURED_LEGITIMATE)
def test_no_legitimate_close_is_caught(name: str, entry: float, exit_: float, side: str) -> None:
    """Gegenprobe: die Schaerfung darf keinen gemessenen Realtrade einfangen.

    Ohne diesen Test waere die Verscharfung eine Einschraenkung statt einer
    Praezisierung.
    """
    assert not is_phantom_close(entry, exit_, side), f"{name} faelschlich als Phantom"


def test_unverifiable_close_is_never_dropped() -> None:
    """Fehlende/nicht-numerische Preise bleiben im Buch -- lieber ein
    unbelegter Close als ein stillschweigend verschwundener."""
    assert not is_phantom_close(None, 3225.68, "long")
    assert not is_phantom_close(1874.25, None, "long")
    assert not is_phantom_close("n/a", "n/a", "long")
    assert not is_phantom_close(0.0, 3225.68, "long")


def test_the_two_eth_artifacts_leave_realized_pnl(tmp_path: Path) -> None:
    """End-to-end am Realfall: nach dem Fix tragen die beiden ETH-Closes das
    Buch nicht mehr.

    Vorher wies der Aggregator ihre +2.255,58 USD als *realisierten* Gewinn
    aus, weil +72 % unter der 200-%-Marke lagen.
    """
    from app.execution.portfolio_read import compute_realized_by_asset

    def close(symbol: str, entry: float, exit_: float, pnl: float, ts: str) -> dict:
        return {
            "schema_version": "v2",
            "event_type": "position_closed",
            "timestamp_utc": ts,
            "symbol": symbol,
            "quantity": 1.0,
            "entry_price": entry,
            "exit_price": exit_,
            "position_side": "long",
            "trade_pnl_usd": pnl,
            "fee_usd": 0.0,
        }

    path = tmp_path / "audit.jsonl"
    rows = [
        # ein gewoehnlicher, legitimer Close als Kontrolle
        close("ETH/USDT", 1882.72, 1905.91, -29.12, "2026-08-17T02:53:00+00:00"),
        close(
            "ETH/USDT",
            1874.24956227636,
            3225.6863500000004,
            1491.190919803811,
            "2026-08-11T23:09:58+00:00",
        ),
        close(
            "ETH/USDT",
            1880.409735,
            3225.6863500000004,
            764.39,
            "2026-08-12T23:06:34+00:00",
        ),
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    result = compute_realized_by_asset(path)
    eth = {b["symbol"]: b for b in result["by_asset"]}["ETH/USDT"]

    # Der Scheingewinn ist raus aus "realisiert" ...
    assert eth["realized_pnl_usd"] == pytest.approx(-29.12)
    assert eth["closed_trades"] == 1
    # ... aber NICHT verschwunden: er steht offen in der Quarantaene.
    assert eth["quarantined_closes"] == 2
    assert eth["quarantined_pnl_usd"] == pytest.approx(1491.190919803811 + 764.39)


@pytest.mark.parametrize("record", VERIFIED_REAL_CLOSES, ids=lambda r: r.symbol)
def test_verified_real_closes_are_acquitted(record) -> None:
    """Der Cap sieht sie — das Gesamturteil spricht sie trotzdem frei.

    Ohne diesen Freispruch quarantaeniert der Lesepfad drei belegte echte Trades
    (netto +39,52 USD) und verzerrt damit genau die Buch-Wahrheit, die er
    schuetzen soll. Stand 2026-08-18 sind das die EINZIGEN Closes, die der
    generische Cap noch allein faengt.

    Der Freispruch haengt seit 2026-08-19 an der Ereignis-ID: eine Zeile OHNE
    ``fill_id`` wird nicht mehr erkannt, damit kein kuenftiger Trade mit
    aehnlichem Exit-Preis den historischen Freispruch erbt.
    """
    from app.learning.bayes_quarantine import corruption_reason

    entry = _ENTRY_BY_FILL_ID[record.fill_id]
    row = {
        "event_type": "position_closed",
        "symbol": record.symbol,
        "timestamp_utc": record.timestamp_utc,
        "fill_id": record.fill_id,
        "order_id": record.order_id,
        "entry_price": entry,
        "exit_price": record.exit_price,
        "position_side": "long",
    }
    assert is_phantom_close(entry, record.exit_price, "long"), (
        f"{record.symbol}: Cap sollte ihn sehen"
    )
    assert corruption_reason(row) is None, (
        f"{record.symbol}: belegt echt, darf nicht quarantaeniert werden"
    )


def test_ohne_ereignis_id_kein_freispruch() -> None:
    """Dieselben Preise, aber ohne Identitaet — der Cap greift wieder."""
    from app.learning.bayes_quarantine import corruption_reason

    record = VERIFIED_REAL_CLOSES[0]
    row = {
        "event_type": "position_closed",
        "symbol": record.symbol,
        "entry_price": _ENTRY_BY_FILL_ID[record.fill_id],
        "exit_price": record.exit_price,
        "position_side": "long",
    }
    assert corruption_reason(row) == "phantom_implied_return"


def test_acquittal_never_overrides_an_exact_signature() -> None:
    """Ein Freispruch darf nur die Heuristik schlagen, nie einen benannten Vorfall."""
    from app.learning.bayes_quarantine import corruption_reason

    row = {
        "event_type": "position_closed",
        "symbol": "ETH/USDT",
        "entry_price": 1874.24956227636,
        "exit_price": 3225.6863500000004,
        "position_side": "long",
    }
    assert corruption_reason(row) == "mock_synthetic_exit_price"
