"""scripts/payment_drain_check.py — kein Release, solange Geld unterwegs ist.

Lueckenregister 26.09.: ``pi_release_deploy.sh`` startet alle sechs Dienste neu,
kai-server eingeschlossen. Ein Neustart mitten in einem Send ist genau der Fall,
den der Reconciler erst hinterher klaeren muesste. Die Pruefung ist rein lesend.
"""

from __future__ import annotations

from pathlib import Path

from scripts import payment_drain_check as drain

from tests.unit.payments.test_service import a_request, a_service


async def _intent(tmp_path: Path, destination: str, key: str) -> tuple[object, str]:
    service = a_service(tmp_path, destination=destination, approval_threshold_sat=1_000_000)
    view = await service.create_intent(a_request(destination=destination), key)
    return service, view.intent_id


def test_ohne_journal_ist_nichts_unterwegs(tmp_path: Path) -> None:
    assert drain.in_motion(tmp_path / "fehlt.jsonl") == []
    assert drain.main(["--journal", str(tmp_path / "fehlt.jsonl")]) == 0


async def test_ein_freigegebener_intent_blockiert(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "payment_journal.jsonl"
    _service, intent_id = await _intent(tmp_path, "sim:inflight:alice", "idem-drain-0000000001")

    blocked = drain.in_motion(path)
    assert [i for i, _ in blocked] == [intent_id]
    assert drain.main(["--journal", str(path)]) == drain.EXIT_BLOCKED
    assert "DRAIN_BLOCKED" in capsys.readouterr().out


async def test_ein_abgeschlossener_intent_blockiert_nicht(tmp_path: Path) -> None:
    path = tmp_path / "payment_journal.jsonl"
    service, intent_id = await _intent(tmp_path, "sim:settle:alice", "idem-drain-0000000002")
    await service.execute(intent_id)  # type: ignore[attr-defined]

    assert drain.in_motion(path) == []
    assert drain.main(["--journal", str(path)]) == 0


def test_eine_kaputte_kette_blockiert(tmp_path: Path) -> None:
    path = tmp_path / "payment_journal.jsonl"
    path.write_text('{"kaputt": true}\n', encoding="utf-8")
    assert drain.main(["--journal", str(path)]) == drain.EXIT_BLOCKED
