"""Repo-only LiteLLM service readiness; never invokes systemd or a gateway.

Diese Datei prueft, was VOR jeder Installation im Repo stimmen muss. Sie startet
nichts, kontaktiert nichts und aktiviert nichts — Installation und Aktivierung
sind getrennte Operator-Tore.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

UNIT = Path("deploy/systemd/kai-litellm.service")
CONFIG = Path("config/litellm.yaml")


def test_unit_uses_immutable_release_and_localhost_only() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/kai/current" in unit
    assert "/home/kai/current/config/litellm.yaml" in unit
    assert "--host 127.0.0.1" in unit
    assert "0.0.0.0" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/home/kai/ai_analyst_trading_bot" in unit


def test_das_binary_kommt_aus_dem_transport_baum_nicht_aus_dem_release_venv() -> None:
    """ADR 0019: `litellm[proxy]` verlangt `openai<3.0.0`, der Kern faehrt 3.6.0.

    Solange die Unit auf `/home/kai/current/.venv/bin/litellm` zeigte, benannte
    sie eine Datei, die es im Release-venv nicht geben KANN — der Konflikt ist
    genau der Grund fuer die Trennung. Der Health-Check erwartete deshalb seit
    Tagen eine Unit, die auf der Pi nicht existiert.

    Die Konfiguration bleibt ausdruecklich beim Release: sie gehoert zur
    Control-Plane, nicht zum Transport.
    """
    unit = UNIT.read_text(encoding="utf-8")
    zeile = next(z for z in unit.splitlines() if z.startswith("ExecStart="))

    assert "/home/kai/current/.venv/bin/litellm" not in zeile, zeile
    assert "/home/kai/current/scripts/pi_transport_exec.sh litellm" in zeile, zeile
    assert "/home/kai/current/config/litellm.yaml" in zeile, zeile


def test_der_verifizierer_liegt_im_release_und_ist_ausfuehrbar() -> None:
    """Er wird ueber `/home/kai/current/...` aufgerufen — also aus dem Release.

    Damit faellt er unter dessen Identitaet, und die Kette hat keine Stelle, an
    der ein unattestierter Schritt steht: `runtime-exec` prueft den Release,
    dieses Skript den Transport.
    """
    verifizierer = Path("scripts/pi_transport_exec.sh")
    assert verifizierer.is_file(), "die Unit ruft ein Skript auf, das es nicht gibt"


def test_was_systemd_direkt_startet_traegt_im_index_das_ausfuehrbar_bit() -> None:
    """Sonst endet der Start mit 203/EXEC, und die Datei ist trotzdem da.

    Der Release wird mit `cp -a` gebaut, das die Modi erhaelt — der Modus im
    Git-Index ist damit der, der auf der Pi ankommt. Die uebrigen Skripte im
    Repo sind bewusst 644: sie werden als `bash <datei>` aufgerufen. Dieses
    hier ruft systemd unmittelbar auf, und dafuer reicht 644 nicht.
    """
    fertig = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-s", "scripts/pi_transport_exec.sh"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).resolve().parents[2],
    )
    if fertig.returncode != 0 or not fertig.stdout.strip():
        pytest.skip("kein Git-Index verfuegbar")

    modus = fertig.stdout.split()[0]
    assert modus == "100755", f"Modus {modus} — systemd kann das nicht starten"


def test_die_unit_startet_kein_beliebiges_binary() -> None:
    """Kein PATH-Fallback, kein zweiter Startweg.

    Ein `litellm` ohne Pfad oder ein `sh -c` in der Unit waere die Luecke, die
    der Verifizierer schliessen soll.
    """
    unit = UNIT.read_text(encoding="utf-8")
    zeile = next(z for z in unit.splitlines() if z.startswith("ExecStart="))

    assert " sh -c" not in zeile and " bash -c" not in zeile, zeile
    for wort in zeile.removeprefix("ExecStart=").split():
        if wort.endswith("litellm") and wort != "litellm":
            assert wort.startswith("/"), f"nicht absolut: {wort}"


def test_config_disables_litellm_retry_and_second_telemetry() -> None:
    config = CONFIG.read_text(encoding="utf-8")
    assert "num_retries: 0" in config
    assert "telemetry: false" in config
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in config
    for alias in (
        "kai-bulk",
        "kai-standard",
        "kai-reasoning",
        "kai-critical",
        "kai-stt",
        "kai-deepseek",
    ):
        assert f"model_name: {alias}" in config
    assert "sk-" not in config


def test_der_transport_ist_exakt_gepinnt() -> None:
    """Eine Spanne waere zwei Gateways unter einem repo_sha.

    Das Release-Modell verspricht, dass ein ``repo_sha`` einen bestimmten Baum
    bezeichnet. Ein ``litellm>=…`` im Manifest wuerde dieses Versprechen an der
    Stelle brechen, an der es am meisten zaehlt: dem Prozess, der die Aufrufe
    tatsaechlich hinausschickt. Die Unit startet ein Binary — es muss benannt
    sein, nicht nur ungefaehr bekannt.
    """
    manifest = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    extra = manifest["project"]["optional-dependencies"]["litellm"]
    assert extra, "die Unit startet ein Binary, das in keinem Manifest steht"
    for eintrag in extra:
        assert "==" in eintrag, f"nicht exakt gepinnt: {eintrag}"
        assert ">=" not in eintrag and "~=" not in eintrag, eintrag
    assert any(e.startswith("litellm") for e in extra)


def test_der_transport_ist_optional_und_nicht_im_kern() -> None:
    """Ohne Transport faellt KAI auf den Direktpfad zurueck, statt zu scheitern."""
    manifest = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    kern = " ".join(manifest["project"]["dependencies"])
    assert "litellm" not in kern, "der Transport gehoert nicht in den Kern"


def test_die_unit_traegt_kein_geheimnis() -> None:
    """Schluessel kommen aus der EnvironmentFile ausserhalb des Release-Baums."""
    unit = UNIT.read_text(encoding="utf-8")
    assert "EnvironmentFile=/home/kai/ai_analyst_trading_bot/.env" in unit, (
        "dieselbe Konvention wie kai-server: Zustand und Geheimnisse liegen "
        "ausserhalb des unveraenderlichen Release-Baums"
    )
    for muster in ("sk-", "API_KEY=", "MASTER_KEY="):
        assert muster not in unit, muster


def test_die_unit_ist_release_gebunden_und_faellt_damit_unter_den_guard() -> None:
    """#869: release-gebundene Units duerfen ohne aktives Release nicht kopiert werden.

    Der Ausfall vom 2026-09-04 entstand, weil Units mit
    ``WorkingDirectory=/home/kai/current`` nach ``/etc`` kopiert wurden, bevor
    ``current`` existierte. Eine sechste solche Unit ist nur dann harmlos, wenn
    der Guard sie auch als release-gebunden ERKENNT — also ``runtime-exec`` mit
    ``--repo`` fuehrt, so wie die fuenf bestehenden.
    """
    unit = UNIT.read_text(encoding="utf-8")
    assert "runtime-exec" in unit
    assert "--repo /home/kai/current" in unit
    assert "--unit %n" in unit


def test_kein_zweiter_deployment_stack() -> None:
    """Ein eigenes Compose oder ein zweiter Installer waere eine zweite Wahrheit."""
    verboten = [
        Path("docker-compose.litellm.yml"),
        Path("deploy/litellm"),
        Path("scripts/install_litellm.sh"),
    ]
    for pfad in verboten:
        assert not pfad.exists(), f"zweiter Deployment-Stack: {pfad}"
