"""Machine-checkable pre-registration gates — no human between measurement and verdict.

The 2026-07-01 verdict error had a deeper cause than a truncated terminal: the
ledger's success criteria are FREE TEXT, so a human reads numbers off a JSON and
transcribes a conclusion. This module closes that gap: a claim can carry a
structured ``gate`` (registered BEFORE data, hashed into the ``prereg_id``), and
``check_gate`` computes PASS/FAIL mechanically from an evaluator's JSON output.
The chain becomes: pre-register (machine-readable bar) → measure (``--json``) →
mechanical verdict → attested report. Fail-closed: anything missing, malformed
or not measurable reads as NOT passed, with the reason recorded.

Gate schema (all thresholds fixed at registration time):

    {
      "level": "overall" | "stories" | "pooled",   # which result block is judged
      "horizon_s": 86400,                           # horizon key inside the block
      "n_min": 300,                                 # minimum sample at that horizon
      "p_min": 0.95,                                # bootstrap/normal P(mean>0) bar
      "require_cost_clearing": true,                # mean_bps >= cost_ref_bps
      "max_top_symbol_share": 0.8,                  # optional (overall/stories)
      "i2_max": 0.5,                                # optional (pooled only)
      "k_min": 8                                    # optional (pooled only)
    }

Pure; consumed by the ``trading prereg-check`` CLI.
"""

from __future__ import annotations

from typing import Any

GATE_LEVELS = ("overall", "stories", "pooled")

_REQUIRED_KEYS = ("level", "horizon_s", "n_min", "p_min")


def validate_gate(gate: dict[str, Any]) -> None:
    """Raise ``ValueError`` on a malformed gate (checked at registration time)."""
    for key in _REQUIRED_KEYS:
        if key not in gate:
            raise ValueError(f"gate missing required key {key!r}")
    if gate["level"] not in GATE_LEVELS:
        raise ValueError(f"gate level must be one of {GATE_LEVELS}, got {gate['level']!r}")
    if not isinstance(gate["horizon_s"], int) or gate["horizon_s"] <= 0:
        raise ValueError("gate horizon_s must be a positive integer (seconds)")
    if not 0.5 <= float(gate["p_min"]) < 1.0:
        raise ValueError("gate p_min must be in [0.5, 1.0)")
    if int(gate["n_min"]) <= 0:
        raise ValueError("gate n_min must be > 0")


def check_gate(gate: dict[str, Any], eval_result: dict[str, Any]) -> dict[str, Any]:
    """Mechanically judge an evaluator JSON against a registered gate (fail-closed).

    Returns ``{passed, verdict, checks: [{name, required, actual, ok}, ...]}``;
    a missing result block or horizon makes the claim NOT MEASURABLE (not passed)
    rather than raising — the reason is the failing check.
    """
    checks: list[dict[str, Any]] = []

    def _check(name: str, required: Any, actual: Any, ok: bool) -> bool:
        checks.append({"name": name, "required": required, "actual": actual, "ok": bool(ok)})
        return bool(ok)

    level = str(gate["level"])
    horizon = int(gate["horizon_s"])
    row = _resolve_row(eval_result, level, horizon)
    measurable = _check(f"{level}@{horizon}s present", "result block exists", bool(row), bool(row))
    passed = measurable
    if row:
        n_key = "n_total" if level == "pooled" else "n"
        n = int(row.get(n_key, 0))
        passed &= _check("n_min", int(gate["n_min"]), n, n >= int(gate["n_min"]))

        p_key = "p_positive_normal" if level == "pooled" else "p_positive"
        p = row.get(p_key)
        p_ok = p is not None and float(p) >= float(gate["p_min"])
        passed &= _check("p_min", float(gate["p_min"]), p, p_ok)

        if gate.get("require_cost_clearing"):
            mean_key = "pooled_mean_bps" if level == "pooled" else "mean_bps"
            mean = float(row.get(mean_key, 0.0))
            cost = row.get("cost_ref_bps")
            if cost is None:  # pooled block carries no cost bar → use top-level base
                cost = eval_result.get("cost_bps")
            cost_ok = cost is not None and mean >= float(cost)
            passed &= _check("cost_clearing", f"mean>=cost({cost})", mean, cost_ok)

        if gate.get("max_top_symbol_share") is not None and level != "pooled":
            share = row.get("top_symbol_share")
            share_ok = share is not None and float(share) <= float(gate["max_top_symbol_share"])
            passed &= _check(
                "max_top_symbol_share", float(gate["max_top_symbol_share"]), share, share_ok
            )

        if gate.get("i2_max") is not None and level == "pooled":
            i2 = row.get("i_squared")
            i2_ok = i2 is not None and float(i2) <= float(gate["i2_max"])
            passed &= _check("i2_max", float(gate["i2_max"]), i2, i2_ok)

        if gate.get("k_min") is not None and level == "pooled":
            k = int(row.get("k_sources", 0))
            passed &= _check("k_min", int(gate["k_min"]), k, k >= int(gate["k_min"]))

    failed = [c["name"] for c in checks if not c["ok"]]
    verdict = (
        "PASSED: every registered criterion met"
        if passed
        else f"FAILED at registered gate ({', '.join(failed)})"
    )
    return {"passed": bool(passed), "verdict": verdict, "checks": checks}


def _resolve_row(eval_result: dict[str, Any], level: str, horizon_s: int) -> dict[str, Any] | None:
    """Locate the judged row; tolerant of str horizon keys (JSON round-trip)."""

    def _by_horizon(block: dict[Any, Any] | None) -> dict[str, Any] | None:
        if not isinstance(block, dict):
            return None
        for key in (horizon_s, str(horizon_s)):
            candidate = block.get(key)
            if isinstance(candidate, dict):
                return candidate
        return None

    if level == "pooled":
        return _by_horizon(eval_result.get("pooled"))
    cohort = eval_result.get(level)
    if not isinstance(cohort, dict):
        return None
    row = _by_horizon(cohort.get("horizons"))
    if row is not None:
        row = dict(row)
        row.setdefault("n", cohort.get("n"))
    return row


__all__ = ["GATE_LEVELS", "check_gate", "validate_gate"]
