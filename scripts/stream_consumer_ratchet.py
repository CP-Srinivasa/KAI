#!/usr/bin/env python3
"""Stream-consumer ratchet (Sprint G4 aus KMA-20260827) — kein neuer Strom ohne Abnehmer.

**Der Defekt, den dieses Gate schliesst.** KAI baut Produzenten und nennt sie
Systeme (Master-Audit R2-19): fast jeder JSONL-Strom hat einen Schreiber, viele
haben keinen Leser, und *niemand zahlt einen Preis, wenn ein Strom stirbt*
(R2-21). Deshalb sind fuenf Komponenten ausgefallen, ohne dass es jemand
bemerkt hat (R2-10) — ``telegram_webhook_rejections.jsonl`` hat 85
Schreibstellen und 0 Leser (A1-022), der Reconciler meldete 1.902-mal gruen,
ohne je etwas verglichen zu haben (A12-081). Vierzehn Einzelfixes schliessen
14 Symptome; dieses Ratchet schliesst die Klasse fuer den **Zuwachs**.

**Die Regel.** Ein Stromname, der nicht in der Baseline steht, ist nur
mergefaehig, wenn ``config/stream_contracts.json`` fuer ihn erklaert:

1. ``reader`` — ein existierendes Modul, das den Stromnamen **referenziert**
   (Papier-Deklarationen fallen durch), und der Strom muss von **mindestens
   zwei** Modulen referenziert werden. Ein Strom, den nur sein eigener
   Schreiber kennt, ist per Konstruktion konsumentenlos.
2. ``failure_consequence`` + die drei Reifegrad-Felder
   ``failure_would_be_noticed_by`` / ``time_to_notice`` /
   ``decision_that_would_change``. Ein Block mit "niemand / nie / keine" ist
   nicht HARDEN-wuerdig, sondern ``NEEDS_CONSUMER_FIRST``.
3. ``freshness_check`` — der Strom muss einen Eintrag in
   ``_FRESHNESS_PER_FILE_MIN`` (``app/alerts/health_check.py``) haben, sonst
   kann sein Tod nicht auffallen.

**Was dieses Gate NICHT ist.** Es ist kein Datenfluss-Beweis: es misst
Referenzen, nicht Lesevorgaenge. Es erzwingt den **Bestand nicht rueckwirkend**
(Baseline = Ist-Stand; die Sanierung der 141 Laufzeit-Stroeme waere ein
40-Sprint-Programm, R2-24). Und es loescht nichts.

**Population.** Code-Wahrheit, nicht Laufzeit-Wahrheit: String-Literale
``*.jsonl`` in ``app/`` (Basename). Das Master-Audit zaehlte 141 Dateien *auf
der Platte* und 101 *Pfade im Code* — beides andere Populationen als diese.
Dynamisch zusammengesetzte Namen (f-Strings) werden als **Familie**
``<modul>::*<suffix>`` gefuehrt, damit ein neuer dynamischer Schreiber nicht
unsichtbar bleibt.

Aufrufe::

    python scripts/stream_consumer_ratchet.py            # CI-Gate
    python scripts/stream_consumer_ratchet.py --update   # Baseline neu schreiben
    python scripts/stream_consumer_ratchet.py --json     # maschinenlesbar
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "stream_baseline.json"
CONTRACTS_PATH = REPO_ROOT / "config" / "stream_contracts.json"
HEALTH_CHECK_PATH = REPO_ROOT / "app" / "alerts" / "health_check.py"
SCAN_ROOT = REPO_ROOT / "app"

#: Ein Stromname ist ein vollstaendiger Basename, kein Suffix-Fragment.
STREAM_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]*\.jsonl$")

#: Pflichtfelder eines Vertrags. Die letzten drei sind die Reifegrad-Achse
#: (G4 Task 4): wer merkt den Ausfall, nach welcher Zeit, und welche
#: Entscheidung waere dann anders.
REQUIRED_FIELDS = (
    "reader",
    "failure_consequence",
    "freshness_check",
    "failure_would_be_noticed_by",
    "time_to_notice",
    "decision_that_would_change",
)

#: Felder, die eine echte Aussage tragen muessen. ``reader`` und
#: ``freshness_check`` werden strukturell geprueft, nicht sprachlich.
PROSE_FIELDS = (
    "failure_consequence",
    "failure_would_be_noticed_by",
    "time_to_notice",
    "decision_that_would_change",
)

#: "niemand / nie / keine" — die drei Antworten, die einen Block als
#: NEEDS_CONSUMER_FIRST ausweisen, in beiden Projektsprachen.
EMPTY_ANSWERS = frozenset(
    {
        "niemand",
        "nie",
        "keine",
        "keiner",
        "keines",
        "nichts",
        "none",
        "nobody",
        "never",
        "nothing",
        "n/a",
        "na",
        "tbd",
        "unknown",
        "unbekannt",
        "-",
        "?",
    }
)

MIN_REFERENCING_MODULES = 2

#: Die Freshness-Registry nennt jeden ueberwachten Strom beim Namen. Sie zaehlt
#: deshalb NICHT als Konsument — sonst waere die Regel "mindestens zwei Module"
#: durch genau die Zeile erfuellt, die dieses Gate ohnehin verlangt.
FRESHNESS_REGISTRY_MODULE = "app/alerts/health_check.py"


@dataclass(frozen=True)
class Stream:
    """Ein im Code deklarierter Strom und die Module, die ihn nennen."""

    name: str
    modules: tuple[str, ...]
    dynamic: bool = False


@dataclass
class Verdict:
    """Ergebnis eines Ratchet-Laufs."""

    new_streams: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    disappeared: list[str] = field(default_factory=list)
    inert: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def _module_key(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def discover_streams(scan_root: Path, repo_root: Path | None = None) -> dict[str, Stream]:
    """Inventarisiere alle im Code deklarierten JSONL-Stroeme unter ``scan_root``.

    Statische Literale werden auf ihren Basename normalisiert; dynamisch
    zusammengesetzte Namen (f-String, dessen letztes Stueck auf ``.jsonl``
    endet) werden als Familie ``<modul>::*<suffix>`` gefuehrt.
    """
    base = repo_root if repo_root is not None else scan_root.parent
    static: dict[str, set[str]] = {}
    dynamic: dict[str, set[str]] = {}

    for path in sorted(scan_root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defekte Datei
            continue
        module = _module_key(path, base)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if not node.value.endswith(".jsonl"):
                    continue
                name = node.value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if STREAM_NAME_RE.match(name):
                    static.setdefault(name, set()).add(module)
            elif isinstance(node, ast.JoinedStr):
                consts = [
                    v.value
                    for v in node.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
                if consts and consts[-1].endswith(".jsonl"):
                    family = f"{module}::*{consts[-1]}"
                    dynamic.setdefault(family, set()).add(module)

    streams = {
        name: Stream(name=name, modules=tuple(sorted(mods))) for name, mods in static.items()
    }
    for family, mods in dynamic.items():
        streams[family] = Stream(name=family, modules=tuple(sorted(mods)), dynamic=True)
    return streams


def freshness_registry(health_check_path: Path) -> set[str]:
    """Lies die Schluessel von ``_FRESHNESS_PER_FILE_MIN`` per AST.

    Bewusst ohne Import: das Gate laeuft in CI vor der App-Installation und
    darf keine App-Seiteneffekte ausloesen.
    """
    if not health_check_path.exists():
        return set()
    tree = ast.parse(health_check_path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        target_names: list[str] = []
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = [node.target.id]
            value = node.value
        elif isinstance(node, ast.Assign):
            target_names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        if "_FRESHNESS_PER_FILE_MIN" not in target_names or not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.add(key.value)
    return keys


def _prose_is_empty(text: Any) -> bool:
    if not isinstance(text, str):
        return True
    stripped = text.strip().rstrip(".").strip().lower()
    return not stripped or stripped in EMPTY_ANSWERS


def _reader_references(reader: str, stream: Stream, repo_root: Path) -> tuple[bool, str]:
    """Prueft, ob das deklarierte Leser-Modul den Strom ueberhaupt nennt."""
    reader_path = repo_root / reader
    if not reader_path.is_file():
        return False, f"reader '{reader}' existiert nicht"
    try:
        source = reader_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:  # pragma: no cover - defekte Datei
        return False, f"reader '{reader}' ist nicht lesbar"
    needle = stream.name.split("::", 1)[-1].lstrip("*")
    if needle not in source:
        return False, (
            f"reader '{reader}' nennt '{stream.name}' nicht — "
            "eine Deklaration auf Papier ist kein Konsument"
        )
    return True, ""


def evaluate(
    streams: dict[str, Stream],
    baseline: set[str],
    contracts: dict[str, Any],
    freshness_keys: set[str],
    repo_root: Path,
) -> Verdict:
    """Vergleiche Ist-Bestand gegen Baseline und pruefe die Vertraege des Zuwachses."""
    verdict = Verdict()
    inert = set(contracts.get("intentionally_inert", []))
    entries = contracts.get("streams", {})

    verdict.disappeared = sorted(baseline - set(streams))

    for name in sorted(set(streams) - baseline):
        stream = streams[name]
        verdict.new_streams.append(name)
        if name in inert:
            verdict.inert.append(name)
            continue

        entry = entries.get(name)
        if not isinstance(entry, dict):
            verdict.violations.append(
                f"{name}: NEEDS_CONSUMER_FIRST — kein Eintrag in config/stream_contracts.json. "
                "Ein neuer Strom braucht einen Leser, eine Ausfallkonsequenz und einen "
                "Freshness-Eintrag (docs/runbooks/stream_consumer_contract.md)."
            )
            continue

        problems: list[str] = []
        missing = [f for f in REQUIRED_FIELDS if f not in entry]
        if missing:
            problems.append(f"Pflichtfelder fehlen: {', '.join(sorted(missing))}")

        for field_name in PROSE_FIELDS:
            if field_name in entry and _prose_is_empty(entry[field_name]):
                problems.append(
                    f"{field_name} ist leer oder sagt 'niemand/nie/keine' ⇒ NEEDS_CONSUMER_FIRST"
                )

        consumer_modules = tuple(m for m in stream.modules if m != FRESHNESS_REGISTRY_MODULE)

        reader = entry.get("reader")
        if isinstance(reader, str) and reader:
            if reader.replace("\\", "/") == FRESHNESS_REGISTRY_MODULE:
                problems.append(
                    "reader ist die Freshness-Registry selbst — eine Ueberwachungszeile "
                    "ist kein Konsument; der Leser gehoert in ein eigenes Modul"
                )
            else:
                ok, why = _reader_references(reader, stream, repo_root)
                if not ok:
                    problems.append(why)
        elif "reader" in entry:
            problems.append("reader ist leer")

        if not stream.dynamic and len(consumer_modules) < MIN_REFERENCING_MODULES:
            problems.append(
                f"nur {len(consumer_modules)} Modul nennt den Strom "
                f"({', '.join(consumer_modules) or 'keins'}) — Schreiber und Leser koennen "
                "nicht dasselbe einzige Modul sein (die Freshness-Registry zaehlt nicht mit)"
            )

        if not stream.dynamic and stream.name not in freshness_keys:
            problems.append(
                "kein Eintrag in _FRESHNESS_PER_FILE_MIN (app/alerts/health_check.py) — "
                "ohne Freshness-Zeile kann der Tod des Stroms nicht auffallen"
            )

        if problems:
            verdict.violations.extend(f"{name}: {p}" for p in problems)
        else:
            verdict.accepted.append(name)

    return verdict


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("streams", []))


def load_contracts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"streams": {}, "intentionally_inert": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("streams", {})
    data.setdefault("intentionally_inert", [])
    return data


def write_baseline(path: Path, streams: dict[str, Stream]) -> None:
    payload = {
        "_comment": (
            "G4-Ratchet (KMA-20260827): Ist-Bestand der im Code deklarierten JSONL-Stroeme. "
            "Der Bestand wird NICHT rueckwirkend saniert — nur der Zuwachs braucht einen "
            "Vertrag in config/stream_contracts.json. Neu erzeugen: "
            "python scripts/stream_consumer_ratchet.py --update"
        ),
        "count": len(streams),
        "streams": sorted(streams),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stream-consumer ratchet (G4)")
    parser.add_argument("--update", action="store_true", help="Baseline auf den Ist-Stand setzen")
    parser.add_argument("--json", action="store_true", help="maschinenlesbares Urteil")
    args = parser.parse_args(argv)

    streams = discover_streams(SCAN_ROOT, REPO_ROOT)

    if args.update:
        write_baseline(BASELINE_PATH, streams)
        print(f"[stream-ratchet] baseline geschrieben: {len(streams)} Stroeme -> {BASELINE_PATH}")
        return 0

    verdict = evaluate(
        streams,
        load_baseline(BASELINE_PATH),
        load_contracts(CONTRACTS_PATH),
        freshness_registry(HEALTH_CHECK_PATH),
        REPO_ROOT,
    )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": verdict.ok,
                    "inventory": len(streams),
                    "new_streams": verdict.new_streams,
                    "accepted": verdict.accepted,
                    "inert": verdict.inert,
                    "disappeared": verdict.disappeared,
                    "violations": verdict.violations,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0 if verdict.ok else 1

    print(f"[stream-ratchet] Inventar: {len(streams)} Stroeme im Code deklariert")
    for name in verdict.disappeared:
        print(f"[stream-ratchet] weg: {name} (Baseline mit --update nachziehen)")
    for name in verdict.inert:
        print(f"[stream-ratchet] INTENTIONALLY_INERT: {name} (zaehlt in keiner Reifegradaussage)")
    for name in verdict.accepted:
        print(f"[stream-ratchet] ok: {name} — Vertrag vollstaendig")
    for problem in verdict.violations:
        print(f"[stream-ratchet] FAIL {problem}")

    if not verdict.ok:
        print(
            f"[stream-ratchet] {len(verdict.violations)} Verstoss(e). "
            "Ein neuer Strom ohne Konsument ist ein Produzent, kein System."
        )
        return 1
    print("[stream-ratchet] ok — kein unvertraglicher Zuwachs")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
