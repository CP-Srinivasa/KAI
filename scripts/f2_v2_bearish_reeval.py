#!/usr/bin/env python3
"""F2-V2 Bearish Re-Evaluation analysis (DS-20260528, spec 2026-05-25).

Operationalises ``docs/strategy/f2_v2_bearish_reeval_spec_20260525.md``: a
read-only analysis that measures whether ``BEARISH_DIRECTIONAL_DISABLED``
(``app/alerts/eligibility.py``, D-142) could be revisited.

This script DECIDES NOTHING and ACTIVATES NOTHING. It computes the empirical
basis (Wilson-Lower-95 per priority bucket), evaluates the three trigger
conditions and maps the result onto the spec's stop-/activation-conditions.
Every actual state change (Shadow-Mode, Live-Switch) remains gated behind the
operator sign-offs SO-1..SO-3 in the spec.

Data reality vs. spec (honest gap markers — KAI rule "no silent assumptions"):
- The spec assumes a ``canonical_documents`` join with columns ``actionable``,
  ``effective_priority`` and ``substantive_pattern``. Those columns do not
  exist. The dispatch-time classification with ``priority`` + ``actionable``
  (+ ``directional_confidence``) lives in ``alert_audit.jsonl`` instead, so we
  source the bearish universe from there and join outcomes by ``document_id``.
- ``substantive_pattern`` (F1 whitelist recovery) is not persisted anywhere, so
  the F1 Bucket-A/B split is UNAVAILABLE. We report a single combined bucket
  and flag ``f1_split_available=false`` (spec §"Bucket A als primary").
- ``directional_confidence`` is present in ``alert_audit`` records, so the F3
  sensitivity split (confidence known vs NULL) is available.

Because bearish directional is currently disabled, the auto-annotator skips
bearish alerts — so live bearish outcomes are near-zero and the script will
typically recommend "defer" (n < 30). That is the spec-expected outcome, not a
failure.

Usage:
    python -m scripts.f2_v2_bearish_reeval \
        --audit artifacts/alert_audit.jsonl \
        --outcomes artifacts/alert_outcomes.jsonl \
        --out-json artifacts/f2_v2_bearish_precision_2026-05-28.json \
        --out-memo artifacts/operator_memos/f2_v2_reeval_2026-05-28.md
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from app.alerts.provenance_metrics import wilson_ci

# ── Spec constants (docs/strategy/f2_v2_bearish_reeval_spec_20260525.md) ──────
TRIGGER_DATE = date(2026, 6, 15)
WINDOW_WEEKS = 8
WINDOW_FLOOR = date(2026, 4, 15)  # F1-deploy minus 3w settling
COVERAGE_MIN_PCT = 20.0  # trigger 2
N_BEARISH_MIN = 30  # trigger 3

# Stufe-1 shadow-start gates (§4)
PRECISION_SHADOW_MIN = 30.0
WILSON_SHADOW_MIN = 15.0

# V1 baseline for comparison columns (24.05 memo)
V1_BASELINE = {"precision_pct": 5.9, "wilson_lower_pct": 1.0, "n": 17}

# §5 stop-condition deferral horizons (weeks)
DEFER_TRIGGER_WEEKS = 4
DEFER_INCONCLUSIVE_WEEKS = 8
DEFER_D142_CONFIRMED_WEEKS = 12


@dataclass(frozen=True)
class BearishOutcome:
    """One bearish dispatch record joined with its latest outcome."""

    document_id: str
    priority: int | None
    outcome: str | None  # "hit" / "miss" / "inconclusive" / None
    dispatched_at: datetime | None
    directional_confidence: float | None = None


@dataclass
class BucketStat:
    bucket: str
    hit: int = 0
    miss: int = 0

    @property
    def resolved(self) -> int:
        return self.hit + self.miss

    @property
    def precision_pct(self) -> float | None:
        if self.resolved == 0:
            return None
        return round(self.hit / self.resolved * 100.0, 2)

    @property
    def wilson_lower_pct(self) -> float | None:
        return wilson_lower_pct(self.hit, self.resolved)

    def to_dict(self) -> dict[str, object]:
        return {
            "bucket": self.bucket,
            "hit": self.hit,
            "miss": self.miss,
            "resolved": self.resolved,
            "precision_pct": self.precision_pct,
            "wilson_lower_pct": self.wilson_lower_pct,
        }


# ── Pure logic (unit-tested) ─────────────────────────────────────────────────


def wilson_lower_pct(hits: int, total: int) -> float | None:
    """Wilson lower bound (95%) as a percentage, or None for empty samples."""
    if total <= 0:
        return None
    ci = wilson_ci(hits, total)
    if ci is None:
        return None
    return round(ci[0] * 100.0, 2)


def priority_bucket(priority: int | None) -> str:
    """Map a priority to the spec's three buckets."""
    if priority is None:
        return "unknown"
    if priority >= 10:
        return "p>=10"
    if priority >= 8:
        return "p=8/9"
    return "p<8"


def rolling_window_start(today: date) -> date:
    """Spec §2: max(WINDOW_FLOOR, today - 8w)."""
    return max(WINDOW_FLOOR, today - timedelta(weeks=WINDOW_WEEKS))


def in_window(dt: datetime | None, start: date, today: date) -> bool:
    if dt is None:
        return False
    d = dt.astimezone(UTC).date()
    return start <= d <= today


def bucket_stats(records: list[BearishOutcome]) -> dict[str, BucketStat]:
    """Hit/miss per priority bucket over resolved (hit|miss) records only."""
    buckets: dict[str, BucketStat] = {}
    for r in records:
        if r.outcome not in ("hit", "miss"):
            continue
        b = priority_bucket(r.priority)
        stat = buckets.setdefault(b, BucketStat(bucket=b))
        if r.outcome == "hit":
            stat.hit += 1
        else:
            stat.miss += 1
    return buckets


def combined_stat(records: list[BearishOutcome]) -> BucketStat:
    combined = BucketStat(bucket="combined")
    for r in records:
        if r.outcome == "hit":
            combined.hit += 1
        elif r.outcome == "miss":
            combined.miss += 1
    return combined


def coverage_pct(resolved_bearish: int, total_bearish_in_window: int) -> float | None:
    """Trigger 2: resolved / actionable-bearish universe, as percent."""
    if total_bearish_in_window <= 0:
        return None
    return round(resolved_bearish / total_bearish_in_window * 100.0, 2)


def evaluate_triggers(
    today: date,
    coverage: float | None,
    n_bearish_resolved: int,
) -> dict[str, object]:
    """Spec §1: all three trigger conditions."""
    date_ok = today >= TRIGGER_DATE
    coverage_ok = coverage is not None and coverage > COVERAGE_MIN_PCT
    n_ok = n_bearish_resolved >= N_BEARISH_MIN
    met = sum((date_ok, coverage_ok, n_ok))
    return {
        "date_ok": date_ok,
        "coverage_ok": coverage_ok,
        "n_ok": n_ok,
        "met_count": met,
        "all_met": met == 3,
    }


def _next_eval(today: date, weeks: int) -> str:
    return (today + timedelta(weeks=weeks)).isoformat()


def recommend(
    today: date,
    triggers: dict[str, object],
    combined: BucketStat,
) -> dict[str, object]:
    """Map empirics + triggers onto spec §4/§5 → a recommendation.

    Returns a decision string, human reason, next-eval date and the spec stage.
    NEVER activates anything; the operator owns SO-1..SO-3.
    """
    w = combined.wilson_lower_pct
    p = combined.precision_pct
    n = combined.resolved

    if not triggers["date_ok"]:
        return {
            "decision": "wait_until_trigger_date",
            "stage": "pre-trigger",
            "reason": f"today < {TRIGGER_DATE.isoformat()} (trigger 1 not met)",
            "next_eval_date": TRIGGER_DATE.isoformat(),
        }
    if not triggers["n_ok"]:
        return {
            "decision": "defer_n_too_low",
            "stage": "deferred",
            "reason": f"n_bearish_resolved={n} < {N_BEARISH_MIN} (trigger 3)",
            "next_eval_date": _next_eval(today, DEFER_TRIGGER_WEEKS),
        }
    if not triggers["coverage_ok"]:
        return {
            "decision": "defer_coverage_low",
            "stage": "deferred",
            "reason": f"coverage <= {COVERAGE_MIN_PCT}% (trigger 2)",
            "next_eval_date": _next_eval(today, DEFER_TRIGGER_WEEKS),
        }
    # All triggers met — evaluate empirics per §5.
    if w is None:
        return {
            "decision": "defer_no_data",
            "stage": "deferred",
            "reason": "no resolved bearish outcomes despite triggers",
            "next_eval_date": _next_eval(today, DEFER_TRIGGER_WEEKS),
        }
    if w < 5.0:
        return {
            "decision": "d142_confirmed_hardened",
            "stage": "stop",
            "reason": f"wilson_lower={w}% < 5% — D-142 quantitatively confirmed",
            "next_eval_date": _next_eval(today, DEFER_D142_CONFIRMED_WEEKS),
        }
    if w < WILSON_SHADOW_MIN:
        return {
            "decision": "inconclusive",
            "stage": "deferred",
            "reason": f"5% <= wilson_lower={w}% < {WILSON_SHADOW_MIN}% — larger sample needed",
            "next_eval_date": _next_eval(today, DEFER_INCONCLUSIVE_WEEKS),
        }
    if p is None or p < PRECISION_SHADOW_MIN:
        return {
            "decision": "threshold_inconsistent",
            "stage": "deferred",
            "reason": (
                f"wilson_lower={w}% >= {WILSON_SHADOW_MIN}% but precision={p}% "
                f"< {PRECISION_SHADOW_MIN}% — enlarge sample, repeat"
            ),
            "next_eval_date": _next_eval(today, DEFER_TRIGGER_WEEKS),
        }
    return {
        "decision": "shadow_start_eligible",
        "stage": "shadow-eligible",
        "reason": (
            f"precision={p}% >= {PRECISION_SHADOW_MIN}%, wilson_lower={w}% "
            f">= {WILSON_SHADOW_MIN}%, n={n} >= {N_BEARISH_MIN} — SO-2 sign-off "
            "required before shadow-mode patch"
        ),
        "next_eval_date": None,
    }


# ── IO / assembly (thin, not unit-tested) ────────────────────────────────────


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


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


@dataclass
class _Universe:
    bearish_in_window: int = 0
    f1_split_available: bool = False
    f3_confidence_known: int = 0
    records: list[BearishOutcome] = field(default_factory=list)


def build_universe(
    audit_rows: list[dict],
    outcome_rows: list[dict],
    today: date,
) -> _Universe:
    """Join bearish dispatch records to their latest outcome within the window.

    Universe (coverage denominator) = actionable bearish audit records in the
    rolling window. Numerator = those with a resolved (hit|miss) outcome.
    """
    latest_outcome: dict[str, str] = {}
    for row in outcome_rows:
        doc_id = row.get("document_id")
        outcome = row.get("outcome")
        if isinstance(doc_id, str) and isinstance(outcome, str):
            latest_outcome[doc_id] = outcome  # last line wins

    start = rolling_window_start(today)
    uni = _Universe()
    seen: set[str] = set()
    for row in audit_rows:
        if (row.get("sentiment_label") or "").lower() != "bearish":
            continue
        if row.get("actionable") is False:
            continue
        doc_id = row.get("document_id")
        if not isinstance(doc_id, str) or doc_id in seen:
            continue
        dt = _parse_iso(row.get("dispatched_at"))
        if not in_window(dt, start, today):
            continue
        seen.add(doc_id)
        uni.bearish_in_window += 1
        conf = row.get("directional_confidence")
        if conf is not None:
            uni.f3_confidence_known += 1
        uni.records.append(
            BearishOutcome(
                document_id=doc_id,
                priority=row.get("priority"),
                outcome=latest_outcome.get(doc_id),
                dispatched_at=dt,
                directional_confidence=conf,
            )
        )
    return uni


def analyze(audit_path: Path, outcomes_path: Path, today: date) -> dict[str, object]:
    uni = build_universe(_read_jsonl(audit_path), _read_jsonl(outcomes_path), today)
    buckets = bucket_stats(uni.records)
    combined = combined_stat(uni.records)
    cov = coverage_pct(combined.resolved, uni.bearish_in_window)
    triggers = evaluate_triggers(today, cov, combined.resolved)
    rec = recommend(today, triggers, combined)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "today": today.isoformat(),
        "window_start": rolling_window_start(today).isoformat(),
        "spec": "docs/strategy/f2_v2_bearish_reeval_spec_20260525.md",
        "gaps": {
            "f1_split_available": uni.f1_split_available,
            "f1_note": "substantive_pattern not persisted — single combined bucket",
            "f3_confidence_known": uni.f3_confidence_known,
            "source": "alert_audit.jsonl (priority/actionable/confidence) + alert_outcomes.jsonl",
        },
        "universe": {
            "bearish_actionable_in_window": uni.bearish_in_window,
            "resolved": combined.resolved,
            "coverage_pct": cov,
        },
        "combined": combined.to_dict(),
        "buckets": {b: s.to_dict() for b, s in sorted(buckets.items())},
        "v1_baseline": V1_BASELINE,
        "triggers": triggers,
        "recommendation": rec,
    }


def render_memo(result: dict[str, object]) -> str:
    rec = result["recommendation"]
    trig = result["triggers"]
    uni = result["universe"]
    comb = result["combined"]
    lines = [
        f"# F2-V2 Bearish Re-Eval Memo — {result['today']}",
        "",
        "**Read-only Analyse — aktiviert nichts. D-142 bleibt bis Operator-Sign-off (SO-2/SO-3).**",
        "",
        f"Spec: `{result['spec']}`  ·  Window ab {result['window_start']}",
        "",
        "## Trigger-Conditions (§1)",
        f"- Datum >= {TRIGGER_DATE.isoformat()}: {'OK' if trig['date_ok'] else 'NEIN'}",
        f"- Coverage > {COVERAGE_MIN_PCT}%: {'OK' if trig['coverage_ok'] else 'NEIN'} "
        f"({uni['coverage_pct']}%)",
        f"- n_bearish_resolved >= {N_BEARISH_MIN}: {'OK' if trig['n_ok'] else 'NEIN'} "
        f"({comb['resolved']})",
        f"- **{trig['met_count']}/3 erfüllt**",
        "",
        "## Empirie (combined)",
        f"- hit {comb['hit']} / miss {comb['miss']} / resolved {comb['resolved']}",
        f"- precision {comb['precision_pct']}%  ·  wilson_lower_95 {comb['wilson_lower_pct']}%",
        f"- V1-Baseline (24.05.): {V1_BASELINE['precision_pct']}% / "
        f"{V1_BASELINE['wilson_lower_pct']}% / n={V1_BASELINE['n']}",
        "",
        "## Priority-Buckets",
        "| Bucket | hit | miss | resolved | precision | wilson_lower_95 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for b, s in result["buckets"].items():
        lines.append(
            f"| {b} | {s['hit']} | {s['miss']} | {s['resolved']} | "
            f"{s['precision_pct']}% | {s['wilson_lower_pct']}% |"
        )
    if not result["buckets"]:
        lines.append("| (keine resolved bearish im Window) | — | — | 0 | — | — |")
    lines += [
        "",
        "## Gap-Marker (Spec-Abweichung)",
        f"- F1-Split (substantive_pattern): {result['gaps']['f1_note']}",
        f"- F3 directional_confidence bekannt: {result['gaps']['f3_confidence_known']} Records",
        f"- Datenquelle: {result['gaps']['source']}",
        "",
        "## Empfehlung (§4/§5)",
        f"- **Decision:** `{rec['decision']}` (stage: {rec['stage']})",
        f"- Begründung: {rec['reason']}",
        f"- Nächster Re-Eval-Termin: {rec['next_eval_date'] or '— (Shadow-Sign-off SO-2)'}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="F2-V2 Bearish Re-Eval (read-only).")
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
