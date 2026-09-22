#!/usr/bin/env python3
"""Drift zwischen den Settings-Klassen und ``.env.example`` (System-Audit 16.09., P1-20).

Die Pi-``.env`` fuehrte 204 Schluessel, die Vorlage deckte 121 — darunter
fehlten sicherheitsrelevante Namen, und ``TRADINGVIEW_WEBHOOK_SHARED_TOKEN``
(Pflicht in den Token-Modi) stand nirgends. Ein Operator, der die Vorlage
liest, sieht also nicht, was das System liest.

Erwartete Env-Namen entstehen deterministisch aus den ``BaseSettings``-Klassen
(``env_prefix`` + Feldname in Grossbuchstaben; bei ``validation_alias`` die
``AliasChoices``, die wie Env-Namen aussehen). Ist-Namen kommen aus
``.env.example`` — aktive UND auskommentierte ``NAME=``-Zeilen zaehlen als
dokumentiert. Die Differenz ist ein Ratchet: bekannte Luecken stehen in
``tests/unit/env_example_drift_baseline.json`` und duerfen nur schrumpfen
(``--update`` schreibt die Baseline neu, nie groesser als der Ist-Stand).

Bewusst NICHT erfasst: direkte ``os.getenv``-Lesungen ausserhalb der
Settings-Klassen (Audit-Anhang, ~23 Stellen) — eigener Punkt.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
import sys
from pathlib import Path

from pydantic import AliasChoices
from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parent.parent
# Der Ratchet muss die Settings DIESES Baums lesen — nicht ein anderes
# installiertes oder per PYTHONPATH erreichbares ``app``.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
EXAMPLE_PATH = REPO_ROOT / ".env.example"
BASELINE_PATH = REPO_ROOT / "tests" / "unit" / "env_example_drift_baseline.json"

#: Module, die BaseSettings-Klassen definieren. Ein neues Settings-Modul gehoert
#: hierher — sonst sieht der Ratchet seine Schluessel nicht.
SETTINGS_MODULES: tuple[str, ...] = (
    "app.core.settings",
    "app.core.ai_cost_settings",
    "app.core.chain_settings",
    "app.core.evidence_settings",
    "app.core.integrity_settings",
    "app.core.lightning_settings",
    "app.core.payment_settings",
    "app.core.pay_settings",
    "app.core.prereg_settings",
    "app.core.re_entry_mode",
    "app.ai.config",
    "app.exploration.settings",
    "app.intelligence.settings",
    "app.governance.third_party_gate",
    "app.market_data.coingecko_overview",
)

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EXAMPLE_LINE = re.compile(r"^\s*(#\s*)?([A-Z][A-Z0-9_]*)=(.*)$")


def settings_classes() -> list[type[BaseSettings]]:
    classes: list[type[BaseSettings]] = []
    for module_name in SETTINGS_MODULES:
        module = importlib.import_module(module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(cls, BaseSettings)
                and cls is not BaseSettings
                and cls.__module__ == module_name
            ):
                classes.append(cls)
    return classes


def _is_nested_settings(annotation: object) -> bool:
    return inspect.isclass(annotation) and issubclass(annotation, BaseSettings)


def expected_env_names() -> dict[str, str]:
    """Env-Name -> ``Klasse.feld`` (erster Treffer gewinnt bei Alias-Dubletten)."""
    names: dict[str, str] = {}
    for cls in settings_classes():
        prefix = str(cls.model_config.get("env_prefix") or "")
        for field_name, info in cls.model_fields.items():
            if _is_nested_settings(info.annotation):
                continue
            alias = info.validation_alias
            if isinstance(alias, AliasChoices):
                candidates = [c for c in alias.choices if isinstance(c, str) and _ENV_NAME.match(c)]
            elif isinstance(alias, str) and _ENV_NAME.match(alias):
                candidates = [alias]
            else:
                candidates = [(prefix + field_name).upper()]
            for name in candidates:
                names.setdefault(name, f"{cls.__name__}.{field_name}")
    return names


def secret_env_names() -> set[str]:
    """Env-Namen von Feldern mit ``repr=False`` — in der Vorlage nur leer erlaubt."""
    secrets: set[str] = set()
    for cls in settings_classes():
        prefix = str(cls.model_config.get("env_prefix") or "")
        for field_name, info in cls.model_fields.items():
            if info.repr is False and not _is_nested_settings(info.annotation):
                alias = info.validation_alias
                if isinstance(alias, AliasChoices):
                    secrets.update(c for c in alias.choices if isinstance(c, str))
                elif isinstance(alias, str):
                    secrets.add(alias)
                else:
                    secrets.add((prefix + field_name).upper())
    return secrets


def example_entries(path: Path = EXAMPLE_PATH) -> dict[str, tuple[bool, str]]:
    """Env-Name -> (aktiv?, Wert) aus ``.env.example``."""
    entries: dict[str, tuple[bool, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _EXAMPLE_LINE.match(line)
        if match:
            commented, name, value = match.groups()
            entries.setdefault(name, (commented is None, value.strip()))
    return entries


def missing_names(path: Path = EXAMPLE_PATH) -> dict[str, str]:
    expected = expected_env_names()
    documented = example_entries(path)
    return {name: origin for name, origin in expected.items() if name not in documented}


def load_baseline(path: Path = BASELINE_PATH) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))["entries"])


def write_baseline(entries: set[str], path: Path = BASELINE_PATH) -> None:
    payload = {
        "_comment": (
            "Ratchet (scripts/env_example_drift.py): bekannte Luecken zwischen den "
            "Settings-Klassen und .env.example. Darf nur schrumpfen; neue Luecken "
            "sind rot. Aktualisieren: python scripts/env_example_drift.py --update"
        ),
        "entries": sorted(entries),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Settings-Klassen gegen .env.example (Ratchet)")
    parser.add_argument("--update", action="store_true", help="Baseline auf den Ist-Stand setzen")
    args = parser.parse_args(argv)

    expected = expected_env_names()
    missing = missing_names()
    print(
        f"[env-drift] {len(expected)} erwartete Schluessel, {len(missing)} fehlen in .env.example"
    )
    by_origin: dict[str, list[str]] = {}
    for name, origin in sorted(missing.items()):
        by_origin.setdefault(origin.split(".")[0], []).append(name)
    for cls_name, names in sorted(by_origin.items()):
        print(f"  {cls_name}: {len(names)} -> {', '.join(names)}")

    if args.update:
        write_baseline(set(missing))
        print(f"[env-drift] baseline written -> {BASELINE_PATH}")
        return 0
    if not BASELINE_PATH.exists():
        print("[env-drift] keine Baseline — mit --update anlegen")
        return 1
    new = sorted(set(missing) - load_baseline())
    if new:
        print(f"[env-drift] FAIL neue Luecken: {', '.join(new)}")
        return 1
    print("[env-drift] ok — keine neue Luecke")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
