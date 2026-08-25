"""Read-only Binance/Bybit shadow measurement for historical close rows.

The command fetches public candles, runs the existing pure verifier in memory,
and emits a decomposed report.  It never changes a close, an evidence tree, or
an execution book.  In particular, the measured venue tolerances remain
measurements here; this module does not promote them into policy.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.execution.close_evidence import VenueCandle, canonical_bytes
from app.execution.close_evidence_collector import CandleFetcher, build_close_evidence
from app.execution.close_verifier import VerifierVerdict, verify_close
from app.execution.venues.candle_fetchers import BinanceCandleFetcher, BybitCandleFetcher

SHADOW_SCHEMA_VERSION = "close_evidence_shadow/v1"


def _counter_payload(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items()))


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float | None:
        if not ordered:
            return None
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    return {
        "n": len(ordered),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "max": ordered[-1] if ordered else None,
    }


def _covering_candle(timestamp_utc: object, candles: tuple[VenueCandle, ...]) -> VenueCandle | None:
    try:
        stamp = datetime.fromisoformat(str(timestamp_utc).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    timestamp_ms = int(stamp.timestamp() * 1000)
    for candle in candles:
        if candle.open_time_ms <= timestamp_ms < candle.open_time_ms + 60_000:
            return candle
    return None


def _divergence_sample(
    close_row: Mapping[str, object], venue_candles: Mapping[str, VenueCandle]
) -> dict[str, Any] | None:
    if set(venue_candles) != {"binance", "bybit"}:
        return None
    left = venue_candles["binance"]
    right = venue_candles["bybit"]
    left_mid = (left.low + left.high) / 2.0
    right_mid = (right.low + right.high) / 2.0
    denominator = (left_mid + right_mid) / 2.0
    midpoint_pct = abs(left_mid - right_mid) / denominator * 100.0
    band_gap = max(0.0, max(left.low, right.low) - min(left.high, right.high))
    return {
        "fill_id": str(close_row.get("fill_id", "")),
        "symbol": str(close_row.get("symbol", "")),
        "timestamp_utc": str(close_row.get("timestamp_utc", "")),
        "venues": {
            "binance": {"low": left.low, "high": left.high, "midpoint": left_mid},
            "bybit": {"low": right.low, "high": right.high, "midpoint": right_mid},
        },
        "midpoint_pct": midpoint_pct,
        "band_gap_pct": band_gap / denominator * 100.0,
    }


def build_shadow_report(
    rows: Sequence[dict[str, object]],
    *,
    fetchers: Mapping[str, CandleFetcher],
    now_utc: datetime,
) -> dict[str, Any]:
    """Measure every full-close row against each injected venue adapter."""
    venues = tuple(sorted(fetchers))
    if set(venues) != {"binance", "bybit"}:
        raise ValueError("shadow report requires exactly binance and bybit fetchers")
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")

    aggregate = {
        venue: {
            "collection": Counter[str](),
            "verdict": Counter[str](),
            "reasons": Counter[str](),
        }
        for venue in venues
    }
    closes = [row for row in rows if row.get("event_type") == "position_closed"]
    observations: list[dict[str, Any]] = []
    divergence_samples: list[dict[str, Any]] = []

    for close_row in closes:
        per_venue: dict[str, Any] = {}
        covering: dict[str, VenueCandle] = {}
        for venue in venues:
            collected = build_close_evidence(
                close_row,
                venue=venue,
                fetch=fetchers[venue],
                now_utc=now_utc,
            )
            status = collected.status.value
            aggregate[venue]["collection"][status] += 1
            reasons: list[str]
            evidence_sha = ""
            if collected.evidence is None:
                verdict = VerifierVerdict.UNVERIFIED.value
                reasons = [f"collection:{status}"]
            else:
                evidence_sha = collected.payload_sha256
                verified = verify_close(
                    close_row,
                    collected.evidence,
                    expected_evidence_sha256=evidence_sha,
                )
                verdict = verified.verdict.value
                reasons = [reason.value for reason in verified.reasons]
                candle = _covering_candle(
                    close_row.get("timestamp_utc"), collected.evidence.candles
                )
                if candle is not None:
                    covering[venue] = candle
            aggregate[venue]["verdict"][verdict] += 1
            aggregate[venue]["reasons"].update(reasons)
            per_venue[venue] = {
                "collection_status": status,
                "verdict": verdict,
                "reasons": reasons,
                "evidence_sha256": evidence_sha,
            }

        sample = _divergence_sample(close_row, covering)
        if sample is not None:
            divergence_samples.append(sample)
        observations.append(
            {
                "fill_id": str(close_row.get("fill_id", "")),
                "order_id": str(close_row.get("order_id", "")),
                "symbol": str(close_row.get("symbol", "")),
                "timestamp_utc": str(close_row.get("timestamp_utc", "")),
                "venues": per_venue,
            }
        )

    venue_payload = {
        venue: {
            "collection_status_counts": _counter_payload(aggregate[venue]["collection"]),
            "verdict_counts": _counter_payload(aggregate[venue]["verdict"]),
            "reason_counts": _counter_payload(aggregate[venue]["reasons"]),
        }
        for venue in venues
    }
    midpoint_values = [float(sample["midpoint_pct"]) for sample in divergence_samples]
    band_gap_values = [float(sample["band_gap_pct"]) for sample in divergence_samples]
    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "mode": "shadow_read_only",
        "generated_at_utc": now_utc.astimezone(UTC).isoformat(),
        "input_rows": len(rows),
        "eligible_closes": len(closes),
        "venues": venue_payload,
        "divergence": {
            "comparable_n": len(divergence_samples),
            "unavailable_n": len(closes) - len(divergence_samples),
            "midpoint_pct": _distribution(midpoint_values),
            "band_gap_pct": _distribution(band_gap_values),
            "samples": divergence_samples,
        },
        "observations": observations,
    }


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            if not raw.strip():
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_number}: {exc.msg}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"invalid JSON object at line {line_number}")
            rows.append(payload)
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure close evidence without changing books")
    parser.add_argument("--shadow", action="store_true", help="required read-only mode guard")
    parser.add_argument("--audit", type=Path, required=True, help="paper audit JSONL input")
    parser.add_argument("--output", type=Path, help="optional JSON report path; default stdout")
    args = parser.parse_args(argv)
    if not args.shadow:
        parser.error("--shadow is mandatory; no mutating mode exists")

    report = build_shadow_report(
        _read_jsonl(args.audit),
        fetchers={"binance": BinanceCandleFetcher(), "bybit": BybitCandleFetcher()},
        now_utc=datetime.now(UTC),
    )
    encoded = canonical_bytes(report) + b"\n"
    if args.output is None:
        sys.stdout.buffer.write(encoded)
    else:
        args.output.write_bytes(encoded)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
