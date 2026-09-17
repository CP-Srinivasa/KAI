"""SCB-Erzeuger auf dem Pi (Sprint S-0917 C3, System-Audit P0-1).

Der Monitor ``app.lightning.backup_monitor`` prueft eine lokale Kopie, aber auf
dem Pi hat sie nie jemand erzeugt (Audit 16.09.: SCB 74 Tage alt, Monitor
``not_configured``). Der Exporter holt sie ueber die lnd-REST-API mit dem
Read-only-Macaroon -- derselbe Weg, den das Dashboard schon nutzt, kein neuer
SSH-Trust zum Node.

Die Falle, gegen die diese Tests stehen: jeder Export ist frisch verschluesselt,
die Bytes aendern sich bei JEDEM Aufruf. Wer stuendlich neu schreibt, laesst den
Monitor stuendlich ``changed`` melden. Der Exporter vergleicht deshalb die vom
Node verifizierte KANALMENGE der gespeicherten Datei mit der des Exports und
schreibt nur bei Abweichung neu; sonst bestaetigt er die Frische per mtime.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from app.core.lightning_settings import LightningSettings
from app.lightning.backup_monitor import check_scb_drift
from app.lightning.client import LndRestClient
from app.lightning.scb_export import refresh_scb_copy, run_export_once

TXID_A = "03147302283cf7a4c29efab215e63875566707979c6c5d1d521078d678f6ec77"
TXID_B = "cf5fe0585efd98789aa8e8c5d9298729666aade45bb02f2946cb0b2cda0f9011"


def _chan_point(txid: str, index: int = 0) -> dict[str, object]:
    return {
        "funding_txid_bytes": base64.b64encode(bytes.fromhex(txid)[::-1]).decode(),
        "output_index": index,
    }


class FakeNode:
    """Minimaler lnd: jeder Export liefert NEUE Bytes fuer dieselbe Kanalmenge."""

    def __init__(self, channels: list[str]) -> None:
        self.channels = channels
        self.known: dict[bytes, list[str]] = {}
        self.exports = 0
        self.verify_calls = 0
        self.fail_get = False
        self.reject_all_verify = False

    def export(self) -> bytes:
        self.exports += 1
        blob = f"scb-{self.exports}-{','.join(self.channels)}".encode()
        self.known[blob] = list(self.channels)
        return blob

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/channels/backup" and request.method == "GET":
            if self.fail_get:
                return httpx.Response(503, text="unavailable")
            blob = self.export()
            return httpx.Response(
                200,
                json={
                    "multi_chan_backup": {
                        "chan_points": [_chan_point(t) for t in self.channels],
                        "multi_chan_backup": base64.b64encode(blob).decode(),
                    }
                },
            )
        if request.url.path == "/v1/channels/backup/verify" and request.method == "POST":
            self.verify_calls += 1
            body = json.loads(request.content)
            blob = base64.b64decode(body["multi_chan_backup"]["multi_chan_backup"])
            if self.reject_all_verify or blob not in self.known:
                return httpx.Response(500, json={"message": "unable to unpack backup"})
            return httpx.Response(200, json={"chan_points": [f"{t}:0" for t in self.known[blob]]})
        return httpx.Response(404)


def _client(node: FakeNode) -> LndRestClient:
    return LndRestClient(
        base_url="https://node:8080",
        macaroon_hex="ab",
        transport=httpx.MockTransport(node.handler),
    )


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_missing_copy_is_written_from_a_verified_export(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    scb = tmp_path / "scb" / "channel.backup"

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "written"
    assert report["reason"] == "missing"
    assert report["chan_points"] == [f"{TXID_A}:0"]
    assert scb.read_bytes() in node.known
    if os.name == "posix":
        assert (scb.stat().st_mode & 0o777) == 0o600


def test_unchanged_channel_set_keeps_bytes_and_refreshes_mtime(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    scb = tmp_path / "channel.backup"
    _run(refresh_scb_copy(_client(node), scb))
    before = scb.read_bytes()
    os.utime(scb, (1_000_000_000, 1_000_000_000))

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "current"
    assert scb.read_bytes() == before  # neu verschluesselter Export wurde NICHT geschrieben
    assert scb.stat().st_mtime > 1_000_000_000 + 3600


def test_monitor_stays_stable_across_hourly_exports(tmp_path: Path) -> None:
    """Die eigentliche Falle: zwei Laeufe, identische Kanalmenge -> kein ``changed``."""
    node = FakeNode([TXID_A])
    scb = tmp_path / "channel.backup"
    baseline = tmp_path / "scb_baseline.json"

    _run(refresh_scb_copy(_client(node), scb))
    first = check_scb_drift(scb, baseline_path=baseline)
    _run(refresh_scb_copy(_client(node), scb))
    second = check_scb_drift(scb, baseline_path=baseline)

    assert first["state"] == "no_baseline"
    assert second["state"] == "stable"
    assert node.exports == 2


def test_changed_channel_set_rewrites_and_monitor_reports_changed(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    scb = tmp_path / "channel.backup"
    baseline = tmp_path / "scb_baseline.json"
    _run(refresh_scb_copy(_client(node), scb))
    check_scb_drift(scb, baseline_path=baseline)

    node.channels = [TXID_A, TXID_B]  # Kanal eroeffnet
    report = _run(refresh_scb_copy(_client(node), scb))
    drift = check_scb_drift(scb, baseline_path=baseline)

    assert report["state"] == "written"
    assert report["reason"] == "channel_set_changed"
    assert report["previous_chan_points"] == [f"{TXID_A}:0"]
    assert sorted(report["chan_points"]) == sorted([f"{TXID_A}:0", f"{TXID_B}:0"])
    assert drift["state"] == "changed"


def test_stored_copy_the_node_cannot_verify_is_replaced(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    scb = tmp_path / "channel.backup"
    scb.write_bytes(b"corrupt")

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "written"
    assert report["reason"] == "stored_unverifiable"
    assert scb.read_bytes() in node.known


def test_node_unreachable_leaves_existing_copy_untouched(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    scb = tmp_path / "channel.backup"
    _run(refresh_scb_copy(_client(node), scb))
    before = scb.read_bytes()
    os.utime(scb, (1_000_000_000, 1_000_000_000))
    node.fail_get = True

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "export_failed"
    assert scb.read_bytes() == before
    # mtime bleibt alt: der Monitor meldet nach scb_max_age_seconds ``stale``.
    assert scb.stat().st_mtime == 1_000_000_000


def test_export_the_node_rejects_is_never_written(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    node.reject_all_verify = True
    scb = tmp_path / "channel.backup"

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "export_failed"
    assert not scb.exists()
    assert not list(tmp_path.iterdir())  # auch keine Temp-Datei liegen geblieben


def test_node_without_channels_writes_nothing(tmp_path: Path) -> None:
    node = FakeNode([])
    scb = tmp_path / "channel.backup"

    report = _run(refresh_scb_copy(_client(node), scb))

    assert report["state"] == "no_channels"
    assert not scb.exists()


def test_run_once_is_idle_when_not_configured() -> None:
    cfg = LightningSettings(enabled=True, tls_cert_path="test-tls.pem", scb_path="")
    report = _run(run_export_once(cfg))
    assert report["state"] == "not_configured"
    assert report["exit_code"] == 0


def test_run_once_is_idle_when_lightning_disabled(tmp_path: Path) -> None:
    cfg = LightningSettings(enabled=False, scb_path=str(tmp_path / "channel.backup"))
    report = _run(run_export_once(cfg))
    assert report["state"] == "disabled"
    assert report["exit_code"] == 0


def test_run_once_fails_loudly_when_export_fails(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    node.fail_get = True
    cfg = LightningSettings(
        enabled=True, tls_cert_path="test-tls.pem", scb_path=str(tmp_path / "channel.backup")
    )

    report = _run(run_export_once(cfg, client=_client(node)))

    assert report["state"] == "export_failed"
    assert report["exit_code"] == 1


def test_monitor_unit_runs_the_exporter_first_and_fails_on_export_error() -> None:
    root = Path(__file__).resolve().parents[2]
    service = (root / "deploy/systemd/kai-ln-scb-monitor.service").read_text(encoding="utf-8")
    pre = [line for line in service.splitlines() if line.startswith("ExecStartPre=")]
    start = [line for line in service.splitlines() if line.startswith("ExecStart=")]

    assert len(pre) == 1 and "python -m app.lightning.scb_export" in pre[0]
    # Ohne "-"-Praefix: ein gescheiterter Export macht die Unit failed -> OnFailure-Alarm.
    assert not pre[0].startswith("ExecStartPre=-")
    assert start and "python -m app.lightning.backup_monitor" in start[0]
    assert service.index("ExecStartPre=") < service.index("ExecStart=/")


def test_report_timestamps_are_utc(tmp_path: Path) -> None:
    node = FakeNode([TXID_A])
    report = _run(refresh_scb_copy(_client(node), tmp_path / "channel.backup"))
    assert datetime.fromisoformat(report["checked_at"]).tzinfo == UTC
