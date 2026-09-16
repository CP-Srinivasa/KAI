"""Experiment-Skripte muessen einen eigenen OpenAI-Key tragen.

Befund 2026-09-16 (OpenAI-Rechnung): der Holdout-Lauf vom 09.09. lief ueber den
Produktiv-Key, ~2,2 USD standen auf der Rechnung, aber in keiner KAI-Telemetrie.
Ein eigener Key trennt den Verbrauch auf der Anbieter-Rechnung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.script_credentials import ScriptKeyError, script_openai_api_key


def test_returns_the_script_key() -> None:
    env = {"OPENAI_API_KEY_SCRIPTS": "sk-scripts", "OPENAI_API_KEY": "sk-prod"}
    assert script_openai_api_key(env=env) == "sk-scripts"


def test_missing_key_fails_closed_without_falling_back_to_production() -> None:
    with pytest.raises(ScriptKeyError, match="OPENAI_API_KEY_SCRIPTS"):
        script_openai_api_key(env={"OPENAI_API_KEY": "sk-prod"})


def test_blank_key_counts_as_missing() -> None:
    with pytest.raises(ScriptKeyError):
        script_openai_api_key(env={"OPENAI_API_KEY_SCRIPTS": "  \n"})


def test_production_key_reused_as_script_key_is_rejected() -> None:
    env = {"OPENAI_API_KEY_SCRIPTS": "sk-prod\n", "OPENAI_API_KEY": "sk-prod"}
    with pytest.raises(ScriptKeyError, match="Produktiv"):
        script_openai_api_key(env=env)


def test_error_never_contains_the_key_value() -> None:
    env = {"OPENAI_API_KEY_SCRIPTS": "sk-secret-123", "OPENAI_API_KEY": "sk-secret-123"}
    with pytest.raises(ScriptKeyError) as exc:
        script_openai_api_key(env=env)
    assert "sk-secret-123" not in str(exc.value)


def test_reads_the_env_file_like_the_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY_SCRIPTS", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=sk-prod\nOPENAI_API_KEY_SCRIPTS=sk-scripts\n", encoding="utf-8"
    )
    assert script_openai_api_key(env_file=env_file) == "sk-scripts"
