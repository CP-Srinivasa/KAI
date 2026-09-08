"""Der Waechter des Pay-Stroms (Stream-Vertrag G4, D-CORE-006).

Der Strom ist ereignisgetrieben und im Default-Zustand gar nicht vorhanden.
Ueberwacht wird deshalb die FORM, nicht die Kadenz — und der Vertrag in
``config/stream_contracts.json`` muss genau das sagen, sonst waere die Zusage
eine andere als die Sonde.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.alerts.health_check_pay import check_pay_requests
from app.pay.store import PAY_REQUESTS_FILENAME

CONTRACTS = Path(__file__).resolve().parents[3] / "config" / "stream_contracts.json"


def _write(tmp_path: Path, *lines: str) -> Path:
    target = tmp_path / "pay" / PAY_REQUESTS_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def _good_line(payment_id: str = "pay_000000000001") -> str:
    return json.dumps(
        {
            "ts": "2026-09-08T12:00:00+00:00",
            "event": "request_created",
            "payload": {"payment_id": payment_id, "ref_hash": "a" * 64},
        }
    )


def test_kein_strom_ist_kein_befund(tmp_path: Path) -> None:
    """Default aus: niemand hat je eine Zahlung angefordert. Das ist normal."""
    assert check_pay_requests(tmp_path) == []


def test_ein_gesunder_strom_ist_still(tmp_path: Path) -> None:
    assert check_pay_requests(_write(tmp_path, _good_line(), _good_line("pay_2"))) == []


def test_eine_zerrissene_zeile_wird_gemeldet(tmp_path: Path) -> None:
    issues = check_pay_requests(_write(tmp_path, _good_line(), '{"ts": "x", "event"'))
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].component == "pay_requests"
    assert "line 2" in issues[0].message


def test_eine_zeile_ohne_payment_id_ist_wertlos(tmp_path: Path) -> None:
    orphan = json.dumps({"ts": "2026-09-08T12:00:00+00:00", "event": "x", "payload": {}})
    issues = check_pay_requests(_write(tmp_path, orphan))
    assert len(issues) == 1
    assert "payload without payment_id" in issues[0].message


def test_eine_zeile_ohne_pflichtfelder_ist_ein_befund(tmp_path: Path) -> None:
    issues = check_pay_requests(_write(tmp_path, json.dumps({"event": "request_created"})))
    assert len(issues) == 1
    assert "missing" in issues[0].message


def test_der_befund_ist_nie_kritisch(tmp_path: Path) -> None:
    """Der Strom kann eine Zuordnung verlieren, nie einen Satoshi.

    ``critical`` traegt ``check_payment_journal_chain`` ueber dem Geld-Journal.
    Zwei kritische Alarme fuer zwei sehr verschiedene Schwerelagen machen aus
    einem Befund ein Rauschen.
    """
    issues = check_pay_requests(_write(tmp_path, "{"))
    assert [issue.severity for issue in issues] == ["warning"]


def test_die_sonde_haengt_im_health_report() -> None:
    """Ein Waechter, den niemand ruft, ist eine Behauptung (Stream-Ratchet G4)."""
    import ast

    source = (Path(__file__).resolve().parents[3] / "app" / "alerts" / "health_check.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    report = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_health_check_report"
    )
    called = {
        node.func.id
        for node in ast.walk(report)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_check_pay_requests" in called


def test_der_vertrag_deklariert_einen_waechter_statt_einer_kadenz() -> None:
    contract = json.loads(CONTRACTS.read_text(encoding="utf-8"))["streams"][PAY_REQUESTS_FILENAME]
    assert contract["monitoring"] == "alternative_watcher"
    assert contract["watcher"] == "_check_pay_requests"
    assert contract["reader"] == "app/alerts/health_check_pay.py"
    assert "freshness_check" not in contract
