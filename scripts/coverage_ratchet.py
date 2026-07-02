#!/usr/bin/env python3
"""Coverage-Ratchet (B4) — die Test-Line-Coverage darf nur STEIGEN.

Spiegelbild des God-File-Ratchets (``scripts/godfile_ratchet.py``): dort duerfen
benannte Dateien nur SCHRUMPFEN, hier darf die Gesamt-Line-Coverage nur WACHSEN.
Das Gate liest den von ``pytest --cov=app --cov-report=xml`` erzeugten
Cobertura-Report (``coverage.xml``), vergleicht die ``line-rate`` gegen die
eingecheckte Baseline (``scripts/coverage_baseline.json``) und schlaegt fehl,
sobald die gemessene Abdeckung UNTER die Baseline faellt. Damit ist "neuer Code
kommt untestet rein" kein Appell mehr, sondern ein Merge-Gate.

Warum eine Marge? Coverage rauscht (nicht-deterministische Test-Pfade, geaenderte
Denominator-Zeilen). Die Baseline liegt daher bewusst ``MARGIN_PP``
Prozentpunkte UNTER dem zuletzt gemessenen Wert — sie faengt Rauschen ab, ohne
eine echte Regression durchzulassen.

``--update`` zieht die Baseline auf ``gemessen - MARGIN_PP`` NACH OBEN (nie nach
unten): nach echten Coverage-Gewinnen einchecken, damit der Fortschritt
verriegelt ist. Eine bewusste Absenkung ist nur durch manuelles Editieren der
JSON moeglich — im Diff sichtbar, vom Review zu rechtfertigen.

Exit-Codes: 0 = ok/angehoben | 1 = Regression (unter Baseline) | 2 = Report
fehlt/kaputt.

Hinweis: Die gedruckte Ausgabe bleibt bewusst reines ASCII, damit der Operator
das Skript auch auf einer Windows-cp1252-Konsole ohne UnicodeEncodeError fahren
kann (CI ist UTF-8; die lokale Konsole nicht immer).
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BASELINE_PATH = SCRIPT_DIR / "coverage_baseline.json"
DEFAULT_XML = REPO_ROOT / "coverage.xml"

# Sicherheitsmarge (Prozentpunkte) gegen Mess-Rauschen. Baseline = gemessen - MARGIN.
MARGIN_PP = 0.5
# Float-Toleranz fuer den Vergleich (Baseline 1 NKS, gemessen 2 NKS).
EPS = 1e-9

EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_BAD_REPORT = 2


def measure_line_rate_pct(xml_path: Path) -> float:
    """Liest die Gesamt-``line-rate`` aus einem Cobertura-Report als Prozent (2 NKS).

    Wirft ``FileNotFoundError`` wenn die Datei fehlt und ``ValueError`` wenn sie
    kein gueltiger Cobertura-Report mit ``line-rate`` ist (inkl. XML-Parse-Fehler).
    """
    if not xml_path.exists():
        raise FileNotFoundError(str(xml_path))
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"XML nicht parsebar: {exc}") from exc
    if root.tag != "coverage":
        raise ValueError(f"Wurzel-Element ist <{root.tag}>, erwartet <coverage> (Cobertura)")
    line_rate = root.get("line-rate")
    if line_rate is None:
        raise ValueError("Attribut 'line-rate' fehlt am <coverage>-Element")
    try:
        return round(float(line_rate) * 100.0, 2)
    except ValueError as exc:
        raise ValueError(f"'line-rate' ist keine Zahl: {line_rate!r}") from exc


def _read_baseline(path: Path) -> tuple[dict, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, float(data["min_line_rate_pct"])


def _write_baseline(path: Path, data: dict, new_pct: float) -> None:
    data["min_line_rate_pct"] = new_pct
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coverage ratchet (line coverage, up-only)")
    parser.add_argument(
        "--xml",
        default=str(DEFAULT_XML),
        help="Pfad zum Cobertura coverage.xml (default: <repo>/coverage.xml)",
    )
    parser.add_argument(
        "--baseline",
        default=str(BASELINE_PATH),
        help="Pfad zur Baseline-JSON (default: scripts/coverage_baseline.json)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Baseline auf (gemessen - Marge) ANHEBEN, wenn das die aktuelle uebersteigt (nie senken)",
    )
    args = parser.parse_args(argv)

    xml_path = Path(args.xml)
    baseline_path = Path(args.baseline)

    try:
        measured = measure_line_rate_pct(xml_path)
    except FileNotFoundError:
        print(
            f"[cov-ratchet] FEHLER: {xml_path} nicht gefunden. Erzeuge ihn via:\n"
            "    pytest tests/ --cov=app --cov-report=xml"
        )
        return EXIT_BAD_REPORT
    except ValueError as exc:
        print(f"[cov-ratchet] FEHLER: {xml_path} ist kein gueltiger Cobertura-Report: {exc}")
        return EXIT_BAD_REPORT

    try:
        data, baseline = _read_baseline(baseline_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[cov-ratchet] FEHLER: Baseline {baseline_path} unlesbar/ungueltig: {exc}")
        return EXIT_BAD_REPORT

    # Regression schlaegt IMMER fehl — auch im --update-Lauf (der hebt nur an).
    if measured + EPS < baseline:
        print(
            f"[cov-ratchet] FAIL: Line-Coverage {measured:.2f}% < Baseline {baseline:.1f}% "
            f"(-{baseline - measured:.2f}pp). Coverage darf nur STEIGEN.\n"
            "    -> Neuen/geaenderten Code mit Tests abdecken. Baseline NICHT senken.\n"
            "    -> Bei legitimem Rueckgang (z.B. grosser, bewusst untestbarer Codeblock): "
            "Baseline in scripts/coverage_baseline.json im Diff sichtbar anpassen und im "
            "PR-Body rechtfertigen."
        )
        return EXIT_REGRESSION

    if args.update:
        new_baseline = round(measured - MARGIN_PP, 1)
        if new_baseline > baseline:
            _write_baseline(baseline_path, data, new_baseline)
            print(
                f"[cov-ratchet] Baseline angehoben: {baseline:.1f}% -> {new_baseline:.1f}% "
                f"(gemessen {measured:.2f}% - {MARGIN_PP}pp Marge). {baseline_path} geschrieben."
            )
        else:
            print(
                f"[cov-ratchet] Baseline unveraendert {baseline:.1f}%; gemessen {measured:.2f}% "
                f"ergaebe {new_baseline:.1f}% <= aktuell (Ratchet senkt nie)."
            )
        return EXIT_OK

    headroom = measured - baseline
    print(
        f"[cov-ratchet] ok: Line-Coverage {measured:.2f}% >= Baseline {baseline:.1f}% "
        f"(Puffer {headroom:.2f}pp). Bei echtem Gewinn: python scripts/coverage_ratchet.py --update."
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
