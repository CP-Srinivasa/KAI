"""Gemeinsamer Vorschauvertrag fuer Dashboard und Telegram (D-288, Befund 6).

Beide Oberflaechen muessen vor der Freigabe dasselbe sagen: ob der Empfaenger
und der Zweck die Regelkette passieren, was die Gebuehr voraussichtlich kostet
und ob das Limit dafuer reicht. Die Vorschau schreibt NICHTS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.payments.models import Money, Quote
from app.payments.preview_contract import assess_fee, preview_payment
from tests.unit.payments.test_service import a_request, a_service

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _quote(fee: int, source: str) -> Quote:
    return Quote(
        rail="lightning",
        amount=Money(minor_units=900, currency="SAT", scale=0),
        fee_estimate=Money(minor_units=fee, currency="SAT", scale=0),
        valid_until=NOW + timedelta(minutes=5),
        estimate_source=source,
    )


def test_eine_node_schaetzung_ueber_dem_limit_warnt() -> None:
    fee = assess_fee(_quote(6, "node_estimate_route_fee"), 3)
    assert (fee.estimate_sat, fee.warning) == (6, "over_limit")


def test_eine_node_schaetzung_im_limit_warnt_nicht() -> None:
    assert assess_fee(_quote(2, "node_estimate_route_fee"), 3).warning == ""


def test_keine_route_hat_keine_scheinzahl() -> None:
    fee = assess_fee(_quote(3, "node_probe_no_route"), 3)
    assert (fee.estimate_sat, fee.warning) == (None, "no_route")


def test_eine_gescheiterte_probe_ist_nicht_keine_route() -> None:
    assert assess_fee(_quote(3, "node_probe_failed"), 3).warning == "probe_failed"


def test_settings_schaetzungen_warnen_nie() -> None:
    assert assess_fee(_quote(99, "settings_floor"), 3).warning == ""


def test_ohne_quote_ist_die_gebuehr_unbekannt() -> None:
    fee = assess_fee(None, 3)
    assert (fee.estimate_sat, fee.source, fee.warning) == (None, "unavailable", "unavailable")


async def test_die_vorschau_prueft_die_regelkette_und_schreibt_nichts(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    before = service.journal.path.read_bytes() if service.journal.path.exists() else b""

    preview = await preview_payment(service, a_request())

    assert preview.verdict in {"ALLOW", "REQUIRES_APPROVAL"}
    assert preview.destination_known is True
    assert preview.fee.source != "unavailable"
    after = service.journal.path.read_bytes() if service.journal.path.exists() else b""
    assert after == before  # kein Intent, kein Record


async def test_ein_fremder_empfaenger_wird_abgelehnt_und_nie_geprobt(tmp_path: Path) -> None:
    service = a_service(tmp_path)
    preview = await preview_payment(service, a_request(destination="sim:settle:mallory"))

    assert preview.verdict == "DENY"
    assert "destination_allowlist" in preview.rule_ids
    assert preview.fee.source == "unavailable"  # keine Quote, also keine Netz-Probe


async def test_der_vertrag_ist_als_dict_serialisierbar(tmp_path: Path) -> None:
    preview = await preview_payment(a_service(tmp_path), a_request())
    body = preview.to_dict()
    assert set(body) == {"verdict", "rule_ids", "reasons", "destination_known", "fee"}
    assert set(body["fee"]) == {"estimate_sat", "source", "limit_sat", "warning"}
