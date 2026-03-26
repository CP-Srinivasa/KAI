"""Offline signal-quality baseline using historical CoinGecko prices."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.market_data.coingecko_adapter import CoinGeckoAdapter

OFFLINE_BASELINE_JSON = "ph5_offline_signal_baseline.json"
OFFLINE_BASELINE_MD = "ph5_offline_signal_baseline.md"


@dataclass(frozen=True)
class BaselineCandidate:
    document_id: str
    published_at: datetime
    priority: int
    sentiment_label: str
    assets: list[str]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x == 0.0 or den_y == 0.0:
        return None
    return round(num / (den_x * den_y), 4)


def _extract_priority(row: dict[str, Any]) -> int | None:
    for key in ("priority", "recommended_priority", "priority_score"):
        value = row.get(key)
        if isinstance(value, int) and 1 <= value <= 10:
            return value
    analysis = row.get("analysis")
    if isinstance(analysis, dict):
        value = analysis.get("recommended_priority")
        if isinstance(value, int) and 1 <= value <= 10:
            return value
    return None


def _extract_published_at(row: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "document_published_at", "timestamp", "dispatched_at"):
        raw = row.get(key)
        if not isinstance(raw, str):
            continue
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _extract_assets(row: dict[str, Any]) -> list[str]:
    assets: list[str] = []
    for key in ("affected_assets", "assets", "crypto_assets", "tickers"):
        raw = row.get(key)
        if isinstance(raw, list):
            for value in raw:
                if isinstance(value, str) and value.strip():
                    assets.append(value.strip().upper())
    analysis = row.get("analysis")
    if isinstance(analysis, dict):
        raw = analysis.get("affected_assets")
        if isinstance(raw, list):
            for value in raw:
                if isinstance(value, str) and value.strip():
                    assets.append(value.strip().upper())
    deduped: list[str] = []
    seen: set[str] = set()
    for asset in assets:
        if asset in seen:
            continue
        seen.add(asset)
        deduped.append(asset)
    return deduped


def _extract_sentiment(row: dict[str, Any]) -> str:
    for key in ("sentiment_label", "sentiment"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    analysis = row.get("analysis")
    if isinstance(analysis, dict):
        value = analysis.get("sentiment_label")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return "unknown"


def _extract_document_id(row: dict[str, Any], idx: int) -> str:
    for key in ("document_id", "doc_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"row-{idx + 1}"


def _rate_pct(num: int, den: int) -> float | None:
    if den <= 0:
        return None
    return round(num / den * 100.0, 2)


async def build_offline_baseline_report(
    *,
    input_path: Path,
    threshold_pct: float = 5.0,
    horizon_hours: int = 24,
    timeout_seconds: int = 10,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """Build offline baseline report from a JSONL input dataset."""
    generated_at = datetime.now(UTC).isoformat()
    rows = _load_jsonl(input_path)
    if not rows:
        return {
            "report_type": "ph5_offline_signal_baseline",
            "generated_at": generated_at,
            "input_path": str(input_path),
            "status": "missing_or_empty_input",
            "input_rows": 0,
            "candidates": 0,
            "resolved_candidates": 0,
        }

    parsed_candidates: list[BaselineCandidate] = []
    skip_reasons: Counter[str] = Counter()
    for idx, row in enumerate(rows):
        if bool(row.get("is_digest")):
            skip_reasons["digest_event"] += 1
            continue
        doc_id = _extract_document_id(row, idx)
        published_at = _extract_published_at(row)
        if published_at is None:
            skip_reasons["missing_published_at"] += 1
            continue
        priority = _extract_priority(row)
        if priority is None:
            skip_reasons["missing_priority"] += 1
            continue
        assets = _extract_assets(row)
        if not assets:
            skip_reasons["missing_assets"] += 1
            continue

        parsed_candidates.append(
            BaselineCandidate(
                document_id=doc_id,
                published_at=published_at,
                priority=priority,
                sentiment_label=_extract_sentiment(row),
                assets=assets,
            )
        )
        if max_rows is not None and len(parsed_candidates) >= max_rows:
            break

    # Dedupe channel-level rows by document_id and keep latest timestamp.
    latest_by_doc: dict[str, BaselineCandidate] = {}
    for cand in parsed_candidates:
        prev = latest_by_doc.get(cand.document_id)
        if prev is None or cand.published_at >= prev.published_at:
            latest_by_doc[cand.document_id] = cand
    parsed_candidates = list(latest_by_doc.values())

    adapter = CoinGeckoAdapter(timeout_seconds=timeout_seconds)
    cache: dict[tuple[str, str, int], tuple[float, float, float] | None] = {}
    resolved_rows: list[dict[str, Any]] = []
    unresolved = 0
    directional_hits = 0
    directional_resolved = 0
    high_priority_threshold = 7
    high_resolved = 0
    high_hits = 0
    low_resolved = 0
    low_hits = 0

    for cand in parsed_candidates:
        horizon_ts = cand.published_at + timedelta(hours=horizon_hours)
        best: tuple[str, float, float, float] | None = None  # asset, p0, p1, move
        bucket_start = cand.published_at.replace(minute=0, second=0, microsecond=0)
        bucket_end = bucket_start + timedelta(hours=horizon_hours)
        for asset in cand.assets:
            cache_key = (asset, bucket_start.isoformat(), horizon_hours)
            if cache_key not in cache:
                symbol = f"{asset}/USDT" if "/" not in asset else asset
                cache[cache_key] = await adapter.get_price_change_between(
                    symbol,
                    start_utc=bucket_start,
                    end_utc=bucket_end,
                )
            movement = cache[cache_key]
            if movement is None:
                continue
            p0, p1, move = movement
            if best is None or abs(move) > abs(best[3]):
                best = (asset, p0, p1, move)

        if best is None:
            unresolved += 1
            continue

        asset, p0, p1, move_pct = best
        abs_move = abs(move_pct)
        directional_outcome = "inconclusive"
        sentiment = cand.sentiment_label
        if sentiment in {"bullish", "bearish"}:
            if abs_move >= threshold_pct:
                directional_resolved += 1
                if (sentiment == "bullish" and move_pct > 0) or (
                    sentiment == "bearish" and move_pct < 0
                ):
                    directional_outcome = "hit"
                    directional_hits += 1
                else:
                    directional_outcome = "miss"
            else:
                directional_outcome = "inconclusive"

        if directional_outcome in {"hit", "miss"}:
            if cand.priority >= high_priority_threshold:
                high_resolved += 1
                if directional_outcome == "hit":
                    high_hits += 1
            else:
                low_resolved += 1
                if directional_outcome == "hit":
                    low_hits += 1

        resolved_rows.append(
            {
                "document_id": cand.document_id,
                "published_at": cand.published_at.isoformat(),
                "horizon_at": horizon_ts.isoformat(),
                "price_window_bucket_start": bucket_start.isoformat(),
                "price_window_bucket_end": bucket_end.isoformat(),
                "asset": asset,
                "sentiment_label": cand.sentiment_label,
                "priority": cand.priority,
                "price_at_start": p0,
                "price_at_horizon": p1,
                "move_pct": move_pct,
                "abs_move_pct": round(abs_move, 4),
                "threshold_confirmed": abs_move >= threshold_pct,
                "directional_outcome": directional_outcome,
            }
        )

    priorities = [float(r["priority"]) for r in resolved_rows]
    abs_moves = [float(r["abs_move_pct"]) for r in resolved_rows]
    signed_moves = [float(r["move_pct"]) for r in resolved_rows]
    report = {
        "report_type": "ph5_offline_signal_baseline",
        "generated_at": generated_at,
        "input_path": str(input_path),
        "status": "ok" if resolved_rows else "insufficient_resolved_data",
        "threshold_pct": threshold_pct,
        "horizon_hours": horizon_hours,
        "input_rows": len(rows),
        "candidates": len(parsed_candidates),
        "resolved_candidates": len(resolved_rows),
        "unresolved_candidates": unresolved,
        "skip_reasons": dict(skip_reasons),
        "confirmation_rate_pct": _rate_pct(
            sum(1 for r in resolved_rows if bool(r["threshold_confirmed"])),
            len(resolved_rows),
        ),
        "directional_resolved": directional_resolved,
        "directional_hits": directional_hits,
        "directional_hit_rate_pct": _rate_pct(directional_hits, directional_resolved),
        "priority_abs_move_correlation": _pearson(priorities, abs_moves),
        "priority_signed_move_correlation": _pearson(priorities, signed_moves),
        "high_priority_threshold": high_priority_threshold,
        "high_priority_hit_rate_pct": _rate_pct(high_hits, high_resolved),
        "low_priority_hit_rate_pct": _rate_pct(low_hits, low_resolved),
        "resolved_sample_rows": resolved_rows[:50],
    }
    return report


def write_offline_baseline_report(
    report: dict[str, Any],
    *,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write JSON + markdown summary and return both paths."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_out = output_dir / OFFLINE_BASELINE_JSON
    md_out = output_dir / OFFLINE_BASELINE_MD
    json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = [
        "# PH5 Offline Signal Baseline",
        "",
        f"Generated: {report.get('generated_at')}",
        f"Input: `{report.get('input_path')}`",
        "",
        "## Status",
        "",
        f"- status: `{report.get('status')}`",
        f"- input_rows: {report.get('input_rows')}",
        f"- candidates: {report.get('candidates')}",
        f"- resolved_candidates: {report.get('resolved_candidates')}",
        f"- unresolved_candidates: {report.get('unresolved_candidates')}",
        "",
        "## Quality Metrics",
        "",
        f"- threshold_pct: {report.get('threshold_pct')}",
        f"- horizon_hours: {report.get('horizon_hours')}",
        f"- confirmation_rate_pct: {report.get('confirmation_rate_pct')}",
        f"- directional_hit_rate_pct: {report.get('directional_hit_rate_pct')}",
        f"- priority_abs_move_correlation: {report.get('priority_abs_move_correlation')}",
        f"- priority_signed_move_correlation: {report.get('priority_signed_move_correlation')}",
        f"- high_priority_hit_rate_pct: {report.get('high_priority_hit_rate_pct')}",
        f"- low_priority_hit_rate_pct: {report.get('low_priority_hit_rate_pct')}",
    ]
    md_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_out, md_out
