"""CLI: konsolidierter LiteLLM-Routen-Abnahmebericht, offline.

Beispiel (Pfade auf der Pi)::

    python -m scripts.litellm_route_report.cli \\
        --telemetry artifacts/llm_telemetry.jsonl \\
        --transport-log logs/litellm.err.log \\
        --eval-report artifacts/litellm_shadow_eval/report.json \\
        --json-out artifacts/litellm_route_report/report.json \\
        --md-out artifacts/litellm_route_report/report.md

Exit-Codes:

0   Bericht geschrieben. Das sagt nichts ueber den Zustand der Routen -- der
    steht im Bericht. **Exit 0 ist keine Freigabe.**
2   Aufruf, Eingabe oder Ausgabe ungueltig; es wurde nichts geschrieben.

Das Werkzeug fuehrt keinen Netzaufruf aus, spricht weder LiteLLM noch einen
Anbieter an und aendert keinen Modus.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.litellm_route_report.engine import DEFAULT_MAX_AGE_HOURS, InputError, build_report
from scripts.litellm_route_report.reporting import canonical_json, markdown_summary

EXIT_SUCCESS = 0
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Konsolidierter LiteLLM-Routen-Abnahmebericht (offline, trifft keine Freigabe)"
    )
    parser.add_argument(
        "--telemetry",
        action="append",
        default=[],
        type=Path,
        help="llm_telemetry.jsonl (mehrfach erlaubt)",
    )
    parser.add_argument(
        "--eval-report", type=Path, help="JSON-Report von scripts.litellm_shadow_eval (v1/v2)"
    )
    parser.add_argument(
        "--transport-log", type=Path, help="logs/litellm.err.log mit TRANSPORT_VERIFIED-Zeilen"
    )
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--md-out", required=True, type=Path)
    parser.add_argument(
        "--now", help="Berichtszeitpunkt, ISO-8601 mit Zeitzone (Standard: jetzt, UTC)"
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=f"ab diesem Alter des juengsten Belegs gilt Evidenz als veraltet "
        f"(Standard {DEFAULT_MAX_AGE_HOURS:g})",
    )
    return parser


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("--now braucht eine Zeitzone")
    return moment


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.telemetry:
        print("mindestens ein --telemetry ist noetig", file=sys.stderr)
        return EXIT_CONFIG
    if args.json_out == args.md_out:
        print("--json-out und --md-out muessen verschieden sein", file=sys.stderr)
        return EXIT_CONFIG
    try:
        now = _parse_now(args.now)
    except ValueError as exc:
        print(f"--now ungueltig: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        report = build_report(
            telemetry=args.telemetry,
            eval_report=args.eval_report,
            transport_log=args.transport_log,
            now=now,
            max_age_hours=args.max_age_hours,
        )
        json_text = canonical_json(report)
        md_text = markdown_summary(report)
        for target, text in ((args.json_out, json_text), (args.md_out, md_text)):
            target.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, nicht Text: LF auf jeder Plattform, derselbe Hash ueberall.
            # Daneben schreiben und tauschen: das Kontrollcenter liest das JSON,
            # waehrend der stuendliche Timer es neu schreibt.
            zwischen = target.with_name(target.name + ".tmp")
            zwischen.write_bytes(text.encode("utf-8"))
            os.replace(zwischen, target)
    except InputError as exc:
        print(f"Eingabefehler: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except OSError as exc:
        print(f"Ausgabefehler: {type(exc).__name__}", file=sys.stderr)
        return EXIT_CONFIG
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EXIT_CONFIG", "EXIT_SUCCESS", "build_parser", "main"]
