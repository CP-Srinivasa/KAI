"""Build PH5 offline signal baseline report from a JSONL dataset."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.alerts.offline_baseline import (
    build_offline_baseline_report,
    write_offline_baseline_report,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-path",
        default="artifacts/ph4b_tier3_shadow.jsonl",
        help="Input JSONL dataset path (default: artifacts/ph4b_tier3_shadow.jsonl)",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/ph5_baseline",
        help="Output directory for baseline artifacts",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=5.0,
        help="Absolute move threshold (percent) used for directional confirmation",
    )
    parser.add_argument(
        "--horizon-hours",
        type=int,
        default=24,
        help="Evaluation horizon in hours from published_at",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=10,
        help="CoinGecko request timeout",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional cap on parsed candidate rows",
    )
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> int:
    report = await build_offline_baseline_report(
        input_path=Path(args.input_path),
        threshold_pct=float(args.threshold_pct),
        horizon_hours=int(args.horizon_hours),
        timeout_seconds=int(args.timeout_seconds),
        max_rows=args.max_rows,
    )
    json_out, md_out = write_offline_baseline_report(
        report,
        output_dir=Path(args.output_dir),
    )
    print("PH5 offline baseline report written:")
    print(f"  {json_out}")
    print(f"  {md_out}")
    print(
        "Status: "
        f"{report.get('status')} "
        f"(resolved={report.get('resolved_candidates')}, "
        f"corr={report.get('priority_abs_move_correlation')})"
    )
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
