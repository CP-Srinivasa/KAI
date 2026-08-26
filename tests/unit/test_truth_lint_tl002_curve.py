"""TL-002 V3: die Mock-Kurve statt eines Preisbands.

Das alte Band ``[95,105]`` lag in beide Richtungen falsch, live gemessen über
3091 Fill-/Close-Preise: 30 Falsch-Positive (reale Assets handeln dort) und
**20 übersehene** Mock-Preise — darunter der gesamte Vorfall vom 11./12.08.
(ETH-Exits bei 3225,68635), also genau die Klasse, für die TL-002 existiert.
Der Mock preist je Symbol verschieden: ETH 3200, BTC 65000, SOL 150.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.truth.lint import run_lint


def _artifacts(tmp_path: Path) -> Path:
    art = tmp_path / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    return art


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def _tl002(art: Path) -> dict | None:
    hits = [v for v in run_lint(art)["violations"] if v["invariant_id"] == "TL-002"]
    return hits[0] if hits else None


# --- Der Fall, den das Band strukturell nicht sehen konnte ----------------------


def test_eth_mock_exit_wird_erkannt(tmp_path: Path) -> None:
    """3225,68635 = mock(ETH, phase 101) x (1 - 0,0005) — weit außerhalb [95,105]."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_eth",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            }
        ],
    )
    v = _tl002(art)
    assert v is not None, "der ETH-Mock-Preis muss gemeldet werden"
    assert v["evidence"]["count"] == 1
    assert v["evidence"]["strong_evidence"] == 1
    assert "ETH/USDT" in v["evidence"]["per_symbol"]


def test_btc_mock_preis_wird_erkannt(tmp_path: Path) -> None:
    """Mock-BTC liegt bei 65000 — das Band um 100 war dort blind."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_btc",
                "symbol": "BTC/USDT",
                "fill_price": 65485.61081500001,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    assert _tl002(art) is not None


# --- Die Falsch-Positive, die das Band erzeugte --------------------------------


def test_realer_eth_preis_ist_kein_verdacht(tmp_path: Path) -> None:
    """ETH handelte im Juli bei ~1874 — das Band meldete jeden Preis um 100.

    Die Mock-ETH-Kurve liegt bei 3200 ± 2 %; ein Preis um 1874 kann von dort
    nicht stammen. ETH ist die Stufe "strong", der Test ist also scharf.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_sol",
                "symbol": "ETH/USDT",
                "fill_price": 1874.24,
                "timestamp_utc": "2026-08-26T08:04:39+00:00",
            }
        ],
    )
    assert _tl002(art) is None


def test_preis_unter_dem_basispreis_ist_nie_mock(tmp_path: Path) -> None:
    """``phase % 360`` deckt nur die erste Viertelwelle ab: sin(t) in [0,1).

    Der Mock kann deshalb NUR Werte >= Basispreis erzeugen — die untere Hälfte
    des alten Bands [95,100) war strukturell unmöglich.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_low",
                "symbol": "AAVE/USDT",
                "fill_price": 98.77,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    assert _tl002(art) is None


# --- Evidenzstärke ---------------------------------------------------------------


def test_price_source_mock_ist_der_staerkste_beleg(tmp_path: Path) -> None:
    """Seit #737 sagt das Fill DIREKT, woher der Preis kam — kein Join nötig."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_src",
                "symbol": "FOO/USDT",
                "fill_price": 4242.4242,  # NICHT auf der Kurve
                "price_source": "mock|synthetic_not_tradeable",
                "timestamp_utc": "2026-08-20T10:00:00+00:00",
            }
        ],
    )
    v = _tl002(art)
    assert v is not None, "eine belegte Mock-Quelle muss immer melden"
    assert v["evidence"]["proven_by_price_source"] == 1
    assert v["evidence"]["strong_evidence"] == 1


def test_roher_kurventreffer_ist_nur_schwacher_verdacht(tmp_path: Path) -> None:
    """Ohne Slippage kann ein runder echter Preis zufällig treffen."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_raw",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,  # roher Kurvenwert, keine Slippage
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["raw_curve_only"] == 1
    assert v["evidence"]["strong_evidence"] == 0


def test_reale_quelle_entlastet_nur_den_schwachen_treffer(tmp_path: Path) -> None:
    """Ein Slippage-Treffer lässt sich davon NICHT aufweichen."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {  # roh + reale Quelle -> entlastet
                "event_type": "order_filled",
                "order_id": "ord_weak",
                "symbol": "BNB/USDT",
                "fill_price": 403.66,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
            {  # Slippage-Treffer + dieselbe reale Quelle -> bleibt Verletzung
                "event_type": "order_filled",
                "order_id": "ord_strong",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            },
        ],
    )
    _write(
        art / "trading_loop_audit.jsonl",
        [
            {"order_id": "ord_weak", "notes": ["market_data_source:bybit"]},
            {"order_id": "ord_strong", "notes": ["market_data_source:bybit"]},
        ],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["count"] == 1
    assert "ETH/USDT" in v["evidence"]["per_symbol"]
    assert "BNB/USDT" not in v["evidence"]["per_symbol"]
    assert v["evidence"]["real_source_excluded"] == 1


# --- Bekanntes vs. Neues ---------------------------------------------------------


def test_quarantaenierter_close_erzeugt_keinen_neuen_alarm(tmp_path: Path) -> None:
    """Ein Wächter soll melden, was NEU ist — sonst ertränkt der bekannte
    Bestand den Befund. Der Fall bleibt in der Evidenz sichtbar."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "position_closed",
                "symbol": "ETH/USDT",
                "entry_price": 1874.24956227636,
                "exit_price": 3225.6863500000004,
                "position_side": "long",
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            }
        ],
    )
    v = _tl002(art)
    assert v is None, "bereits quarantaeniert — kein neuer Alarm"


def test_close_ausserhalb_der_quarantaene_meldet(tmp_path: Path) -> None:
    """Gegenprobe: derselbe Preis auf einem Symbol ohne Signatur meldet sehr wohl."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_x",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            }
        ],
    )
    assert _tl002(art) is not None


# --- Die Grenze des Verfahrens ---------------------------------------------------


def test_lueckenlose_kurve_meldet_nicht(tmp_path: Path) -> None:
    """Der wichtigste Test dieser Datei: wo die Kurve NICHTS unterscheidet.

    Symbole ohne eigenen Mock-Basispreis laufen auf den Default 100. Die Kurve
    deckt dort [100,01 … 101,99] in Schritten von exakt 0,0100 ab — also jeden
    auf zwei Nachkommastellen quotierten Preis des Bandes, lueckenlos. Ein
    bit-exakter Treffer beweist dann genau nichts.

    Live belegt am 26.08. gegen die Pi-Artefakte: fuenf AAVE-Fills (100,31 bis
    101,72) rechneten bit-exakt auf (``100.83 x 1.0005 = 100.880415``) und haetten
    als STARKE Evidenz gemeldet. Dass AAVE in dieser Preislage ECHT handelt, ist
    zweimal sichtgeprueft (12.07. und 02.08., 5 Fills zwischen 97,02 und 99,89
    gegen Binance-Kerzen, im Kommentar von ``_check_mock_curve_prices``
    festgehalten). Ein Waechter, der hier meldet, wiederholt den Fehler des alten
    Bands mit mehr Nachkommastellen.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_aave",
                "symbol": "AAVE/USDT",
                "fill_price": 100.880415,  # = 100.83 * 1.0005, bit-exakt
                "timestamp_utc": "2026-07-26T23:39:28+00:00",
            }
        ],
    )
    assert _tl002(art) is None, "lueckenlose Kurve darf keinen Alarm erzeugen"


def test_lueckenlose_kurve_meldet_trotzdem_bei_belegter_mock_quelle(
    tmp_path: Path,
) -> None:
    """Gegenprobe: die Blindstelle betrifft NUR die Kurve.

    Sagt das Fill selbst ``price_source: mock``, ist die Herkunft bewiesen und
    der Basispreis des Symbols voellig gleichgueltig. Genau deshalb ist die
    Provenienz-Schicht (#737) fuer diese Symbolklasse der einzige Weg.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_aave_mock",
                "symbol": "AAVE/USDT",
                "fill_price": 100.880415,
                "price_source": "mock|synthetic_not_tradeable",
                "timestamp_utc": "2026-07-26T23:39:28+00:00",
            }
        ],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["proven_by_price_source"] == 1
    assert v["evidence"]["curve_not_discriminating"] == 1


def test_blindstelle_bleibt_in_der_evidenz_sichtbar(tmp_path: Path) -> None:
    """Eine unterdrueckte Meldung darf nicht spurlos verschwinden.

    Sonst sieht der naechste Leser eine ruhige Invariante und haelt sie fuer
    Deckung. Der Zaehler macht die Grenze des Verfahrens messbar.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {  # nicht unterscheidbar -> unterdrueckt, aber gezaehlt
                "event_type": "order_filled",
                "order_id": "ord_blind",
                "symbol": "AAVE/USDT",
                "fill_price": 100.880415,
                "timestamp_utc": "2026-07-26T23:39:28+00:00",
            },
            {  # traegt die Meldung
                "event_type": "order_filled",
                "order_id": "ord_eth",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            },
        ],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["count"] == 1
    assert v["evidence"]["curve_not_discriminating"] == 1


def test_fill_der_schliessenden_order_zaehlt_nicht_doppelt(tmp_path: Path) -> None:
    """Ein quarantaenierter Close hinterlaesst ZWEI Zeilen mit demselben Preis.

    ``position_closed`` UND der ``order_filled`` der schliessenden Order tragen
    denselben ``order_id``. Ohne die Bruecke meldet der Waechter den bereits
    aufgearbeiteten Vorfall ein zweites Mal von der Order-Seite — live gemessen
    am 26.08. genau vier Mal.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_3bde9b249140",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            },
            {
                "event_type": "position_closed",
                "order_id": "ord_3bde9b249140",
                "symbol": "ETH/USDT",
                "entry_price": 1874.24956227636,
                "exit_price": 3225.6863500000004,
                "position_side": "long",
                "timestamp_utc": "2026-08-11T23:09:58+00:00",
            },
        ],
    )
    assert _tl002(art) is None, "derselbe Vorgang darf nicht zweimal melden"


def test_fill_ohne_quarantaenierten_close_meldet_weiter(tmp_path: Path) -> None:
    """Gegenprobe: die Bruecke darf nur ueber einen QUARANTAENIERTEN Close gehen.

    Sonst wuerde jeder Mock-Fill sich selbst entlasten, sobald irgendein Close
    denselben ``order_id`` traegt.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_frisch",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-08-20T10:00:00+00:00",
            },
            {  # Close mit unauffaelligem Preis -> nicht quarantaeniert
                "event_type": "position_closed",
                "order_id": "ord_frisch",
                "symbol": "ETH/USDT",
                "entry_price": 2500.0,
                "exit_price": 2550.0,
                "position_side": "long",
                "timestamp_utc": "2026-08-20T10:00:00+00:00",
            },
        ],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["count"] == 1


# --- Der alarmfreie Diagnose-Kanal ------------------------------------------------


def test_diagnostik_entsteht_auch_ohne_befund(tmp_path: Path) -> None:
    """Schweigen allein ist kein Beleg fuer Deckung.

    TL-002 hat eine strukturelle Blindstelle; ohne Zahl im Report waere sie von
    "nichts gefunden" nicht zu unterscheiden.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_blind",
                "symbol": "AAVE/USDT",
                "fill_price": 100.880415,
                "timestamp_utc": "2026-07-26T23:39:28+00:00",
            }
        ],
    )
    result = run_lint(art)
    assert _tl002(art) is None
    diag = result["diagnostics"]["TL-002"]
    assert diag["reported"] == 0
    assert diag["suppressed_curve_not_discriminating"] == 1


def test_diagnostik_veraendert_die_schwere_nicht(tmp_path: Path) -> None:
    """Ein Diagnose-Eintrag darf weder ``--gate`` ziehen noch den Digest kippen."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_blind",
                "symbol": "AAVE/USDT",
                "fill_price": 100.880415,
                "timestamp_utc": "2026-07-26T23:39:28+00:00",
            }
        ],
    )
    result = run_lint(art)
    assert result["max_severity"] is None
    assert not [v for v in result["violations"] if v["invariant_id"] == "TL-002"]
    assert result["diagnostics"]["TL-002"]["suppressed_curve_not_discriminating"] == 1


def test_diagnostik_nennt_die_reichweite_gestaffelt(tmp_path: Path) -> None:
    """Die Reichweite des Verfahrens steht im Report, nicht nur im Kommentar.

    Gestaffelt, nicht binaer: sonst faellt ein Symbol wie BNB (43 Live-Zeilen,
    40,9 % Abdeckung) kommentarlos aus der Deckung, statt als Verdachtsstufe
    sichtbar zu bleiben.
    """
    art = _artifacts(tmp_path)
    _write(art / "paper_execution_audit.jsonl", [])
    result = run_lint(art)
    tiers = result["diagnostics"]["TL-002"]["coverage_tiers"]
    assert tiers["strong"] == ["BTC/USDT", "ETH/USDT"]
    assert "BNB/USDT" in tiers["reportable"]
    assert "SOL/USDT" in tiers["suppressed"]


def test_slippage_treffer_auf_mittlerer_stufe_bleibt_verdacht(tmp_path: Path) -> None:
    """Auch der Slippage-Pfad wird von der Abdeckung begrenzt.

    Bei BNB (40,9 %) trifft rund jeder zweite in-Band-Preis die Kurve — auch mit
    Slippage. Wer den Treffer dort als BELEG zaehlt, macht ihn unwiderlegbar:
    die Entlastungswege greifen nur bei schwachen Treffern, ein "starker" liesse
    sich durch keine belegte reale Quelle mehr aufweichen.
    """
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_bnb",
                "symbol": "BNB/USDT",
                "fill_price": 403.45817000000005,  # = 403.66 * (1 - 0,0005)
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    _write(
        art / "trading_loop_audit.jsonl",
        [{"order_id": "ord_bnb", "notes": ["market_data_source:bybit"]}],
    )
    assert _tl002(art) is None, "belegte reale Quelle muss den Verdacht ausraeumen"


def test_slippage_treffer_auf_starker_stufe_bleibt_beleg(tmp_path: Path) -> None:
    """Gegenprobe: bei ETH (5,6 %) laesst sich derselbe Pfad NICHT aufweichen."""
    art = _artifacts(tmp_path)
    _write(
        art / "paper_execution_audit.jsonl",
        [
            {
                "event_type": "order_filled",
                "order_id": "ord_eth",
                "symbol": "ETH/USDT",
                "fill_price": 3225.6863500000004,
                "timestamp_utc": "2026-07-12T09:00:00+00:00",
            }
        ],
    )
    _write(
        art / "trading_loop_audit.jsonl",
        [{"order_id": "ord_eth", "notes": ["market_data_source:bybit"]}],
    )
    v = _tl002(art)
    assert v is not None
    assert v["evidence"]["strong_evidence"] == 1
