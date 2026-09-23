"""L2-Kandidatenkontext: ID vor der Messung, Zeiten kausal zusammenhaengend.

Der L2-Provider misst INNERHALB von ``SignalGenerator.generate`` und schrieb
bisher nur ``symbol``/``direction``/``now()`` in den Shadow-Log. Kandidat und
Messung waren danach nur ueber ``symbol`` + Zeitfenster paarbar, und der Abstand
zwischen Mess- und Entscheidungszeit liess sich aus den Daten nicht
rekonstruieren. Diese Tests pinnen, dass der Kontext waehrend der Messung steht,
exakt zurueckgesetzt wird und die Kausalitaet der drei Zeiten sichtbar macht.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.l2_candidate_context import (
    L2CandidateContext,
    bind_candidate,
    current_candidate,
)

# ── Kontext an sich ────────────────────────────────────────────────────────


def test_ausserhalb_eines_zyklus_gibt_es_keinen_kontext() -> None:
    assert current_candidate() is None


def test_kontext_steht_im_block_und_ist_danach_weg() -> None:
    with bind_candidate(candidate_id="cyc-1", decision_ts="2026-09-23T10:00:00+00:00") as ctx:
        seen = current_candidate()
        assert seen is ctx
        assert seen.candidate_id == "cyc-1"
    assert current_candidate() is None


def test_kontext_wird_auch_bei_einer_ausnahme_zurueckgesetzt() -> None:
    with pytest.raises(RuntimeError):
        with bind_candidate(candidate_id="cyc-1", decision_ts="2026-09-23T10:00:00+00:00"):
            raise RuntimeError("Messung gescheitert")
    assert current_candidate() is None


def test_verschachtelung_stellt_den_aeusseren_kontext_wieder_her() -> None:
    with bind_candidate(candidate_id="aussen", decision_ts="2026-09-23T10:00:00+00:00"):
        with bind_candidate(candidate_id="innen", decision_ts="2026-09-23T10:00:05+00:00"):
            assert current_candidate().candidate_id == "innen"
        assert current_candidate().candidate_id == "aussen"


@pytest.mark.asyncio
async def test_kontext_leckt_nicht_zwischen_nebenlaeufigen_zyklen() -> None:
    """Ein Kandidat darf niemals in die Messung eines anderen Zyklus geraten."""
    gesehen: dict[str, str | None] = {}

    async def zyklus(name: str, verzoegerung: float) -> None:
        with bind_candidate(candidate_id=name, decision_ts="2026-09-23T10:00:00+00:00"):
            await asyncio.sleep(verzoegerung)
            ctx = current_candidate()
            gesehen[name] = ctx.candidate_id if ctx else None

    await asyncio.gather(zyklus("cyc-a", 0.02), zyklus("cyc-b", 0.01))

    assert gesehen == {"cyc-a": "cyc-a", "cyc-b": "cyc-b"}


# ── Kausalitaet der drei Zeiten ────────────────────────────────────────────


def test_preis_vor_entscheidung_ist_kausal_in_ordnung() -> None:
    ctx = L2CandidateContext(
        candidate_id="cyc-1",
        decision_ts="2026-09-23T10:00:05+00:00",
        reference_price_ts="2026-09-23T10:00:00+00:00",
    )
    assert ctx.causality_ok is True
    assert ctx.as_log_fields()["causality_ok"] is True


def test_gleichstand_gilt_als_in_ordnung() -> None:
    # Derselbe Tick kann Preis und Entscheidung tragen.
    same = "2026-09-23T10:00:00+00:00"
    assert L2CandidateContext("cyc-1", same, same).causality_ok is True


def test_preis_nach_der_entscheidung_ist_look_ahead_und_wird_markiert() -> None:
    ctx = L2CandidateContext(
        candidate_id="cyc-1",
        decision_ts="2026-09-23T10:00:00+00:00",
        reference_price_ts="2026-09-23T10:00:05+00:00",
    )
    assert ctx.causality_ok is False
    # Sichtbar machen, nicht verwerfen — das Urteil gehoert dem Evaluator.
    assert ctx.as_log_fields()["causality_ok"] is False


def test_ohne_preiszeit_wird_keine_kausalitaet_behauptet() -> None:
    ctx = L2CandidateContext(candidate_id="cyc-1", decision_ts="2026-09-23T10:00:00+00:00")
    assert ctx.causality_ok is None
    fields = ctx.as_log_fields()
    assert "causality_ok" not in fields
    assert "reference_price_ts" not in fields


def test_unlesbare_zeit_behauptet_nichts() -> None:
    ctx = L2CandidateContext("cyc-1", "kein-zeitstempel", "2026-09-23T10:00:00+00:00")
    assert ctx.causality_ok is None


def test_naiv_gegen_aware_ist_nicht_vergleichbar() -> None:
    ctx = L2CandidateContext("cyc-1", "2026-09-23T10:00:05", "2026-09-23T10:00:00+00:00")
    assert ctx.causality_ok is None


def test_log_felder_tragen_id_und_entscheidungszeit() -> None:
    ctx = L2CandidateContext(
        candidate_id="cyc-1",
        decision_ts="2026-09-23T10:00:05+00:00",
        reference_price_ts="2026-09-23T10:00:00+00:00",
    )
    assert ctx.as_log_fields() == {
        "candidate_id": "cyc-1",
        "decision_ts": "2026-09-23T10:00:05+00:00",
        "reference_price_ts": "2026-09-23T10:00:00+00:00",
        "causality_ok": True,
    }
