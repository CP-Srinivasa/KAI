# Watchdog: Source Reputation Engine & Risk-adjusted Agent Scoreboard

**Status:** scoring cores LIVE (code + tests), live-data wiring = follow-up.
**Owner:** Watchdog (Monitoring / Source Quality / Agent Quality / Drift).
**Date:** 2026-06-05.
**Modules:**
- `app/observability/source_reputation.py`
- `app/observability/agent_scoreboard.py`
- Tests: `tests/unit/test_source_reputation.py`, `tests/unit/test_agent_scoreboard.py`

Both are **pure, deterministic scoring cores**. They fuse signals KAI already
produces; they do **not** invent evidence and they do **not** authorise any
trade. Output is advisory monitoring only.

---

## 1. Source Reputation Engine

Per-source multi-dimensional reputation built on the existing source-quality
substrate (`app/learning/source_reliability.py` Wilson lower-bound,
`app/risk/manipulation_detection_models.SourceTrustReport`, edge/outcome data,
source-confluence/dedup).

### Dimensions
Scored (weighted): `historical_accuracy`, `timeliness`, `originality`,
`independence`, `domain_relevance`, `realized_signal_quality`, `conflict_rate`
(neg), `manipulation_probability` (neg).
Tracked/reported only (operator-requested, not in the formula):
`correction_history`, `bot_probability`.

### Score (exact operator weights — not renormalised)
```
source_reputation =
    0.22*historical_accuracy + 0.15*timeliness + 0.14*originality
  + 0.12*independence + 0.12*domain_relevance + 0.10*realized_signal_quality
  - 0.08*conflict_rate - 0.07*manipulation_probability
```
Positive weights sum to **0.85** → that is the maximum achievable reputation.
The `>0.80` band is deliberately reachable only by sources near-perfect on every
positive axis with zero conflict/manipulation. High trust is earned.

### Usage gate (advisory tier; boundary → higher band)
| reputation | tier | meaning |
|---|---|---|
| `< 0.30` | `research_only` | background context only |
| `[0.30, 0.60)` | `supporting_evidence` | may corroborate, not drive |
| `[0.60, 0.80)` | `signal_support` | may contribute to a signal |
| `>= 0.80` | `high_trust_support` | strong corroboration |

### Missing evidence
Each unprovided dimension uses a conservative neutral default (positive axes
0.5 = "unknown", negative axes 0.0 = "no detected evidence") and is flagged in
`provided` / `data_completeness`. A cold-start source with no evidence scores
`0.5 * 0.85 = 0.425` → `supporting_evidence`, `low_confidence = True`. It can
**never** reach `signal_support`/`high_trust_support` without real evidence.

### Safety invariant (enforced + tested)
> No source — at any reputation, including `>0.80` — may alone trigger execution.

Every `SourceReputationScore` carries `can_trigger_execution_alone = False` and
`max_role = "support"`. Execution authority lives in the risk-gate chain and the
`EXECUTION_ENTRY_MODE` kill switch, never here.

---

## 2. Risk-adjusted Agent Scoreboard

Ranks agents by edge **quality**, not raw PnL, fusing learning / calibration /
walk-forward / trade data.

### Displayed metrics
PnL, Sharpe, Sortino, Max Drawdown, CVaR contribution, Hit Rate, Payoff Ratio,
EV-after-costs, Brier, Calibration Error, IC-by-horizon, Signal Decay,
Overtrading Penalty, regime performance, `source_quality_dependency`.

### Score (exact operator weights)
```
agent_score =
    0.20*EV_after_costs + 0.15*Sharpe + 0.15*Sortino + 0.15*calibration_quality
  + 0.10*IC_stability + 0.10*drawdown_quality + 0.10*regime_robustness
  - 0.05*overtrading_penalty
```
Raw metrics live on incompatible scales, so each term is a **documented,
monotone, linear-clamped normaliser into [0,1]** (1 = best). Raw metrics **and**
normalised sub-scores are both reported — no hidden constants. Anchor points:
- EV: `-50bps→0`, `0→0.5`, `+50bps→1`
- Sharpe/Sortino: `-2→0`, `0→0.5`, `+2→1`
- Calibration: Brier `0→1`, `0.25→0`; ECE `0→1`, `0.25→0` (mean of available)
- IC stability: positive mean IC × cross-horizon consistency (sign-flips punished)
- Drawdown: `0→1`, `-50%→0`
- Regime robustness: `0.5*mean + 0.5*worst-case` over per-regime EV

### Operator requirements (encoded as tests A/B/C/D)
- **A** high PnL + toxic drawdown → not top (drawdown_quality + Sharpe/Sortino pull down).
- **B** low PnL + stable EV + good calibration → ranks above A.
- **C** high confidence + poor hit-rate → low `calibration_quality`, flagged.
- **D** many signals + no EV after costs → low EV-quality + overtrading penalty, ranked last.

`source_quality_dependency` is surfaced as a **risk flag** (not weighted), tying
the scoreboard back to the Source Reputation Engine: an agent whose edge leans on
low-reputation sources is flagged even if its raw numbers look fine.

---

## 3. Wiring status (honest)

**Live now:** both scoring cores, adapters
(`merge_trust_into_inputs`, `reliability_tier_to_accuracy`), report builders,
32 unit tests, ruff/format/mypy clean.

**Follow-up (not in this change):**
1. A collector that reads `monitor/source_reliability.json`, the manipulation
   report and edge/outcome artifacts into `SourceReputationInputs`.
2. A collector that reads calibration / walk-forward / cohort-edge artifacts into
   `AgentMetricInputs`.
3. CLI + Watchdog dropbox emission (`artifacts/agents/watchdog/*.jsonl`) and a
   dashboard surface.

The cores are intentionally shipped first and separately because — per the
04.06. shadow report — `real_resolved = 0`: there is barely any realised
agent/trade data to aggregate yet. The engines are ready to consume it the
moment it exists; wiring them before the data is real would produce
canary-contaminated scoreboards (cf. the V1 shadow-degeneration incident).

**Invariant across both:** monitoring output, never an execution trigger.
`EXECUTION_ENTRY_MODE` stays `disabled`.
