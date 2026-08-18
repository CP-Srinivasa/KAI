"""Die Wachliste muss mindestens einen EINGANG enthalten, nicht nur Ausgänge.

Der TV-Webhook war vom 02.08. 17:23Z bis zum 08.08. tot — sechs Tage, unbemerkt.
Der Promotion-Timer lief die ganze Zeit ``enabled+active+success``, weil „0
offene Ereignisse" als Erfolg zählt. Die Ursache war nicht der Timer: die
Wachliste enthielt 13 Einträge, und **alle 13 waren Ausgänge**. Ein gesunder
Ausgang beweist keinen lebenden Eingang.

``tradingview_webhook_audit.jsonl`` ist der einzige Beleg dafür, dass überhaupt
noch etwas hereinkommt. Auf dem Pi trägt die Datei als letzte Änderung exakt den
Todeszeitpunkt.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.health_check import _FRESHNESS_PER_FILE_MIN, _check_data_freshness

INGRESS_FILE = "tradingview_webhook_audit.jsonl"


def _artifacts_with_ingress(tmp_path: Path, *, age_seconds: float) -> Path:
    adir = tmp_path / "artifacts"
    adir.mkdir(parents=True, exist_ok=True)
    path = adir / INGRESS_FILE
    # 2026-08-18: ein ANGENOMMENER Record, nicht nur eine frische Datei.
    # Der Waechter misst seit dem den letzten outcome=accepted -- eine blosse
    # Datei-Beruehrung ist kein eingehender Verkehr, sonst koennte jede
    # Abweisung (auch die eines Fremden) den Eingang gruen faerben.
    stamp_for_record = time.time() - age_seconds
    path.write_text(
        json.dumps(
            {
                "outcome": "accepted",
                "received_at": datetime.fromtimestamp(stamp_for_record, tz=UTC).isoformat(),
            }
        )
        + chr(10),
        encoding="utf-8",
    )
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))
    return adir


def test_eingangsstrom_hat_eine_freshness_schwelle() -> None:
    assert INGRESS_FILE in _FRESHNESS_PER_FILE_MIN
    threshold = _FRESHNESS_PER_FILE_MIN[INGRESS_FILE]
    # Grosszuegig genug fuer unregelmaessige Alerts, eng genug um Tage-Stille
    # zu fangen. Die reale Luecke war 6 Tage = 8640 min.
    assert 120 <= threshold <= 1440


def test_toter_eingang_erzeugt_einen_befund(tmp_path: Path) -> None:
    """Genau das Szenario vom 02.–08.08.: Datei existiert, wächst aber nicht."""
    adir = _artifacts_with_ingress(tmp_path, age_seconds=6 * 24 * 3600)

    issues, _ = _check_data_freshness(adir, datetime.now(UTC))

    ingress = [i for i in issues if "tradingview_ingress" in i.component]
    assert ingress, (
        "Ein sechs Tage toter Eingangsstrom muss einen Befund erzeugen — "
        f"gefunden: {[i.component for i in issues]}"
    )


def test_frischer_eingang_erzeugt_keinen_befund(tmp_path: Path) -> None:
    adir = _artifacts_with_ingress(tmp_path, age_seconds=60)

    issues, _ = _check_data_freshness(adir, datetime.now(UTC))

    assert not [i for i in issues if "tradingview_ingress" in i.component]
