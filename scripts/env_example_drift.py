#!/usr/bin/env python3
"""Drift zwischen dem, was das System liest, und ``.env.example`` (Audit 16.09., P1-20).

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

Zweite Quelle seit 23.09.: **direkte** ``os.getenv``/``os.environ``-Lesungen in
``app/``. Sie stehen in keiner Settings-Klasse, waren deshalb fuer die
Ableitung unsichtbar — und KEINE der 16 stand in der Vorlage, obwohl darunter
der Phantom-Filter des Paper-Engines, die Preis-Sanity-Schwellen und die
Anbieter-Abweichung sind. Erfasst werden nur String-Literale; ein Name aus
einer Variablen bleibt unsichtbar (bewusste Grenze, im Scan dokumentiert).
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
#: ``os.getenv("X")`` / ``os.environ.get("X")`` / ``os.environ["X"]`` — nur
#: Literale. Ein Name, der aus einer Variablen kommt, ist so nicht auffindbar;
#: das ist die bewusste Grenze dieses Scans.
_DIRECT_READ = re.compile(
    r"""os\.(?:getenv|environ\.get)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\]"""
)


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


def expected_fields() -> dict[str, list[str]]:
    """``Klasse.feld`` -> Env-Namen; der erste ist der kanonische (Prefix + Feld
    bzw. erste ``AliasChoices``-Wahl). Ein Feld gilt als dokumentiert, sobald
    EINER seiner Namen in der Vorlage steht."""
    fields: dict[str, list[str]] = {}
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
            if candidates:
                fields[f"{cls.__name__}.{field_name}"] = candidates
    return fields


def expected_env_names() -> dict[str, str]:
    """Env-Name -> ``Klasse.feld`` (erster Treffer gewinnt bei Alias-Dubletten)."""
    names: dict[str, str] = {}
    for origin, candidates in expected_fields().items():
        for name in candidates:
            names.setdefault(name, origin)
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


def direct_env_reads(app_root: Path | None = None) -> dict[str, str]:
    """Env-Name -> erste Fundstelle ``datei:zeile`` fuer Lesungen an den
    Settings-Klassen vorbei.

    Diese Namen sind die eigentliche Falle: sie stehen in keiner Settings-Klasse,
    also sieht sie die Ableitung oben nicht — und bis 23.09. stand keiner von
    ihnen in der Vorlage, obwohl darunter Handels-Schwellen sind (Phantom-Filter,
    Preis-Sanity, Provider-Abweichung).
    """
    root = app_root or (REPO_ROOT / "app")
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:  # Root ausserhalb des Repos (Tests)
            rel = path.relative_to(root).as_posix()
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            for match in _DIRECT_READ.finditer(line):
                name = match.group(1) or match.group(2)
                found.setdefault(name, f"{rel}:{lineno}")
    return found


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
    """Kanonischer Env-Name -> Herkunft (``Klasse.feld`` oder ``datei:zeile``)
    fuer alles, was das System liest und die Vorlage nicht nennt."""
    documented = example_entries(path)
    missing = {
        candidates[0]: origin
        for origin, candidates in expected_fields().items()
        if not any(name in documented for name in candidates)
    }
    for name, origin in direct_env_reads().items():
        if name not in documented:
            missing.setdefault(name, origin)
    return missing


def load_baseline(path: Path = BASELINE_PATH) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))["entries"])


def write_baseline(entries: set[str], path: Path = BASELINE_PATH) -> None:
    payload = {
        "_comment": (
            "Ratchet (scripts/env_example_drift.py): bekannte Luecken zwischen dem, was "
            "das System liest (Settings-Klassen UND direkte os.getenv-Lesungen), und "
            ".env.example. Darf nur schrumpfen; neue Luecken sind rot. "
            "Aktualisieren: python scripts/env_example_drift.py --update"
        ),
        "entries": sorted(entries),
    }
    # newline="\n": auf Windows sonst CRLF, und Git meldet bei jedem Lauf Normalisierung.
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Settings-Klassen gegen .env.example (Ratchet)")
    parser.add_argument("--update", action="store_true", help="Baseline auf den Ist-Stand setzen")
    args = parser.parse_args(argv)

    expected = expected_env_names()
    direct = direct_env_reads()
    missing = missing_names()
    print(
        f"[env-drift] {len(expected)} Schluessel aus Settings-Klassen + {len(direct)} direkt "
        f"gelesene, {len(missing)} fehlen in .env.example"
    )
    by_origin: dict[str, list[str]] = {}
    for name, origin in sorted(missing.items()):
        # "Klasse.feld" -> Klasse; "app/pfad.py:12" -> Pfad ohne Zeile
        group = origin.rsplit(":", 1)[0] if "/" in origin else origin.split(".")[0]
        by_origin.setdefault(group, []).append(name)
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
