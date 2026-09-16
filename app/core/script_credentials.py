"""Eigener OpenAI-Key fuer Experiment- und Mess-Skripte.

Holdouts, A/B-Laeufe und Messungen laufen ausserhalb des Gateways und damit an
Telemetrie und Tagesbudget vorbei. Ueber den Produktiv-Key waren sie auf der
OpenAI-Rechnung nicht vom Betrieb zu trennen (09.09.: ~2,2 USD unsichtbar).

Regel: Skripte holen den Key NUR hier. Fehlt ``OPENAI_API_KEY_SCRIPTS`` oder ist
er identisch mit ``OPENAI_API_KEY``, bricht das Skript ab -- kein stiller
Rueckfall auf den Produktiv-Key.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

SCRIPT_KEY_ENV = "OPENAI_API_KEY_SCRIPTS"
PRODUCTION_KEY_ENV = "OPENAI_API_KEY"


class ScriptKeyError(RuntimeError):
    """Skript-Key fehlt oder ist der Produktiv-Key."""


def _clean(value: str | None) -> str:
    return (value or "").strip().lstrip("﻿")


def script_openai_api_key(
    *,
    env: Mapping[str, str | None] | None = None,
    env_file: Path | str = ".env",
) -> str:
    """Liefert den Skript-Key; Prozess-Umgebung schlaegt ``.env`` wie in den Settings."""
    if env is None:
        file_values = dotenv_values(env_file) if Path(env_file).is_file() else {}
        env = {**file_values, **os.environ}

    script_key = _clean(env.get(SCRIPT_KEY_ENV))
    if not script_key:
        raise ScriptKeyError(
            f"{SCRIPT_KEY_ENV} fehlt -- Experiment-Skripte laufen nicht ueber den Produktiv-Key."
        )
    if script_key == _clean(env.get(PRODUCTION_KEY_ENV)):
        raise ScriptKeyError(
            f"{SCRIPT_KEY_ENV} ist identisch mit dem Produktiv-Key {PRODUCTION_KEY_ENV}."
        )
    return script_key
