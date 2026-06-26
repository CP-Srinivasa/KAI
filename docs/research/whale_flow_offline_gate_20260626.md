# Whale exchange-flow — Phase-0 offline edge gate (2026-06-26)

**Verdict: no directional edge. 0 survivors across all horizons. Valid $0 result.**

## Question

Does whale / large-transaction exchange flow predict short-horizon price direction
for KAI's majors? Operator asked whether to add a "whale-alert" data source. Before
building any live plumbing, the cheapest decisive test: run the signal through the
existing BH-FDR edge-discovery harness on real historical data.

## Method (capital-free, point-in-time honest)

- **Data:** Whale Alert free archive (`whale-alert.io/whale-alerts-archive.json.gzip`,
  research-licensed, $0) — 100,105 alerts 2018–2026. Reduced to signed exchange flow
  per asset (`to ∈ exchange` = +inflow, `from ∈ exchange` = -outflow; internal /
  non-exchange transfers excluded) by `scripts/build_whale_flow_series.py`. Event time =
  on-chain confirmation time → conservative, no look-ahead.
- **Features (causal, `app/analysis/features/whale_flow_align.py`):** trailing-24h net
  flow + rolling z-score, as-of-joined onto 1h bars (`event_ms <= bar_open_ms`), same
  no-look-ahead discipline as the funding align. `coin_netflow_z` (asset's own coin) and
  market-wide `stable_netflow_z`.
- **Hypotheses (`app/research/whale_hypotheses.py`):** the two textbook flow theories,
  both directions — `coin_inflow_short` / `coin_outflow_long` (coins to exchange =
  selling) and `stable_inflow_long` / `stable_outflow_short` (stablecoins to exchange =
  dry powder). Tested in the SAME BH-FDR batch as the TA+funding set (honest bar) over
  BTC/ETH/SOL, 365d, horizons {4,12,24} bars, cost-net.
- **Run:** `python scripts/whale_netflow_research.py --lookback-days 365`.

Coverage was strong (BTC/ETH ~99.7% of bars with a defined `netflow_z`, 2,786–4,269
trades per hypothesis; SOL thinner at ~52% / 489 events).

## Result (trade-weighted mean net bps, cost-adjusted)

| hypothesis | h=4 | h=12 | h=24 |
|---|---|---|---|
| coin_inflow_short | −15.4 | −14.4 | −8.2 |
| coin_outflow_long | −25.0 | −26.9 | −30.0 |
| stable_inflow_long | −23.3 | −42.7 | −43.6 |
| stable_outflow_short | −17.5 | −11.0 | **+15.6** |

A single positive cell (stable_outflow_short @ h=24) does **not** survive BH-FDR (0/3
symbols, not robust). Every other cell is negative; most are negative even pre-cost.

## Conclusion & decision

The on-chain *transfer* form of whale data has **no usable directional edge** here —
matching the academic finding that large transfers track volatility more than
direction, and are lagging/noisy. **Stop** investing in the transfer-form source.

The reusable mechanism (causal flow feature + deciders + research harness) ships so the
next, stronger data type — **Hyperliquid live perp-position transparency** (pre-unwind,
not lagging; Phase 1) — plugs into the same `*_netflow_z` feature slot. Hyperliquid is a
*different* data type, so this negative does not condemn it; it informs the decider
design. All whale hypotheses are recorded in the shared hypothesis ledger (honest
cumulative trial count → harder DSR bar at any future promotion).
