"""SCB-Erzeuger auf dem Pi — frische, vom Node verifizierte ``channel.backup``.

System-Audit 2026-09-16 P0-1: Der Monitor (:mod:`app.lightning.backup_monitor`)
prueft eine lokale Kopie, aber auf dem Pi hat nie etwas sie erzeugt. Das einzige
Off-Node-SCB lag 74 Tage alt auf dem Laptop, dessen stuendlicher Pull seit der
Key-Rotation vom 31.08. scheiterte. Dieses Modul schliesst die Luecke.

**Kein neuer Vertrauensweg.** Gelesen wird ueber die lnd-REST-API mit dem
Read-only-Macaroon (``offchain:read``) -- derselbe Weg, den das Dashboard schon
nutzt. Kein SSH zum Node, kein Schreibrecht am Node; die Entscheidung "Weg A"
(14.07., kein SSH-Trust vom Pi zum Node) bleibt unberuehrt.

**Die Falle.** lnd verschluesselt jeden Export neu; die Bytes -- und damit der
Hash, den der Monitor vergleicht -- aendern sich bei JEDEM Aufruf, auch wenn
sich kein Kanal geaendert hat. Stuendlich neu zu schreiben hiesse stuendlich
``changed``. Stabil ist nur die Kanalmenge. Deshalb:

1. Export holen; seine Kanalmenge ist die Sollmenge.
2. Die GESPEICHERTE Datei vom Node verifizieren lassen. Deckt sie genau die
   Sollmenge ab, bleibt sie Byte fuer Byte liegen; nur ihre mtime wird
   erneuert -- "vom Node als aktuell bestaetigt am". Der Monitor sieht
   ``stable`` und misst Frische an der letzten Bestaetigung.
3. Sonst (fehlt, nicht verifizierbar, andere Kanalmenge) den Export selbst
   verifizieren und atomar schreiben (0600). Der Monitor meldet dann
   ``changed`` -- genau der Moment, in dem das Backup neu archiviert werden muss.

Faellt der Node aus, bleibt die Datei unangetastet und altert; der Monitor
meldet nach ``scb_max_age_seconds`` ``stale``. Ein gescheiterter Export endet
mit Exit 1, damit die Unit ``failed`` wird und der OnFailure-Alarm greift.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.lightning_settings import LightningSettings
from app.lightning.client import LightningUnavailableError, LndRestClient

logger = logging.getLogger(__name__)


def _export_chan_points(multi: dict[str, Any]) -> list[str]:
    points: list[str] = []
    for cp in multi.get("chan_points") or []:
        if not isinstance(cp, dict):
            continue
        txid = str(cp.get("funding_txid_str") or "")
        if not txid:
            raw = base64.b64decode(str(cp.get("funding_txid_bytes") or ""))
            txid = raw[::-1].hex()
        points.append(f"{txid}:{int(cp.get('output_index') or 0)}")
    return sorted(points)


def _verified_chan_points(response: dict[str, Any]) -> list[str]:
    return sorted(str(p) for p in response.get("chan_points") or [])


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".channel.backup.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


async def _stored_chan_points(client: LndRestClient, path: Path) -> list[str] | None:
    """Kanalmenge der gespeicherten Datei laut Node; ``None`` = fehlt/unlesbar/abgelehnt."""
    try:
        stored = path.read_bytes()
    except OSError:
        return None
    try:
        return _verified_chan_points(await client.verify_channel_backup(stored))
    except LightningUnavailableError:
        return None


async def refresh_scb_copy(
    client: LndRestClient, scb_path: Path | str, *, now: datetime | None = None
) -> dict[str, Any]:
    """Einen Lauf ausfuehren. Wirft nie; ``state`` sagt, was passiert ist."""
    path = Path(scb_path)
    checked_at = (now or datetime.now(UTC)).astimezone(UTC)
    report: dict[str, Any] = {"scb_path": str(path), "checked_at": checked_at.isoformat()}

    try:
        multi = (await client.export_channel_backup()).get("multi_chan_backup") or {}
        blob = base64.b64decode(str(multi.get("multi_chan_backup") or ""))
    except (LightningUnavailableError, ValueError) as exc:
        return {**report, "state": "export_failed", "detail": f"export: {exc}"}

    wanted = _export_chan_points(multi)
    report["chan_points"] = wanted
    if not wanted:
        return {**report, "state": "no_channels", "detail": "node has no open channels"}

    previous = await _stored_chan_points(client, path) if path.exists() else None
    if previous == wanted:
        try:
            os.utime(path, (checked_at.timestamp(), checked_at.timestamp()))
        except OSError as exc:
            # Ohne erneuerte mtime wuerde der Monitor spaeter "stale" melden, ohne
            # dass jemand weiss, warum -- lieber jetzt laut scheitern.
            return {**report, "state": "export_failed", "detail": f"touch: {exc}"}
        return {**report, "state": "current"}

    if not path.exists():
        reason = "missing"
    elif previous is None:
        reason = "stored_unverifiable"
    else:
        reason = "channel_set_changed"
        report["previous_chan_points"] = previous

    try:
        confirmed = _verified_chan_points(await client.verify_channel_backup(blob))
    except LightningUnavailableError as exc:
        return {**report, "state": "export_failed", "detail": f"verify export: {exc}"}
    if confirmed != wanted:
        return {
            **report,
            "state": "export_failed",
            "detail": f"export covers {confirmed}, node lists {wanted}",
        }
    try:
        _write_atomic(path, blob)
    except OSError as exc:
        return {**report, "state": "export_failed", "detail": f"write: {exc}"}
    return {**report, "state": "written", "reason": reason, "size_bytes": len(blob)}


def _client_from(cfg: LightningSettings) -> LndRestClient:
    macaroon_hex, macaroon_path = cfg.macaroon_credentials("read")
    return LndRestClient(
        base_url=cfg.base_url,
        macaroon_hex=macaroon_hex,
        macaroon_path=macaroon_path,
        tls_cert_path=cfg.tls_cert_path,
        timeout=cfg.timeout_seconds,
    )


async def run_export_once(
    cfg: LightningSettings | None = None, *, client: LndRestClient | None = None
) -> dict[str, Any]:
    """Systemd-Lauf: idle ohne Konfiguration, Exit 1 nur bei gescheitertem Export."""
    cfg = cfg or LightningSettings()
    if not cfg.enabled:
        return {"state": "disabled", "exit_code": 0}
    scb_path = cfg.scb_path.strip()
    if not scb_path:
        return {
            "state": "not_configured",
            "detail": "APP_LN_SCB_PATH is not configured — exporter idle",
            "exit_code": 0,
        }
    try:
        client = client or _client_from(cfg)
    except LightningUnavailableError as exc:
        return {"state": "export_failed", "detail": f"client: {exc}", "exit_code": 1}
    report = await refresh_scb_copy(client, scb_path)
    report["exit_code"] = 1 if report["state"] == "export_failed" else 0
    return report


def main() -> int:
    try:
        report = asyncio.run(run_export_once())
    except Exception as exc:  # noqa: BLE001 — jede Konfigurationspanne muss sichtbar sein
        report = {
            "state": "export_failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "exit_code": 1,
        }
    print(json.dumps(report, sort_keys=True))
    return int(report["exit_code"])


__all__ = ["refresh_scb_copy", "run_export_once"]


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
