"""L2-Kandidatenkontext: ID vor der Messung, Eingabeschnitt nach dem Kursabruf.

Der L2-Provider misst INNERHALB von ``SignalGenerator.generate``. Diese Tests
pinnen, dass der Kontext waehrend der Messung steht, exakt zurueckgesetzt wird und
die Kausalitaet sichtbar macht: Kurs und L1-Datensatz duerfen nicht juenger sein
als der Eingabeschnitt (Befund 3, Zeitmodell seit 2026-09-30).
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.l2_candidate_context import (
    L2CandidateContext,
    bind_candidate,
    current_candidate,
)

_START = "2026-09-23T10:00:00+00:00"
_CUT = "2026-09-23T10:00:00.400000+00:00"


def _bind(candidate_id: str = "cyc-1", **kw: str):  # noqa: ANN202
    return bind_candidate(
        candidate_id=candidate_id,
        cycle_started_at=kw.get("start", _START),
        input_cutoff_ts=kw.get("cut", _CUT),
        reference_price_ts=kw.get("price"),
    )


# ── Kontext an sich ────────────────────────────────────────────────────────


def test_ausserhalb_eines_zyklus_gibt_es_keinen_kontext() -> None:
    assert current_candidate() is None


def test_kontext_steht_im_block_und_ist_danach_weg() -> None:
    with _bind() as ctx:
        seen = current_candidate()
        assert seen is ctx
        assert seen.candidate_id == "cyc-1"
        assert seen.cycle_started_at == _START and seen.input_cutoff_ts == _CUT
    assert current_candidate() is None


def test_kontext_wird_auch_bei_einer_ausnahme_zurueckgesetzt() -> None:
    with pytest.raises(RuntimeError):
        with _bind():
            raise RuntimeError("Messung gescheitert")
    assert current_candidate() is None


def test_verschachtelung_stellt_den_aeusseren_kontext_wieder_her() -> None:
    with _bind("aussen"):
        with _bind("innen"):
            assert current_candidate().candidate_id == "innen"
        assert current_candidate().candidate_id == "aussen"


@pytest.mark.asyncio
async def test_kontext_leckt_nicht_zwischen_nebenlaeufigen_zyklen() -> None:
    """Ein Kandidat darf niemals in die Messung eines anderen Zyklus geraten."""
    gesehen: dict[str, str | None] = {}

    async def zyklus(name: str, verzoegerung: float) -> None:
        with _bind(name):
            await asyncio.sleep(verzoegerung)
            ctx = current_candidate()
            gesehen[name] = ctx.candidate_id if ctx else None

    await asyncio.gather(zyklus("cyc-a", 0.02), zyklus("cyc-b", 0.01))

    assert gesehen == {"cyc-a": "cyc-a", "cyc-b": "cyc-b"}


# ── Kausalitaet gegen den Eingabeschnitt ───────────────────────────────────


def test_preis_nach_zyklusbeginn_aber_vor_dem_schnitt_ist_kausal() -> None:
    # Genau der Normalfall, der bis 30.09. faelschlich durchfiel (Median 0,17 s).
    ctx = L2CandidateContext("cyc-1", _START, _CUT, "2026-09-23T10:00:00.170000+00:00")
    assert ctx.causality_ok() is True
    assert ctx.as_log_fields()["causality_ok"] is True


def test_gleichstand_mit_dem_schnitt_gilt_als_in_ordnung() -> None:
    assert L2CandidateContext("cyc-1", _START, _CUT, _CUT).causality_ok() is True


def test_preis_nach_dem_schnitt_ist_look_ahead_und_wird_markiert() -> None:
    ctx = L2CandidateContext("cyc-1", _START, _CUT, "2026-09-23T10:00:01+00:00")
    assert ctx.causality_ok() is False
    # Sichtbar machen, nicht verwerfen — das Urteil gehoert dem Evaluator.
    assert ctx.as_log_fields()["causality_ok"] is False


def test_zukunftsdatierter_preis_faellt_durch() -> None:
    # Uhrenversatz der Boerse: Zeitstempel eine Minute in der Zukunft.
    ctx = L2CandidateContext("cyc-1", _START, _CUT, "2026-09-23T10:01:00+00:00")
    assert ctx.causality_ok() is False


def test_spaeter_eingetroffener_l1_satz_ist_nicht_kausal() -> None:
    ctx = L2CandidateContext("cyc-1", _START, _CUT, _START)
    assert ctx.causality_ok("2026-09-23T09:45:00+00:00") is True
    assert ctx.causality_ok("2026-09-23T10:00:01+00:00") is False
    assert ctx.as_log_fields("2026-09-23T10:00:01+00:00")["causality_ok"] is False


def test_ohne_preiszeit_wird_keine_kausalitaet_behauptet() -> None:
    ctx = L2CandidateContext(candidate_id="cyc-1", cycle_started_at=_START, input_cutoff_ts=_CUT)
    assert ctx.causality_ok() is None
    fields = ctx.as_log_fields()
    assert "causality_ok" not in fields
    assert "reference_price_ts" not in fields


def test_unlesbare_zeit_behauptet_nichts() -> None:
    ctx = L2CandidateContext("cyc-1", _START, "kein-zeitstempel", _START)
    assert ctx.causality_ok() is None


def test_naiv_gegen_aware_ist_nicht_vergleichbar() -> None:
    ctx = L2CandidateContext("cyc-1", _START, "2026-09-23T10:00:05", _START)
    assert ctx.causality_ok() is None


def test_log_felder_tragen_id_zyklusbeginn_schnitt_und_l1() -> None:
    ctx = L2CandidateContext("cyc-1", _START, _CUT, "2026-09-23T10:00:00.170000+00:00")
    assert ctx.as_log_fields("2026-09-23T09:45:00+00:00") == {
        "candidate_id": "cyc-1",
        "cycle_started_at": _START,
        "input_cutoff_ts": _CUT,
        "reference_price_ts": "2026-09-23T10:00:00.170000+00:00",
        "l1_observed_ts": "2026-09-23T09:45:00+00:00",
        "causality_ok": True,
    }
