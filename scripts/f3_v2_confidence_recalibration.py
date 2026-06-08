#!/usr/bin/env python3
"""F3-V2 confidence-threshold recalibration analysis (read-only).

Operationalises ``docs/strategy/f3_confidence_recalibration_spec_20260525.md``.
It evaluates whether the bullish/bearish directional-confidence thresholds have
enough outcome evidence for a 14-day shadow recalibration. It never changes
``MIN_DIRECTIONAL_CONFIDENCE_*`` and never enables a shadow/live patch.

Data reality marker: the spec names ``canonical_documents`` as primary source,
but dispatch-time ``actionable`` and outcome linkage are already present in
``alert_audit.jsonl`` + ``alert_outcomes.jsonl``. This script uses those
append-only streams so the result is reproducible on the Pi without DB writes.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.alerts.provenance_metrics import wilson_ci

TRIGGER_DATE = date(2026, 6, 15)
WINDOW_FLOOR = date(2026, 5, 24)
WINDOW_WEEKS = 4
MIN_TOTAL_RESOLVED = 100
MIN_LABEL_RESOLVED = 30
MIN_BIN_RESOLVED = 30
BINS: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 0.99)
CURRENT_THRESHOLDS = {"bullish": 0.80, "bearish": 0.95}
TARGET_FLOORS = {"bullish": 50.0, "bearish": 70.0}
PLATEAU_GAIN_MAX_PP = 5.0


@dataclass(frozen=True)
class ConfidenceOutcome:
    document_id: str
    label: str
    confidence: float
    outcome: str | None
    dispatched_at: datetime | None


@dataclass(frozen=True)
class CurvePoint:
    threshold: float
    selected: int
    hit: int
    miss: int
    hits_total: int

    @property
    def resolved(self) -> int:
        return self.hit + self.miss

    @property
    def precision_pct(self) -> float | None:
        if self.resolved == 0:
            return None
        return round(self.hit / self.resolved * 100.0, 2)

    @property
    def recall_pct(self) -> float | None:
        if self.hits_total == 0:
            return None
        return round(self.hit / self.hits_total * 100.0, 2)

    @property
    def wilson_lower_pct(self) -> float | None:
        if self.resolved == 0:
            return None
        ci = wilson_ci(self.hit, self.resolved)
        return None if ci is None else round(ci[0] * 100.0, 2)

    def to_dict(self) -> dict[str, object]:
        return {
            "threshold": self.threshold,
            "selected": self.selected,
            "hit": self.hit,
            "miss": self.miss,
            "resolved": self.resolved,
            "precision_pct": self.precision_pct,
            "recall_pct": self.recall_pct,
            "wilson_lower_pct": self.wilson_lower_pct,
        }


@dataclass
class _Universe:
    records: list[ConfidenceOutcome] = field(default_factory=list)
    total_directional_confidence_known: int = 0


def rolling_window_start(today: date) -> date:
    return max(WINDOW_FLOOR, today - timedelta(weeks=WINDOW_WEEKS))


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _in_window(dt: datetime | None, start: date, today: date) -> bool:
    if dt is None:
        return False
    d = dt.astimezone(UTC).date()
    return start <= d <= today


def _as_confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    val = float(value)
    if val < 0.0 or val > 1.0:
        return None
    return val


def build_universe(
    audit_rows: list[dict],
    outcome_rows: list[dict],
    today: date,
) -> _Universe:
    latest_outcome: dict[str, str] = {}
    for row in outcome_rows:
        doc_id = row.get("document_id")
        outcome = row.get("outcome")
        if isinstance(doc_id, str) and isinstance(outcome, str):
            latest_outcome[doc_id] = outcome

    start = rolling_window_start(today)
    seen: set[str] = set()
    uni = _Universe()
    for row in audit_rows:
        label = str(row.get("sentiment_label") or "").lower()
        if label not in ("bullish", "bearish"):
            continue
        if row.get("actionable") is False:
            continue
        conf = _as_confidence(row.get("directional_confidence"))
        if conf is None:
            continue
        doc_id = row.get("document_id")
        if not isinstance(doc_id, str) or doc_id in seen:
            continue
        dt = _parse_iso(row.get("dispatched_at"))
        if not _in_window(dt, start, today):
            continue
        seen.add(doc_id)
        uni.total_directional_confidence_known += 1
        uni.records.append(
            ConfidenceOutcome(
                document_id=doc_id,
                label=label,
                confidence=conf,
                outcome=latest_outcome.get(doc_id),
                dispatched_at=dt,
            )
        )
    return uni


def threshold_curve(
    records: list[ConfidenceOutcome],
    *,
    label: str,
    bins: tuple[float, ...] = BINS,
) -> list[CurvePoint]:
    label_records = [r for r in records if r.label == label]
    hits_total = sum(1 for r in label_records if r.outcome == "hit")
    points: list[CurvePoint] = []
    for threshold in bins:
        selected = [r for r in label_records if r.confidence >= threshold]
        points.append(
            CurvePoint(
                threshold=threshold,
                selected=len(selected),
                hit=sum(1 for r in selected if r.outcome == "hit"),
                miss=sum(1 for r in selected if r.outcome == "miss"),
                hits_total=hits_total,
            )
        )
    return points


def label_resolved_counts(records: list[ConfidenceOutcome]) -> dict[str, int]:
    out = {"bullish": 0, "bearish": 0}
    for r in records:
        if r.label in out and r.outcome in ("hit", "miss"):
            out[r.label] += 1
    return out


def evaluate_triggers(today: date, records: list[ConfidenceOutcome]) -> dict[str, object]:
    counts = label_resolved_counts(records)
    total_resolved = sum(counts.values())
    label_ok = {label: n >= MIN_LABEL_RESOLVED for label, n in counts.items()}
    date_ok = today >= TRIGGER_DATE
    total_ok = total_resolved >= MIN_TOTAL_RESOLVED
    return {
        "date_ok": date_ok,
        "total_resolved_ok": total_ok,
        "label_resolved_ok": label_ok,
        "total_resolved": total_resolved,
        "label_resolved": counts,
        "all_met": date_ok and total_ok and all(label_ok.values()),
    }


def _next_precision_gain(points: list[CurvePoint], idx: int) -> float | None:
    if idx + 1 >= len(points):
        return 0.0
    cur = points[idx].precision_pct
    nxt = points[idx + 1].precision_pct
    if cur is None or nxt is None:
        return None
    return round(nxt - cur, 2)


def find_optimal_threshold(
    points: list[CurvePoint],
    *,
    target_floor_pct: float,
    min_resolved: int = MIN_BIN_RESOLVED,
    plateau_gain_max_pp: float = PLATEAU_GAIN_MAX_PP,
) -> dict[str, object]:
    for idx, point in enumerate(points):
        gain = _next_precision_gain(points, idx)
        if (
            point.resolved >= min_resolved
            and point.wilson_lower_pct is not None
            and point.wilson_lower_pct >= target_floor_pct
            and gain is not None
            and gain < plateau_gain_max_pp
        ):
            return {
                "status": "found",
                "threshold": point.threshold,
                "point": point.to_dict(),
                "next_precision_gain_pp": gain,
            }
    return {
        "status": "inconclusive",
        "threshold": None,
        "point": None,
        "next_precision_gain_pp": None,
    }


def recommendation_for_label(
    *,
    label: str,
    optimal: dict[str, object],
    current_threshold: float,
) -> dict[str, object]:
    if optimal["status"] != "found" or optimal["threshold"] is None:
        return {
            "decision": "inconclusive",
            "stage": "deferred",
            "reason": "no threshold satisfied n/wilson/plateau criteria",
        }
    threshold = float(optimal["threshold"])
    delta = round(threshold - current_threshold, 2)
    if abs(delta) <= 0.05:
        return {
            "decision": "current_threshold_confirmed",
            "stage": "stop",
            "reason": f"{label} empirical threshold delta {delta:+.2f} <= 0.05",
        }
    return {
        "decision": "shadow_recalibration_eligible",
        "stage": "shadow-eligible",
        "reason": (
            f"{label} empirical threshold {threshold:.2f} differs from current "
            f"{current_threshold:.2f}; SO-2 required before shadow patch"
        ),
    }


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def analyze(audit_path: Path, outcomes_path: Path, today: date) -> dict[str, object]:
    uni = build_universe(_read_jsonl(audit_path), _read_jsonl(outcomes_path), today)
    triggers = evaluate_triggers(today, uni.records)
    labels: dict[str, object] = {}
    for label in ("bullish", "bearish"):
        points = threshold_curve(uni.records, label=label)
        optimal = find_optimal_threshold(points, target_floor_pct=TARGET_FLOORS[label])
        labels[label] = {
            "current_threshold": CURRENT_THRESHOLDS[label],
            "target_floor_pct": TARGET_FLOORS[label],
            "curve": [p.to_dict() for p in points],
            "optimal": optimal,
            "recommendation": recommendation_for_label(
                label=label,
                optimal=optimal,
                current_threshold=CURRENT_THRESHOLDS[label],
            ),
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "today": today.isoformat(),
        "window_start": rolling_window_start(today).isoformat(),
        "spec": "docs/strategy/f3_confidence_recalibration_spec_20260525.md",
        "source": "alert_audit.jsonl + alert_outcomes.jsonl",
        "universe": {
            "directional_confidence_known": uni.total_directional_confidence_known,
            "records": len(uni.records),
        },
        "triggers": triggers,
        "labels": labels,
    }


def render_memo(result: dict[str, object]) -> str:
    triggers = result["triggers"]
    lines = [
        f"# F3-V2 Confidence Recalibration Memo — {result['today']}",
        "",
        "**Read-only Analyse — aendert keine Confidence-Thresholds.**",
        "",
        f"Spec: `{result['spec']}`  ·  Window ab {result['window_start']}",
        f"Datenquelle: {result['source']}",
        "",
        "## Trigger-Conditions",
        f"- Datum >= {TRIGGER_DATE.isoformat()}: {'OK' if triggers['date_ok'] else 'NEIN'}",
        f"- total_resolved >= {MIN_TOTAL_RESOLVED}: "
        f"{'OK' if triggers['total_resolved_ok'] else 'NEIN'} "
        f"({triggers['total_resolved']})",
        f"- label_resolved >= {MIN_LABEL_RESOLVED}: {triggers['label_resolved']}",
        f"- **all_met={triggers['all_met']}**",
        "",
        "## Label-Ergebnisse",
    ]
    labels = result["labels"]
    for label, payload in labels.items():
        rec = payload["recommendation"]
        opt = payload["optimal"]
        lines += [
            f"### {label}",
            f"- current_threshold: {payload['current_threshold']}",
            f"- target_floor_pct: {payload['target_floor_pct']}",
            f"- optimal: {opt}",
            f"- recommendation: `{rec['decision']}` ({rec['stage']}) — {rec['reason']}",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="F3-V2 confidence recalibration (read-only).")
    ap.add_argument("--audit", default="artifacts/alert_audit.jsonl", type=Path)
    ap.add_argument("--outcomes", default="artifacts/alert_outcomes.jsonl", type=Path)
    ap.add_argument("--out-json", type=Path, default=None)
    ap.add_argument("--out-memo", type=Path, default=None)
    ap.add_argument(
        "--today",
        type=lambda s: date.fromisoformat(s),
        default=datetime.now(UTC).date(),
        help="Override 'today' (testing / dry-runs).",
    )
    args = ap.parse_args()

    result = analyze(args.audit, args.outcomes, args.today)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    if args.out_memo:
        args.out_memo.parent.mkdir(parents=True, exist_ok=True)
        args.out_memo.write_text(render_memo(result), encoding="utf-8")
    print(render_memo(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
