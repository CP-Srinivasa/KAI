"""CLI for offline Jev evidence. It never calls a model or changes runtime state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.jev_shadow_eval.evaluator import evaluate, load_cases, policy_from_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Versioned JSONL evidence")
    parser.add_argument("--policy", type=Path, required=True, help="Pre-registered JSON policy")
    parser.add_argument("--output", type=Path, help="Write the canonical JSON report")
    args = parser.parse_args(argv)
    try:
        policy = policy_from_dict(json.loads(args.policy.read_text(encoding="utf-8")))
        cases, issues, input_sha256 = load_cases(args.input)
        report = evaluate(cases, issues, policy)
        report["input_sha256"] = input_sha256
        report["input_file"] = str(args.input.resolve())
        report["policy_file"] = str(args.policy.resolve())
        serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 0 if report["status"] == "READY_FOR_SHADOW_REVIEW" else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_INPUT", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
