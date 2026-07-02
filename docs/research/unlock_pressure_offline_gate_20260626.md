# Token-unlock pressure — Phase-1 offline edge gate (2026-06-26)

**Verdict: first BH-FDR *survivor* — but the built-in confound check shows it is
mostly alt-beta, not unlock timing. A candidate to scrutinise, NOT a promotable edge.**

## Question

Does scheduled token-unlock pressure ("a large unlock is imminent → short") have a
real, capital-free directional edge? This is the doctrine's strongest *documented*
crypto edge candidate, so unlike the whale-transfer gate a survivor was plausible.

## Method (capital-free, point-in-time honest)

- **Data:** DefiLlama free unlock dataset (`defillama-datasets.llama.fi/emissions/{slug}`,
  no key) → per token the scheduled `unlockEvents` (cliff + linear allocations) +
  max supply. Reduced by `scripts/build_unlock_events.py`. Universe = 11 unlock
  tokens with a Binance perp (APT/ARB/TIA/SEI/JUP/ALT/ZRO/ONDO + single-cliff
  DYDX/WLD/ARKM which never trade).
- **Feature (causal, `app/analysis/features/unlock_align.py`):** fraction of max
  supply scheduled to unlock in the next 7 days, as-of each bar (the schedule is
  PUBLIC in advance → causal w.r.t. information, never future price), + rolling z.
- **Hypotheses (`app/research/unlock_hypotheses.py`):** `unlock_imminent_short`
  (z>1 → short) and its mirror `unlock_quiet_long`, tested in the SAME BH-FDR batch
  as the TA+funding set (honest bar), 730d, horizons {24,72,168} bars (1d/3d/7d).
- **Confound check (the decisive extra scrutiny):** per symbol at h=168, compare the
  ALWAYS-short net (alt-beta) vs the UNLOCK-TIMED short net (z>1 bars only). The
  BH-FDR screen does not do this.
- **Run:** `python scripts/build_unlock_events.py && python scripts/unlock_pressure_research.py`.

## Result

`unlock_imminent_short` **survives** BH-FDR: 3/11 symbols @h=24 (mean −21bps), 4/11
@h=72 (+0.6bps), **5/11 @h=168 (+162bps net, 20k trades)**. The mirror
`unlock_quiet_long` loses symmetrically (−22bps @h=168) — already a beta smell.

**Confound check (h=168, net bps):**

| token | always-short | unlock-timed | timing-alpha | n_timed |
|---|---|---|---|---|
| APT | +130.6 | −151.6 | **−282.2** | 4032 |
| ARB | +117.8 | +103.4 | −14.4 | 4168 |
| SEI | +54.5 | −119.8 | **−174.3** | 3973 |
| JUP | +18.1 | +199.8 | +181.6 | 2321 |
| ALT | +189.3 | +647.3 | +458.0 | 3193 |
| ZRO | **−6.8** | +468.9 | **+475.7** | 2042 |
| TIA | +138.3 | +1450.8 | +1312.5 | **168** |
| ONDO | +74.1 | +1102.0 | +1027.9 | **168** |

Always-short is already positive for ~all tokens (these alts trended down 2024-26 →
beta short profits). Timing-alpha is **inconsistent**: negative for APT/ARB/SEI,
positive for JUP/ALT/ZRO, and the largest positives (TIA/ONDO) sit on tiny n=168
(likely a single unlock crash, not robust). Only **ZRO** shows clean timing alpha
(its always-short is ≈ flat, yet timed short is +469).

## Conclusion & next scrutiny

The headline +162bps survivor is **substantially alt-beta, not a clean unlock-timing
edge.** It is a candidate to scrutinise, not promotable. This is exactly the trap the
confound check exists to catch — the BH-FDR survival screen alone would have
mislabelled beta as edge.

Before any promotion the full gate must clear, beta-neutralised:
1. **Beta-neutralise:** formally test unlock-timing vs an always-short baseline
   (only ZRO currently survives that).
2. **Funding carry:** the cost model omits 7-day short funding — a real cost on a
   168h hold; add it.
3. **Per-symbol n≥100–200 + DSR/MinTRL + outlier-robust** (`trading edge-validation`)
   — the tiny-n spikes (TIA/ONDO) must drop out.
4. **Schedule-revision look-ahead:** DefiLlama serves the current schedule; verify
   against point-in-time snapshots for the surviving names.

The reusable unlock-pressure mechanism + the confound primitive ship; the verdict is
honest ("beta, not edge — except possibly ZRO, to isolate"). The whale-positioning
overlay (option 1 of the 1+2 synthesis) is the next data type to add around these
events if/when a beta-neutral unlock signal survives.
