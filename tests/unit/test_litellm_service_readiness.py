"""Repo-only LiteLLM service readiness; never invokes systemd or a gateway.

Diese Datei prueft, was VOR jeder Installation im Repo stimmen muss. Sie startet
nichts, kontaktiert nichts und aktiviert nichts — Installation und Aktivierung
sind getrennte Operator-Tore.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

UNIT = Path("deploy/systemd/kai-litellm.service")
CONFIG = Path("config/litellm.yaml")


def test_unit_uses_immutable_release_and_localhost_only() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/kai/current" in unit
    assert "/home/kai/current/.venv/bin/litellm" in unit
    assert "/home/kai/current/config/litellm.yaml" in unit
    assert "--host 127.0.0.1" in unit
    assert "0.0.0.0" not in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/home/kai/ai_analyst_trading_bot" in unit


def test_config_disables_litellm_retry_and_second_telemetry() -> None:
    config = CONFIG.read_text(encoding="utf-8")
    assert "num_retries: 0" in config
    assert "telemetry: false" in config
    assert "master_key: os.environ/LITELLM_MASTER_KEY" in config
    for alias in ("kai-bulk", "kai-standard", "kai-reasoning", "kai-critical", "kai-stt"):
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
