"""CLI for the offline LiteLLM shadow evidence harness.

Exit codes:
0 evaluation completed and every evaluated route is READY/SHADOW_VALIDATED
2 invalid CLI, policy, runtime flags, or output operation
3 invalid evidence
4 insufficient evidence (also empty input)
5 valid but NOT_READY evidence
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.litellm_shadow_eval.engine import evaluate
from scripts.litellm_shadow_eval.models import GraduationStatus
from scripts.litellm_shadow_eval.policy import PolicyError, load_policy, load_runtime_flags
from scripts.litellm_shadow_eval.reporting import canonical_json, markdown_summary

EXIT_SUCCESS = 0
EXIT_CONFIG = 2
EXIT_INVALID_EVIDENCE = 3
EXIT_INSUFFICIENT_EVIDENCE = 4
EXIT_NOT_READY = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate LiteLLM shadow evidence offline")
    parser.add_argument("--telemetry", action="append", default=[], type=Path)
    parser.add_argument("--replay", action="append", default=[], type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--runtime-evidence", required=True, type=Path)
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--md-out", required=True, type=Path)
    return parser


def _exit_code(statuses: set[GraduationStatus], *, has_routes: bool) -> int:
    if GraduationStatus.INVALID_EVIDENCE in statuses:
        return EXIT_INVALID_EVIDENCE
    if not has_routes or GraduationStatus.INSUFFICIENT_EVIDENCE in statuses:
        return EXIT_INSUFFICIENT_EVIDENCE
    if GraduationStatus.NOT_READY in statuses:
        return EXIT_NOT_READY
    return EXIT_SUCCESS


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inputs = [*args.telemetry, *args.replay]
    if not inputs:
        print("at least one --telemetry or --replay input is required", file=sys.stderr)
        return EXIT_CONFIG
    if args.json_out == args.md_out:
        print("--json-out and --md-out must differ", file=sys.stderr)
        return EXIT_CONFIG
    try:
        policy = load_policy(args.policy)
        flags = load_runtime_flags(args.runtime_evidence)
        report = evaluate(inputs, policy, flags)
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(canonical_json(report), encoding="utf-8")
        args.md_out.write_text(markdown_summary(report), encoding="utf-8")
    except (OSError, PolicyError, ValueError) as exc:
        print(f"configuration/output error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_CONFIG
    statuses = {decision.status for decision in report.decisions.values()}
    if report.invalid_record_count:
        statuses.add(GraduationStatus.INVALID_EVIDENCE)
    return _exit_code(statuses, has_routes=bool(report.routes))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXIT_CONFIG",
    "EXIT_INSUFFICIENT_EVIDENCE",
    "EXIT_INVALID_EVIDENCE",
    "EXIT_NOT_READY",
    "EXIT_SUCCESS",
    "build_parser",
    "main",
]
