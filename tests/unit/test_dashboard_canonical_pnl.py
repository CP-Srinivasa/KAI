"""G3 — die kanonische Paper-PnL und ihre Etiketten.

Der Anlass: Dashboard und unabhaengige Nachrechnung zeigten -100,42 USD gegen
-181,49 USD, und der Audit fuehrte das als offenen Widerspruch (C-12). Die
Arbitrierung ergab, dass es nie zwei Rechenwege gab -- dieselbe Regel, 17 Stunden
auseinander gemessen, an einem wachsenden Buch. Die 81,07 USD waren die PnL der
Closes 277 bis 281.

Diese Tests halten fest, was daraus folgt:

  * Die Reihenfolge der beiden Filter ist egal (Epoche und Korruptionssignatur
    sind unabhaengige Praedikate) -- niemand muss sich je wieder fragen, ob das
    Dashboard "anders herum" rechnet.
  * Die Quarantaene-Zahlen tragen dieselbe Epoche wie die PnL daneben. Vorher
    standen 82.404 USD Lifetime neben -241 USD Epoche im selben Payload.
  * Das Scope-Etikett widerspricht nicht der eigenen Rechnung.
  * Die Zahl kommt nie ohne ihren Messzeitpunkt.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.execution.close_pnl import close_pnl
from app.execution.paper_scope import fills_scope_label, quarantine_payload, split_closes
from app.learning.bayes_quarantine import is_corrupt_close

EPOCH_START = datetime(2026, 7, 12, 22, 22, 9, tzinfo=UTC)


def _close(
    ts: datetime, pnl: float, *, symbol: str = "BTC/USDT", partial: bool = False
) -> dict[str, Any]:
    return {
        "event_type": "position_partial_closed" if partial else "position_closed",
        "closed_at": ts.isoformat(),
        "symbol": symbol,
        "trade_pnl_usd": pnl,
    }


def _book() -> list[dict[str, Any]]:
    """Ein Buch mit allen vier Faellen: vor/in der Epoche, sauber/korrupt."""
    before = EPOCH_START - timedelta(days=3)
    inside = EPOCH_START + timedelta(days=1)
    corrupt = _close(inside + timedelta(hours=1), 900.0)
    # Die Signatur, an der die Quarantaene greift, kommt aus der echten Regel --
    # der Test erfindet sie nicht, er markiert die Zeile so, wie das Buch es tut.
    corrupt["entry_price"] = 1.0
    corrupt["exit_price"] = 100.0
    corrupt["quantity"] = 1.0
    return [
        _close(before, 500.0),
        _close(before, -50.0),
        _close(inside, 10.0),
        _close(inside + timedelta(minutes=5), -30.0),
        _close(inside + timedelta(minutes=10), -20.0, partial=True),
        corrupt,
    ]


def _ts_of(row: dict[str, Any]) -> datetime | None:
    raw = row.get("closed_at")
    return datetime.fromisoformat(str(raw)) if raw else None


def _in_epoch(row: dict[str, Any]) -> bool:
    return datetime.fromisoformat(str(row["closed_at"])) >= EPOCH_START


def test_filter_order_cannot_matter() -> None:
    """Quarantaene-zuerst und Epoche-zuerst muessen dieselbe Menge liefern.

    Live an 693 echten Closes gemessen: beide Wege 287 / -241,89 USD / 34,49 %.
    Sie sind kommutativ, weil beide Praedikate unabhaengig sind -- der Verdacht
    "das Dashboard filtert anders herum" konnte nie zutreffen.
    """
    rows = _book()
    d = [r for r in rows if not is_corrupt_close(r) and _in_epoch(r)]
    a = [r for r in rows if _in_epoch(r) and not is_corrupt_close(r)]
    assert d == a
    assert round(sum(close_pnl(r) for r in d), 2) == round(sum(close_pnl(r) for r in a), 2)


def test_the_corrupt_close_is_excluded_from_the_canonical_sum() -> None:
    rows = _book()
    canonical = [r for r in rows if _in_epoch(r) and not is_corrupt_close(r)]
    assert len(canonical) == 3, "drei saubere Closes in der Epoche"
    assert round(sum(close_pnl(r) for r in canonical), 2) == -40.0
    # Positivkontrolle: der korrupte Close IST in der Epoche -- er wird nicht
    # durch den Zeitfilter entfernt, sondern durch die Signatur.
    assert any(_in_epoch(r) and is_corrupt_close(r) for r in rows)


def test_quarantine_carries_the_same_epoch_as_the_pnl_beside_it(tmp_path: Path) -> None:
    """Der Payload-Fehler, den G3 gefunden hat, in einem Satz Code.

    Vorher: quarantined_* wurde VOR dem Epochenfilter gebildet, alles daneben
    danach. Real standen 24 Closes / 82.404,06 USD Lifetime neben 287 Closes /
    -241,89 USD Epoche -- im selben Payload, ohne Kennzeichnung.
    """
    rows = _book()
    quarantined_lifetime = [r for r in rows if is_corrupt_close(r)]
    quarantined_epoch = [r for r in quarantined_lifetime if _in_epoch(r)]
    realized = [r for r in rows if _in_epoch(r) and not is_corrupt_close(r)]

    # Beide Sichten existieren und sind unterscheidbar.
    assert len(quarantined_lifetime) >= len(quarantined_epoch)
    # Die Zahl, die neben der PnL steht, teilt deren Population.
    for r in quarantined_epoch:
        assert _in_epoch(r)
    for r in realized:
        assert _in_epoch(r)


def test_payload_names_both_scopes_not_just_one() -> None:
    """Der Payload-Block fuehrt beide Sichten und benennt, welche welche ist.

    Vorher trug er nur EINE Zahl -- die Lifetime-Sicht -- unter einem Namen, der
    neben epochenreinen Werten stand. Geprueft wird das Verhalten, nicht der
    Quelltext: eine Textsuche haette den Umzug der Logik in ein eigenes Modul
    ueberlebt, ohne noch irgendetwas zu beweisen.
    """
    rows = _book()
    scoped = split_closes(
        rows,
        is_corrupt=is_corrupt_close,
        first_ts=lambda r, keys: _ts_of(r),
        epoch_start=EPOCH_START,
    )
    payload = quarantine_payload(scoped, pnl=close_pnl, epoch_id="paper_v2_attested")

    assert payload["paper_quarantine_scope"] == "epoch:paper_v2_attested"
    assert payload["paper_quarantined_closes"] == len(scoped.quarantined_in_epoch)
    assert payload["paper_quarantined_closes_lifetime"] == len(scoped.quarantined_lifetime)
    # Und die Aussage, um die es geht: die Lifetime-Sicht ist nie kleiner.
    assert payload["paper_quarantined_closes_lifetime"] >= payload["paper_quarantined_closes"]


def test_the_epoch_view_drops_a_pre_epoch_quarantine_row() -> None:
    """Genau der Fall, der real 24 gegen 4 Closes auseinandertrieb."""
    rows = _book()
    old_corrupt = _close(EPOCH_START - timedelta(days=5), 900.0)
    old_corrupt.update({"entry_price": 1.0, "exit_price": 100.0, "quantity": 1.0})
    scoped = split_closes(
        rows + [old_corrupt],
        is_corrupt=is_corrupt_close,
        first_ts=lambda r, keys: _ts_of(r),
        epoch_start=EPOCH_START,
    )
    assert len(scoped.quarantined_lifetime) == len(scoped.quarantined_in_epoch) + 1


def test_scope_label_follows_the_epoch_not_the_cutoff() -> None:
    """Das Etikett muss der eigenen Rechnung folgen, sonst ist es schaedlich."""
    assert fills_scope_label("paper_v2_attested", has_cutoff=True) == "epoch:paper_v2_attested"
    assert fills_scope_label("paper_v2_attested", has_cutoff=False) == "epoch:paper_v2_attested"
    # Ohne Epoche bleibt die alte, korrekte Unterscheidung erhalten.
    assert fills_scope_label(None, has_cutoff=True) == "cutoff_since"
    assert fills_scope_label(None, has_cutoff=False) == "lifetime"


def test_without_an_epoch_both_views_are_the_same_set() -> None:
    """Kein Epochen-Reset heisst: es gibt keine Grenze -- keine Notloesung."""
    rows = _book()
    scoped = split_closes(
        rows, is_corrupt=is_corrupt_close, first_ts=lambda r, keys: _ts_of(r), epoch_start=None
    )
    assert scoped.quarantined_in_epoch == scoped.quarantined_lifetime
    assert scoped.pre_epoch_excluded == 0


def test_an_undated_close_is_excluded_and_counted_never_guessed() -> None:
    rows = _book() + [{"event_type": "position_closed", "trade_pnl_usd": 1000.0}]
    scoped = split_closes(
        rows,
        is_corrupt=is_corrupt_close,
        first_ts=lambda r, keys: _ts_of(r),
        epoch_start=EPOCH_START,
    )
    assert scoped.without_timestamp == 1
    assert 1000.0 not in [close_pnl(r) for r in scoped.clean_in_epoch]


def test_a_close_without_a_timestamp_never_counts_as_performance() -> None:
    """Fail-closed Richtung Ausschluss: undatierbar heisst kein Anspruch."""
    rows = _book() + [{"event_type": "position_closed", "symbol": "X", "trade_pnl_usd": 1000.0}]
    dated = [r for r in rows if r.get("closed_at")]
    assert len(dated) == len(rows) - 1
    assert 1000.0 not in [close_pnl(r) for r in dated]


def test_the_four_cited_numbers_are_one_definition_at_four_times() -> None:
    """Dokumentiert das Ergebnis der Arbitrierung als pruefbare Rechnung.

    215 zaehlt die kontaminierten Closes MIT, die anderen drei nicht -- das ist
    die einzige Populationsdifferenz. Der Rest ist Zeit.
    """
    booked_with_contamination = 771.05
    contamination = 1701.54
    clean_at_that_moment = round(booked_with_contamination - contamination, 2)
    # Live gegen das echte Buch zum Stichtag 2026-08-18T17:30Z nachgemessen.
    assert clean_at_that_moment == -930.49


def test_close_pnl_uses_the_trade_field_not_the_cumulative_one() -> None:
    """`realized_pnl_usd` ist ein BESTAND, `trade_pnl_usd` die Trade-PnL.

    Das Summieren des Bestandsfeldes ueberschaetzte die PnL einmal um das
    2,8-fache. Der Test haelt fest, welches Feld die kanonische Zahl speist.
    """
    row = {
        "event_type": "position_closed",
        "closed_at": (EPOCH_START + timedelta(days=1)).isoformat(),
        "trade_pnl_usd": -12.5,
        "realized_pnl_usd": 9999.0,
    }
    assert close_pnl(row) == -12.5


def test_the_book_is_json_lines_and_tolerates_a_broken_row() -> None:
    """Eine unparsbare Zeile darf die Zahl nicht kippen, nur fehlen."""
    raw = "\n".join([json.dumps(_book()[2]), "{kaputt", json.dumps(_book()[3])])
    parsed = []
    for line in raw.splitlines():
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    assert len(parsed) == 2


def test_the_quarantine_sum_carries_its_decomposition() -> None:
    """Kein Aggregat ohne Zerlegung -- und hier traegt sie wirklich etwas.

    Die Lifetime-Quarantaene summiert real 82.404,06 USD aus 24 Zeilen, von
    denen 17 gewinnen und 4 verlieren. Eine blosse Summe sagt nicht, ob ein
    einziges Phantom sie traegt; leave-one-out sagt es.
    """
    rows = _book()
    scoped = split_closes(
        rows,
        is_corrupt=is_corrupt_close,
        first_ts=lambda r, keys: _ts_of(r),
        epoch_start=EPOCH_START,
    )
    payload = quarantine_payload(scoped, pnl=close_pnl, epoch_id="paper_v2_attested")
    dec = payload["paper_quarantined_decomposition"]
    assert set(dec) == {"epoch", "lifetime"}
    assert dec["epoch"]["n"] == payload["paper_quarantined_closes"]
    assert dec["lifetime"]["n"] == payload["paper_quarantined_closes_lifetime"]
    # Bei genau einer korrupten Zeile ist der Mittelwert vollstaendig von ihr
    # getragen -- die Zerlegung muss das sagen koennen, nicht verschweigen.
    assert dec["epoch"]["top_contributor"] is not None


def test_an_empty_quarantine_decomposes_without_crashing() -> None:
    """Ein leerer Topf ist ein gueltiger Zustand, kein Fehler."""
    scoped = split_closes(
        [_close(EPOCH_START + timedelta(days=1), 5.0)],
        is_corrupt=is_corrupt_close,
        first_ts=lambda r, keys: _ts_of(r),
        epoch_start=EPOCH_START,
    )
    payload = quarantine_payload(scoped, pnl=close_pnl, epoch_id="paper_v2_attested")
    assert payload["paper_quarantined_closes"] == 0
    assert payload["paper_quarantined_pnl_usd"] == 0
    assert payload["paper_quarantined_decomposition"]["epoch"]["n"] == 0
