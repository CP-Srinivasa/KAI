"""Was die Transport-Konfiguration verlangt, muss dokumentiert sein.

``config/litellm.yaml`` löst jeden Modelleintrag über ``os.environ/<NAME>`` auf.
Fehlt eine dieser Variablen, bricht der Proxy beim Start ab, **bevor** er einen
Port öffnet — und die Meldung sieht aus wie ein Fehler der Unit, nicht wie eine
unvollständige Konfiguration. Auf kai-pi5 war am 2026-09-08 keine einzige der
sechs gesetzt und keine einzige in ``.env.example`` erwähnt: wer der Vorlage
folgt, hätte den Dienst nie starten können und den Grund im Journal gesucht.

Der Test schließt die Klasse, nicht den Einzelfall. Er liest die
``os.environ/``-Verweise aus der YAML und verlangt für jeden einen Eintrag in
der Vorlage. Kommt später eine Route dazu, fällt die fehlende Zeile beim Merge
auf statt beim ersten Start auf dem Zielsystem.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "litellm.yaml"
VORLAGE = REPO / ".env.example"

#: ``model: os.environ/KAI_LITELLM_BULK_MODEL`` und ``master_key: os.environ/…``
_VERWEIS = re.compile(r"os\.environ/([A-Z][A-Z0-9_]*)")


def _verlangte_variablen() -> set[str]:
    return set(_VERWEIS.findall(CONFIG.read_text(encoding="utf-8")))


def test_der_pruefer_findet_ueberhaupt_verweise() -> None:
    """Ein Wächter, der nichts sieht, ist von einem grünen nicht zu unterscheiden."""
    verlangt = _verlangte_variablen()

    assert len(verlangt) >= 7, f"nur {len(verlangt)} os.environ-Verweise: {sorted(verlangt)}"
    assert "LITELLM_MASTER_KEY" in verlangt
    assert "KAI_LITELLM_DEEPSEEK_MODEL" in verlangt


def test_jede_verlangte_variable_steht_in_der_vorlage() -> None:
    """Sonst ist die Vorlage vollständig und die Konfiguration trotzdem nicht."""
    vorlage = VORLAGE.read_text(encoding="utf-8")
    namen = {
        zeile.split("=", 1)[0].strip()
        for zeile in vorlage.splitlines()
        if "=" in zeile and not zeile.lstrip().startswith("#")
    }

    fehlend = sorted(name for name in _verlangte_variablen() if name not in namen)

    assert not fehlend, (
        f"in config/litellm.yaml verlangt, in .env.example nicht erklaert: {fehlend}"
    )


def test_die_vorlage_traegt_keinen_master_key() -> None:
    """Ein Beispielwert wäre ein Geheimnis, das aussieht wie Dokumentation.

    Er stünde im Repo, käme per `cp .env.example .env` in Betrieb, und niemand
    änderte ihn — ein Proxy-Schlüssel, den jeder Leser des Repos kennt.
    """
    for zeile in VORLAGE.read_text(encoding="utf-8").splitlines():
        if zeile.startswith("LITELLM_MASTER_KEY="):
            assert zeile.strip() == "LITELLM_MASTER_KEY=", zeile


def test_die_control_plane_bleibt_in_der_vorlage_fail_closed() -> None:
    """`.env.example` wird kopiert. Ein `true` hier schaltete den Transport bei
    jedem ein, der die Vorlage übernimmt — ohne Entscheidung."""
    vorlage = VORLAGE.read_text(encoding="utf-8")

    assert "KAI_INFERENCE_ENABLED=false" in vorlage
    assert "KAI_INFERENCE_MODE_CEILING=off" in vorlage
    assert "KAI_INFERENCE_MODE_CEILING=primary" not in vorlage


def test_deepseek_bleibt_eine_zusaetzliche_transport_probe() -> None:
    """Der Alias darf Gemini nicht ersetzen und keine app/ai-Route erfinden."""
    config = CONFIG.read_text(encoding="utf-8")
    control_plane = (REPO / "app" / "ai" / "config.py").read_text(encoding="utf-8")

    assert "model_name: kai-deepseek" in config
    assert "model: os.environ/KAI_LITELLM_DEEPSEEK_MODEL" in config
    assert "model_name: kai-standard" in config
    assert "kai-deepseek" not in control_plane
    assert "KAI_LITELLM_DEEPSEEK_MODEL" not in control_plane


def test_deepseek_vertrag_enthaelt_keinen_schluessel() -> None:
    """Nur Name und Modelle sind dokumentiert; der Schlüssel bleibt leer."""
    vorlage = VORLAGE.read_text(encoding="utf-8")

    assert "DEEPSEEK_API_KEY=" in vorlage
    assert "DEEPSEEK_MODEL=deepseek-v4-flash" in vorlage
    assert "#   KAI_LITELLM_DEEPSEEK_MODEL=deepseek/deepseek-v4-flash" in vorlage
    assert "KAI_LITELLM_DEEPSEEK_MODEL=" in vorlage
    for zeile in vorlage.splitlines():
        if zeile.startswith("DEEPSEEK_API_KEY="):
            assert zeile == "DEEPSEEK_API_KEY="
