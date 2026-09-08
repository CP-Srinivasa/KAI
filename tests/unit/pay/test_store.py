"""Der Strom der Produktschicht: Roundtrip, Haltbarkeit, Grenzen.

Der Store ist ein CACHE ueber dem Geld-Journal. Was hier geprueft wird, ist
deshalb nicht "stimmt der Betrag" — das prueft ``test_service.py`` gegen das
Journal —, sondern: ueberlebt die VERKNUEPFUNG einen Neustart, und traegt eine
kaputte Zeile den Rest der Datei mit sich?
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.pay.status import PayStatus
from app.pay.store import EVENT_CREATED, EVENT_SETTLED, PayRequest, PayStore

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _request(payment_id: str = "pay_000000000001", **overrides: object) -> PayRequest:
    base: dict[str, object] = {
        "payment_id": payment_id,
        "ref_hash": "a" * 64,
        "amount_sat": 1000,
        "description": "Beratung 30 Minuten",
        "reference": "ORDER-1",
        "webhook_url": "",
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
        "bolt11": "sim:settle:invoice:abc",
    }
    base.update(overrides)
    return PayRequest(**base)  # type: ignore[arg-type]


def test_anlage_und_neuaufbau_ergeben_denselben_zustand(tmp_path: Path) -> None:
    path = tmp_path / "pay" / "requests.jsonl"
    store = PayStore(path)
    store.create(_request())

    reopened = PayStore(path)
    assert reopened.load() == 1
    entry = reopened.get("pay_000000000001")
    assert entry is not None
    assert entry.ref_hash == "a" * 64
    assert entry.bolt11 == "sim:settle:invoice:abc"
    assert entry.status is PayStatus.WAITING


def test_jede_zeile_ist_fuer_sich_lesbar(tmp_path: Path) -> None:
    """Eine Zeile, ein JSON-Objekt, ein ``ts``. Ohne das ist der Strom kein Strom."""
    path = tmp_path / "pay" / "requests.jsonl"
    store = PayStore(path)
    store.create(_request())
    store.mark("pay_000000000001", PayStatus.SETTLED, journal_seq=7, record_hash="b" * 64)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == [EVENT_CREATED, EVENT_SETTLED]
    assert all(line["ts"] and line["payload"]["payment_id"] for line in lines)


def test_der_zustand_ueberlebt_den_neustart(tmp_path: Path) -> None:
    path = tmp_path / "pay" / "requests.jsonl"
    store = PayStore(path)
    store.create(_request())
    store.mark("pay_000000000001", PayStatus.SETTLED, journal_seq=7, record_hash="b" * 64)

    reopened = PayStore(path)
    reopened.load()
    entry = reopened.get("pay_000000000001")
    assert entry is not None
    assert entry.status is PayStatus.SETTLED
    assert (entry.journal_seq, entry.record_hash) == (7, "b" * 64)


def test_settled_wird_nie_ueberschrieben(tmp_path: Path) -> None:
    """Eine bezahlte Forderung laeuft nicht nachtraeglich ab."""
    store = PayStore(tmp_path / "pay" / "requests.jsonl")
    store.create(_request())
    store.mark("pay_000000000001", PayStatus.SETTLED, journal_seq=7, record_hash="b" * 64)
    again = store.mark("pay_000000000001", PayStatus.EXPIRED)
    assert again.status is PayStatus.SETTLED


def test_waiting_ist_kein_schreibbarer_zustand(tmp_path: Path) -> None:
    store = PayStore(tmp_path / "pay" / "requests.jsonl")
    store.create(_request())
    with pytest.raises(ValueError, match="not a terminal"):
        store.mark("pay_000000000001", PayStatus.WAITING)


def test_eine_kaputte_zeile_nimmt_den_rest_nicht_mit(tmp_path: Path) -> None:
    """Anders als das Geld-Journal: hier haengt keine Beweiskette daran.

    Der Kern verweigert bei einem zerrissenen Tail den Dienst, weil ein
    Weiterschreiben die Kette forken wuerde. Dieser Strom hat keine Kette — das
    Ueberspringen kostet eine Anzeige, und der Eingang bleibt im Journal
    belegt.
    """
    path = tmp_path / "pay" / "requests.jsonl"
    store = PayStore(path)
    store.create(_request("pay_000000000001"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "request_created", "payload"\n')
    store.create(_request("pay_000000000002", reference="ORDER-2"))

    reopened = PayStore(path)
    reopened.load()
    assert reopened.get("pay_000000000001") is not None
    assert reopened.get("pay_000000000002") is not None


def test_referenz_und_idempotenzschluessel_finden_zurueck(tmp_path: Path) -> None:
    store = PayStore(tmp_path / "pay" / "requests.jsonl")
    store.create(_request("pay_000000000001", idempotency_key_hash="c" * 64))
    store.create(_request("pay_000000000002", reference="ORDER-2"))

    assert [r.payment_id for r in store.by_reference("ORDER-1")] == ["pay_000000000001"]
    found = store.by_key("c" * 64)
    assert found is not None and found.payment_id == "pay_000000000001"
    assert store.by_key("d" * 64) is None


def test_offene_forderungen_kommen_alt_zuerst_und_gedeckelt(tmp_path: Path) -> None:
    """Alt zuerst: die aelteste Forderung laeuft als naechste ab."""
    store = PayStore(tmp_path / "pay" / "requests.jsonl")
    for index in range(5):
        store.create(_request(f"pay_00000000000{index}"))
    store.mark("pay_000000000000", PayStatus.SETTLED)

    open_ids = [r.payment_id for r in store.open_requests(limit=2)]
    assert open_ids == ["pay_000000000001", "pay_000000000002"]
    assert [r.payment_id for r in store.recent(limit=2)] == [
        "pay_000000000004",
        "pay_000000000003",
    ]
    assert store.settled_count() == 1
    newest = store.newest_settled()
    assert newest is not None and newest.payment_id == "pay_000000000000"


def test_ein_rail_fehler_hinterlaesst_keine_zeile(tmp_path: Path) -> None:
    """Sonst begraebt ein toter Node die vier Ereignisse, die etwas bedeuten."""
    path = tmp_path / "pay" / "requests.jsonl"
    store = PayStore(path)
    store.create(_request())
    before = len(path.read_text(encoding="utf-8").splitlines())
    noted = store.note_error("pay_000000000001", "rail: node unreachable")
    assert noted.last_error == "rail: node unreachable"
    assert len(path.read_text(encoding="utf-8").splitlines()) == before
