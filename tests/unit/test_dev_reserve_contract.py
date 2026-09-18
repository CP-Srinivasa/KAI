"""Die Entwicklerreserve ist von der Laufzeit getrennt — gemessen, nicht erklaert.

ADR 0020 / D-CORE-009: Ein zweiter LiteLLM-Proxy (``config/litellm_dev.yaml``)
traegt drei ``kai-dev-*``-Routen fuer einen Entwicklerclient (OpenCode). Er ist
KEIN Teil von ``app/ai``, keine Stufe der Laufzeit-Fallback-Kette und keine
systemd-Unit. Was ihn erreicht, ist ein eigener Schluessel; was ihn verlaesst,
geht nur an 127.0.0.1.

Jede Zusicherung, die man dazu in einem Dokument lesen kann, steht hier als
Pruefung. Verschiebt ein spaeterer Aenderer die Grenze — eine Dev-Route in der
Laufzeit-YAML, ein ``KAI_DEV_``-Name in ``app/ai``, ein Master-Key im Client —
faellt es beim Merge auf und nicht im Betrieb.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DEV_CONFIG = REPO / "config" / "litellm_dev.yaml"
RUNTIME_CONFIG = REPO / "config" / "litellm.yaml"
VORLAGE = REPO / ".env.example"
OPENCODE = REPO / "opencode.json"
SKRIPT = REPO / "scripts" / "dev_reserve.sh"
APP_AI = REPO / "app" / "ai"
UNITS = REPO / "deploy" / "systemd"

DEV_ROUTEN = {"kai-dev-economy", "kai-dev-code", "kai-dev-frontier"}
_VERWEIS = re.compile(r"os\.environ/([A-Z][A-Z0-9_]*)")


def _dev_yaml() -> dict:
    return yaml.safe_load(DEV_CONFIG.read_text(encoding="utf-8"))


def _vorlagen_namen() -> set[str]:
    return {
        zeile.split("=", 1)[0].strip()
        for zeile in VORLAGE.read_text(encoding="utf-8").splitlines()
        if "=" in zeile and not zeile.lstrip().startswith("#")
    }


# --- der Dev-Proxy selbst ----------------------------------------------------


def test_die_dev_konfiguration_traegt_genau_die_drei_dev_routen() -> None:
    namen = {eintrag["model_name"] for eintrag in _dev_yaml()["model_list"]}
    assert namen == DEV_ROUTEN


def test_jede_dev_route_holt_modell_und_schluessel_aus_der_umgebung() -> None:
    """Kein Anbieter und kein Schluessel steht im Repository."""
    for eintrag in _dev_yaml()["model_list"]:
        params = eintrag["litellm_params"]
        assert params["model"].startswith("os.environ/KAI_DEV_LITELLM_"), eintrag
        assert params["api_key"].startswith("os.environ/KAI_DEV_LITELLM_"), eintrag
        assert params["api_key"].endswith("_API_KEY"), eintrag


def test_der_dev_proxy_hat_einen_eigenen_schluessel() -> None:
    """Der Laufzeit-Master-Key darf den Reserve-Proxy nicht oeffnen."""
    text = DEV_CONFIG.read_text(encoding="utf-8")
    assert _dev_yaml()["general_settings"]["master_key"] == "os.environ/LITELLM_DEV_MASTER_KEY"
    assert "os.environ/LITELLM_MASTER_KEY" not in text


def test_der_dev_proxy_verspricht_kein_budget_das_er_nicht_haelt() -> None:
    """``max_budget`` wirkt nur mit Datenbank; ohne sie ist die Zeile fail-open."""
    einstellungen = _dev_yaml().get("litellm_settings", {})
    assert "max_budget" not in einstellungen
    assert "budget_duration" not in einstellungen
    assert einstellungen["num_retries"] == 0


def test_jede_dev_variable_steht_in_der_vorlage_und_ist_leer() -> None:
    verlangt = set(_VERWEIS.findall(DEV_CONFIG.read_text(encoding="utf-8")))
    assert len(verlangt) == 7, sorted(verlangt)

    namen = _vorlagen_namen()
    fehlend = sorted(name for name in verlangt if name not in namen)
    assert not fehlend, f"in litellm_dev.yaml verlangt, in .env.example nicht erklaert: {fehlend}"

    for zeile in VORLAGE.read_text(encoding="utf-8").splitlines():
        name = zeile.split("=", 1)[0]
        if name in verlangt:
            assert zeile.strip() == f"{name}=", zeile


# --- die Grenze zur Laufzeit -------------------------------------------------


def test_die_laufzeit_konfiguration_kennt_keine_dev_route() -> None:
    text = RUNTIME_CONFIG.read_text(encoding="utf-8")
    assert "kai-dev-" not in text
    assert "KAI_DEV_" not in text
    assert "LITELLM_DEV_MASTER_KEY" not in text


def test_die_dev_konfiguration_kennt_keine_laufzeit_route() -> None:
    text = DEV_CONFIG.read_text(encoding="utf-8")
    laufzeit = {
        eintrag["model_name"]
        for eintrag in yaml.safe_load(RUNTIME_CONFIG.read_text(encoding="utf-8"))["model_list"]
    }
    for alias in laufzeit:
        assert f"model_name: {alias}\n" not in text, alias
    assert "KAI_LITELLM_" not in text  # keine Laufzeit-Variable, auch nicht als Vorgabe


def test_app_ai_kennt_die_entwicklerreserve_nicht() -> None:
    """Die Inferenz-Ebene beraet; sie hat keinen Weg zum Entwicklerproxy."""
    treffer = {}
    for pfad in sorted(APP_AI.rglob("*.py")):
        if "__pycache__" in pfad.parts:
            continue
        text = pfad.read_text(encoding="utf-8")
        gefunden = [m for m in ("kai-dev-", "KAI_DEV_", "litellm_dev", ":4001") if m in text]
        if gefunden:
            treffer[pfad.name] = gefunden
    assert treffer == {}


def test_es_gibt_keine_unit_und_keinen_timer_fuer_den_dev_proxy() -> None:
    """Die Reserve wird von Hand gestartet — nichts laeuft ungefragt mit."""
    treffer = sorted(
        p.name for p in UNITS.iterdir() if "litellm_dev" in p.read_text(encoding="utf-8")
    )
    assert treffer == []
    assert not any("dev-reserve" in p.name or "dev_reserve" in p.name for p in UNITS.iterdir())


# --- das Startskript ---------------------------------------------------------


def test_das_skript_bindet_nur_loopback_und_einen_eigenen_port() -> None:
    text = SKRIPT.read_text(encoding="utf-8")
    assert "HOST=127.0.0.1" in text
    assert "PORT=4001" in text
    assert "0.0.0.0" not in text
    assert 'litellm --config "$konfig" --host "$HOST" --port "$PORT"' in text


def test_das_skript_reicht_nur_dev_variablen_an_den_proxy() -> None:
    """Kein ``set -a; . .env`` — die Reserve sieht keinen Produktionsschluessel."""
    text = SKRIPT.read_text(encoding="utf-8")
    assert "set -a" not in text
    assert "source " not in text
    assert "LITELLM_DEV_MASTER_KEY=*|KAI_DEV_LITELLM_*=*" in text
    assert "MOONSHOT_API_KEY" not in text
    assert "DEEPSEEK_API_KEY" not in text
    assert "LITELLM_MASTER_KEY=" not in text.replace("LITELLM_DEV_MASTER_KEY=", "")


def test_das_skript_startet_aus_dem_attestierten_transport() -> None:
    text = SKRIPT.read_text(encoding="utf-8")
    assert "scripts/pi_transport_exec.sh" in text
    assert "config/litellm_dev.yaml" in text
    assert "DEV_RESERVE_ENV_INCOMPLETE" in text  # fehlt eine Variable: kein Start


def test_die_probe_schliesst_bei_fehlender_kostenmessung() -> None:
    text = SKRIPT.read_text(encoding="utf-8")
    assert "x-litellm-response-cost" in text
    assert "kosten_null" in text and "kosten_fehlen" in text
    assert "DEV_RESERVE_FAIL_CLOSED" in text
    standard = text.split("ROUTEN_STANDARD=", 1)[1].split("\n", 1)[0]
    assert "kai-dev-frontier" not in standard  # die Eskalationsstufe kostet nie ungefragt


# --- der Client --------------------------------------------------------------


def test_opencode_spricht_nur_den_dev_proxy_durch_den_tunnel() -> None:
    konfig = json.loads(OPENCODE.read_text(encoding="utf-8"))
    anbieter = konfig["provider"]
    assert list(anbieter) == ["kai-litellm-dev"]
    optionen = anbieter["kai-litellm-dev"]["options"]
    assert optionen["baseURL"] == "http://127.0.0.1:4001/v1"
    assert optionen["apiKey"] == "{env:KAI_DEV_LITELLM_KEY}"
    assert set(anbieter["kai-litellm-dev"]["models"]) == DEV_ROUTEN
    assert konfig["model"] == "kai-litellm-dev/kai-dev-code"
    assert not konfig["model"].endswith("kai-dev-frontier")


def test_opencode_traegt_keinen_schluessel_und_keinen_laufzeit_namen() -> None:
    text = OPENCODE.read_text(encoding="utf-8")
    assert "LITELLM_MASTER_KEY" not in text
    assert "sk-" not in text
    assert ":4000" not in text
    for laufzeit_alias in ("kai-standard", "kai-kimi-research", "kai-deepseek"):
        assert laufzeit_alias not in text


def test_opencode_darf_weder_mergen_noch_deployen_noch_geheimnisse_lesen() -> None:
    rechte = json.loads(OPENCODE.read_text(encoding="utf-8"))["permission"]
    bash = rechte["bash"]
    assert bash["*"] == "ask"
    for verboten in ("git push*", "git merge*", "gh pr merge*", "ssh *", "sudo *", "systemctl *"):
        assert bash[verboten] == "deny", verboten
    # Letzte passende Regel gewinnt: die Verbote muessen NACH "*" stehen.
    schluessel = list(bash)
    assert schluessel.index("*") < schluessel.index("git push*")
    lesen = rechte["read"]
    assert lesen["*.env"] == "deny" and lesen["*.env.*"] == "deny"
    assert lesen["*.env.example"] == "allow"
    assert rechte["edit"] == "ask"
    assert rechte["external_directory"] == "deny"


# --- der Start, ausgefuehrt --------------------------------------------------

def _find_bash() -> str | None:
    """Prefer Git Bash on Windows; ``System32/bash.exe`` is a WSL launcher.

    The path adapter below intentionally emits MSYS paths (``/c/...``). Feeding
    those to WSL made the two executable contract tests fail before the script
    under test was reached.
    """
    if os.name == "nt":
        for candidate in (
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/bin/bash.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Git/usr/bin/bash.exe",
        ):
            if candidate.is_file():
                return str(candidate)
    return shutil.which("bash")


_BASH = _find_bash()
_ALLE_DEV_NAMEN = (
    "LITELLM_DEV_MASTER_KEY",
    "KAI_DEV_LITELLM_ECONOMY_MODEL",
    "KAI_DEV_LITELLM_ECONOMY_API_KEY",
    "KAI_DEV_LITELLM_CODE_MODEL",
    "KAI_DEV_LITELLM_CODE_API_KEY",
    "KAI_DEV_LITELLM_FRONTIER_MODEL",
    "KAI_DEV_LITELLM_FRONTIER_API_KEY",
)
_PRODUKTION = ("LITELLM_MASTER_KEY", "MOONSHOT_API_KEY", "OPENAI_API_KEY")


def _bash_pfad(pfad: Path) -> str:
    s = pfad.as_posix()
    if os.name == "nt" and len(s) > 2 and s[1:3] == ":/":
        s = "/" + s[0].lower() + s[2:]
    return s


def _kulisse(tmp_path: Path, *, ohne: str | None = None) -> tuple[Path, Path]:
    """Ein Wegwerf-Release mit Sonden-Transport und eine .env mit Produktionswerten."""
    release = tmp_path / "release"
    (release / "scripts").mkdir(parents=True)
    (release / "config").mkdir()
    sonde = release / "scripts" / "pi_transport_exec.sh"
    zeilen = ["#!/usr/bin/env bash", 'echo "ARGS=$*"']
    for name in _ALLE_DEV_NAMEN + _PRODUKTION:
        zeilen.append(
            f'if [ -n "${{{name}:-}}" ]; then echo "{name}=SET"; else echo "{name}=MISSING"; fi'
        )
    sonde.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    sonde.chmod(0o755)
    (release / "config" / "litellm_dev.yaml").write_text("model_list: []\n", encoding="utf-8")

    env = tmp_path / "env"
    werte = [f"{name}=prod-{name.lower()}" for name in _PRODUKTION]
    for name in _ALLE_DEV_NAMEN:
        if name == ohne:
            werte.append(f"{name}=")
        elif name.endswith("_API_KEY"):
            werte.append(f'{name}="dev-{name.lower()}"')  # mit Anfuehrungszeichen, absichtlich
        else:
            werte.append(f"{name}=anbieter/modell-{name.lower()}")
    # LF wie auf der Pi; ein CR am Zeilenende waere sonst ein "Wert".
    env.write_text("\n".join(werte) + "\n", encoding="utf-8", newline="\n")
    return release, env


def _starte_proxy(release: Path, env: Path) -> subprocess.CompletedProcess[str]:
    umgebung = {k: v for k, v in os.environ.items() if k not in _ALLE_DEV_NAMEN + _PRODUKTION}
    umgebung["KAI_DEV_RESERVE_REPO"] = _bash_pfad(release)
    umgebung["KAI_DEV_RESERVE_ENV"] = _bash_pfad(env)
    return subprocess.run(  # noqa: S603
        [_BASH or "bash", _bash_pfad(SKRIPT), "proxy"],
        capture_output=True,
        text=True,
        env=umgebung,
        timeout=60,
    )


@pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")
def test_der_start_reicht_genau_die_dev_werte_durch_und_keinen_produktionswert(
    tmp_path: Path,
) -> None:
    """Die .env traegt Produktionsschluessel; der Transport darf sie nicht sehen."""
    release, env = _kulisse(tmp_path)

    fertig = _starte_proxy(release, env)

    assert fertig.returncode == 0, fertig.stderr
    assert "ARGS=litellm --config " in fertig.stdout
    assert "/config/litellm_dev.yaml --host 127.0.0.1 --port 4001" in fertig.stdout
    for name in _ALLE_DEV_NAMEN:
        assert f"{name}=SET" in fertig.stdout, name
    for name in _PRODUKTION:
        assert f"{name}=MISSING" in fertig.stdout, f"{name} erreicht den Dev-Proxy"
    assert "DEV_RESERVE_PROXY_START host=127.0.0.1 port=4001" in fertig.stderr


@pytest.mark.skipif(_BASH is None, reason="bash interpreter not available")
def test_ein_leerer_dev_wert_verhindert_den_start(tmp_path: Path) -> None:
    """Fail-closed: kein Platzhalter, kein Start mit sechs von sieben Werten."""
    release, env = _kulisse(tmp_path, ohne="KAI_DEV_LITELLM_CODE_API_KEY")

    fertig = _starte_proxy(release, env)

    assert fertig.returncode == 1
    assert "DEV_RESERVE_ENV_INCOMPLETE: KAI_DEV_LITELLM_CODE_API_KEY" in fertig.stderr
    assert "ARGS=" not in fertig.stdout, "der Transport wurde trotzdem gestartet"
