# Token-unlock short — beta-neutral validation (2026-06-26)

**Verdict: NO promotable unlock edge. The BH-FDR "+162bps survivor" was alt-beta plus
autocorrelation-inflated n. De-overlapped to independent holds and beta-neutralised,
the pooled timing alpha is NEGATIVE (-111bps, Sharpe -0.08, p(mean>0)=0.15). The one
clean candidate (ZRO) fails the promotion gate hard (DSR 0.18, n=14).**

## Why this run

The Phase-1 gate (`unlock_pressure_offline_gate_20260626.md`, PR #484) found
`unlock_imminent_short` surviving BH-FDR @7d (+162bps, 20k trades) — KAI's first
survivor. Its built-in confound check already smelled beta (always-short was already
positive for ~all tokens), with ZRO the lone clean-looking timing alpha. Doctrine
(`kai_edge_discovery_validation_doctrine_20260625`): BH-FDR survival != edge. This run
applies the formal promotion gate to settle it.

## Method (read-only, capital-free)

`scripts/unlock_validation.py`, h=168 bars (7d), 730d, 20bps round-trip cost:

1. **De-overlap** (`select_independent`): a 7d hold makes adjacent hourly z>1 entries
   the *same* trade. Keep only entries spaced >= 168 bars apart -> ~independent holds.
   This collapses each symbol's ~2000-4000 overlapping "trades" to **14-25 independent
   ones** — the honest n the BH-FDR screen never had.
2. **Beta-neutralise**: per symbol `alpha_i = mean_fwd - fwd_i` (short-timing return in
   excess of the symbol's own unconditional forward return). >0 means unlock timing
   beat simply being short. This removes the 2024-26 alt-downtrend beta.
3. **Gate** (`app/observability/edge_validation_gate.evaluate_edge_validation`,
   never an entry-path import): DSR deflated by the honest cumulative trial count
   (42, from the shared ledger), MinTRL, n>=100, outlier-robust + an
   autocorrelation-robust bootstrap p(mean>0).
4. **Funding context** (`BinanceFuturesAdapter.get_funding_rate_history`): realized
   perp funding a short earns/pays over each 7d hold — a real cost the OHLCV backtest
   omits.

## Result (h=168, de-overlapped, beta-neutral alpha in bps)

| symbol | n_overlap | n_indep | alpha | p(mean>0) | raw_short_net | funding | DSR |
|---|---|---|---|---|---|---|---|
| ARB  | 4168 | 25 | **-305** | 0.19 | -188 | +11 | n/a |
| APT  | 4032 | 24 | **-153** | 0.36 | -22  | -20 | n/a |
| SEI  | 3973 | 24 | **-690** | **0.03** | -635 | -22 | n/a |
| ALT  | 3194 | 20 | +375 | 0.92 | +565 | -19 | 0.16 |
| JUP  | 2320 | 16 | **-134** | 0.17 | -116 | -22 | n/a |
| ZRO  | 2043 | 14 | +461 | 0.96 | +455 | -19 | **0.18** |
| TIA  | 168  | 1  | +1807 | n/a | +1945 | -93 | n/a |
| ONDO | 168  | 1  | +360 | n/a | +434 | -1  | n/a |

**Pooled** (n=125, clears the n>=100 floor): mean **-110.9bps**, Sharpe **-0.08**,
p(mean>0)=**0.147** -> fails `cost_net_positive`; DSR/MinTRL undefined (no positive
Sharpe to deflate). **gate_ready=False.**

## Reading

- **The headline edge evaporates under de-overlapping + beta-neutralisation.** Pooled
  timing alpha is negative and insignificant. The earlier +162bps was alt-beta
  (always-short already profited as alts fell 2024-26) on a hugely autocorrelated n.
- **Per symbol it is mostly negative**, and SEI is *significantly* negative
  (p=0.03 for mean>0): shorting into SEI unlocks did materially worse than always-short.
  Only ALT/ZRO are positive among the n>=14 names; TIA/ONDO are single-event noise.
- **Even ZRO, the lone clean candidate, fails the gate**: DSR 0.18 (needs >=0.95) on
  n=14 (needs >=100). A real but tiny, undeflatable sample — not promotable.
- **Funding makes it worse, not better**: shorts *paid* ~20bps/7d funding on these
  alts on average (TIA -93bps) — a cost the OHLCV backtest had omitted.

## Conclusion

The unlock-short signal is **not a promotable edge** by the doctrine's gate, on any
cut (pooled, per-symbol, or isolating ZRO). This is the win the gate exists for: the
confound + de-overlap + DSR caught what the BH-FDR survival screen alone mislabelled
as edge. The reusable unlock-pressure feature/ledger machinery stays (it is the honest
trial count that, correctly, deflates everything downstream); the verdict is a clean,
capital-free "no edge — do not promote." No code touches the entry/execution path.
