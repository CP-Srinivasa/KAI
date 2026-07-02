# How a Small Quant Operation Discovers and Validates a Real Crypto Edge — and How KAI Can Do It Much Better

*Provenance: Web-grounded research report compiled 2026-06-25 from ~30+ distinct searches/fetches plus three parallel research agents (data/alpha sources, validation methodology, costs/execution alpha). Every non-trivial claim carries an inline source URL that appeared in search results or was fetched. Items labeled **[synthesis]** / **[opinion]** are analyst judgment, not sourced fact. All edge magnitudes from the literature are pre-cost upper bounds unless stated.*

---

## A. Executive Summary — the highest-leverage shifts

1. **Stop hunting directional price/TA signals; that search is statistically doomed and KAI's own data already proves it.** After just ~100 trials, a *useless* strategy (true Sharpe 0) shows an expected maximum annualized Sharpe of **~2.5 purely from selection luck** ([marti.ai](https://marti.ai/qfin/2018/05/30/deflated-sharpe-ratio.html)). The canonical TA study finds no simple trading rule survives data-snooping correction ([Sullivan-Timmermann-White, SSRN 65140](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=65140)); a 99-coin crypto test found TA didn't beat buy-and-hold *before* costs ([Emerald JDQS](https://www.emerald.com/jdqs/article/32/1/23/1214013/Do-technical-trading-rules-outperform-the-simple)); a Lucky-Factors+SPA test over 7,846 rules × 12 coins found only ~1–2 signals survive and **Bitcoin itself fails out-of-sample** ([Wiley ijfe.2863](https://onlinelibrary.wiley.com/doi/full/10.1002/ijfe.2863)). KAI's n=51, P=16.5% result is the *expected* outcome, not a fluke.

2. **Reframe the primary opportunity as EXECUTION ALPHA + COST CONTROL, not prediction.** KAI's −24 bps mean almost exactly matches an independent 22-strategy, 26,765-trade crypto paper test that lost **−0.078%/trade** on realistic fees ([stratproof](https://stratproof.com/blog/paper-trading-22-strategies-real-fees)). Real crypto round-trip cost is **~25–30 bps** for alt-taker churn, not the ~20 bps backtests assume. Taker→maker + passive fills + cost-gating + churn cuts can plausibly recover **~5–25 bps** ([RL-Exec: +2.7 to +23 bps over TWAP/VWAP on BTC, arXiv 2511.07434](https://arxiv.org/html/2511.07434v1); [OKX maker rebates](https://www.okx.com/en-us/learn/crypto-futures-fees-compared)). This is larger than most price signals' gross edge.

3. **Move to edges that fit a Pi-based, calendar/data shop — not latency.** The three best fits: **(a) token-unlock event shorts** (46/52 = 88.5% negative within 72h, [SSRN](https://papers.ssrn.com/sol3/Delivery.cfm/6632838.pdf?abstractid=6632838&mirid=1)), **(b) time-series momentum/trend on majors net-of-cost** (the most credible crypto factor, [Liu-Tsyvinski-Wu JF 2022](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131)), and **(c) live-collected on-chain/stablecoin flow features at hourly horizon** ([arXiv 2411.06327](https://arxiv.org/abs/2411.06327)). Avoid microstructure-HFT, market-making, and cross-exchange arb — all latency-gated and effectively closed to a Pi.

4. **Adopt a hard quantitative validation gate before *ever* claiming an edge** (Section C): Deflated Sharpe ≥ 0.95 accounting for # trials, PBO < 0.2 via CPCV, MinTRL met, MinBTL respected, Harvey-Liu multiple-testing haircut, t>3.0 for new factors, cost-net + buy-and-hold-controlled + purged walk-forward, n≥100–200. KAI's "canonical edge" pipeline is already directionally right — formalize it into this gate.

5. **Fix the silent backtest killers:** survivorship/delisting bias (>58% of tokens are dead; equal-weight alt backtests inflate returns up to **62% annualized**, [SSRN 4287573](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573)) and on-chain data-revision look-ahead (exchange-flow data is explicitly **not point-in-time**, [CryptoQuant](https://userguide.cryptoquant.com/api/btc-exchange-flows)). Only trust live-collected, timestamped data for edge tests.

---

## B. Detailed Findings

### Dimension 1 — Why naive signal/TA search fails (quantified)

**Multiple testing / data-snooping is the dominant failure mode.** The expected maximum Sharpe of N useless strategies grows with N as `E[max SR] ≈ √V[SR] · ((1−γ)·Z⁻¹[1−1/N] + γ·Z⁻¹[1−1/(Ne)])`, γ≈0.5772. Empirically, **100 trials → expected max annualized Sharpe ≈ 2.5 from pure luck** ([marti.ai](https://marti.ai/qfin/2018/05/30/deflated-sharpe-ratio.html)). Harvey-Liu-Zhu showed ~316 published equity factors used a too-lenient t>2.0 bar and proposed **t > 3.0** for new factors ([SSRN 2513152](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2513152); [NBER w20592](https://www.nber.org/papers/w20592)).

**TA specifically doesn't survive.** Sullivan-Timmermann-White (JF 1999): the best of a large rule universe gave no superior performance once data-snooping was corrected, and failed out-of-sample ([SSRN 65140](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=65140)). Crypto: 5 indicators × 99 coins → only 3/99 significant, **before costs**, which "would make the returns worse" ([Emerald JDQS](https://www.emerald.com/jdqs/article/32/1/23/1214013/Do-technical-trading-rules-outperform-the-simple)). Counter-evidence for honesty: TA *can* beat B&H net of costs in some coins during bubble regimes ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1042443122000816)) — so the edge is regime-dependent and fragile, not strictly zero.

**Regime non-stationarity.** Crypto volatility is "clustered, asymmetric, state dependent, with jumps and structural breaks"; events like FTX/regulatory shocks "fundamentally shift" the underlying process, breaking stationarity-assuming models ([arXiv multifractal vol](https://arxiv.org/pdf/2507.00575); [ScienceDirect DL vol](https://www.sciencedirect.com/science/article/abs/pii/S0378437122001704)). A strategy fit to one regime "often fails later" ([arXiv 2512.10913](https://arxiv.org/html/2512.10913v1)).

**Transaction-cost drag (KAI's core problem).** Real crypto round trip ≈ **25–30 bps** (maker 0.075%/side + taker 0.10%/side + L2 spread 0.05–0.15%/side), vs the flat 0.1%/side most backtests use ([stratproof](https://stratproof.com/blog/paper-trading-22-strategies-real-fees)). Rule of thumb: **avg profit/trade should be 2–3× per-trade cost**; a 150 bps gross edge at 200% turnover with 75 bps cost goes net-negative ([macrosynergy](https://macrosynergy.com/research/transaction-costs-and-portfolio-strategies/)).

**Survivorship/look-ahead bias.** >14,000 of ~24,000 tokens are "dead" (>58%) ([stratbase](https://stratbase.ai/en/blog/survivorship-bias-crypto)); ignoring delistings inflates performance **0.93% (value-weighted) up to 62.19% (equal-weighted) annualized** ([SSRN 4287573](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573)). On-chain exchange-flow data is **not point-in-time** — clusters are revised weekly, so naive backtests are look-ahead-biased ([CryptoQuant](https://userguide.cryptoquant.com/api/btc-exchange-flows)). Mitigation: point-in-time universe construction, including later-delisted symbols ([coinapi.io](https://www.coinapi.io/blog/how-to-eliminate-survivorship-bias-in-crypto-backtesting)).

**Low signal-to-noise / minimum n.** Sample Sharpe SE scales 1/√n. Practical guidance: ~30 trades = basic validity, **100 = confidence, 500+ = robust**; with 100 trades a Sharpe of 1.0 can be significant, but at 20 trades even Sharpe 2.0 may not be ([Medium synthesis](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05)). **KAI's n=51 is in the "thin/noise-dominated" band.**

### Dimension 2 — Rigorous edge-discovery & validation methodology

**López de Prado toolkit (formulas KAI can implement):**

- **Probabilistic Sharpe Ratio (PSR):** `PSR(c) = Φ[ (SR−c)·√(T−1) / √(1 − γ₃·SR + ((γ₄−1)/4)·SR²) ]`, where SR is per-observation Sharpe, c is benchmark (often 0), T = observations, γ₃ = skewness, γ₄ = **non-excess** kurtosis (Normal → 3). Negative skew / fat tails lower PSR. Accept PSR(0) ≥ 0.95 ([Portfolio Optimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/); [SSRN 1821643](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1821643); [Wikipedia DSR](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)).
- **Minimum Track Record Length (MinTRL):** `MinTRL(c) = (1 − γ₃·SR + ((γ₄−1)/4)·SR²) · ( z_{1−α} / (SR−c) )²` — the observations needed before a Sharpe is credibly non-zero; negative skew/fat tails inflate it ([Portfolio Optimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/)).
- **Deflated Sharpe Ratio (DSR):** PSR with benchmark c replaced by SR₀, the expected max Sharpe under the null given N trials: `SR₀ = √V[SR_n] · ((1−γ)·Φ⁻¹[1−1/N] + γ·Φ⁻¹[1−1/(Ne)])`, γ≈0.5772. **Require DSR ≥ 0.95.** Count **every config ever tried** in N; if trials are correlated, use *effective* N via PCA on trial returns ([SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551); [Wikipedia DSR](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio); [davidhbailey.com](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)).
- **Minimum Backtest Length (MinBTL):** `MinBTL (years) < 2·ln(N) / E[max_N]²`. Concretely: **with 5 years of data, testing >~45 independent configs almost guarantees an in-sample annualized Sharpe ≈ 1 whose true OOS Sharpe = 0** ([Pseudo-Math SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659); [scholarworks](https://scholarworks.wmich.edu/math_pubs/40/); [AMS PDF](https://www.ams.org/notices/201405/rnoti-p458.pdf)). Necessary, not sufficient.
- **Probability of Backtest Overfitting (PBO)** via **Combinatorially Symmetric Cross-Validation (CSCV)** = probability the in-sample-best config underperforms the OOS median. Require **PBO < 0.5, prefer < 0.2**; PBO > 0.5 means selection is worse than a coin flip at picking OOS winners; robust strategies can reach PBO < 1% ([Bailey et al. SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253); [scholarworks](https://scholarworks.wmich.edu/math_pubs/42/); [EmergentMind](https://www.emergentmind.com/topics/cscv-pbo-diagnostic)).
- **Combinatorial Purged Cross-Validation (CPCV):** split data into N groups, hold out k as test (purged + embargoed); distinct backtest paths = `φ(N,k) = (k/N)·C(N,k)` (e.g. N=6,k=2 → 5 paths). **Purge** train rows whose labels overlap test labels; **embargo** ~1% of rows after each test fold. CPCV yields a *distribution* of Sharpes (feeds DSR/PBO) and beats single-path walk-forward — but breaks within-path chronology, so keep one **sacred chronological forward/walk-forward holdout** as the final gate ([Wikipedia Purged CV](https://en.wikipedia.org/wiki/Purged_cross-validation); [Towards AI](https://towardsai.net/p/l/the-combinatorial-purged-cross-validation-method)).
- **Triple-barrier labeling** (profit-take / stop / time barrier, volatility-scaled) + **meta-labeling** (primary model picks *side* for high recall; secondary model decides *whether/how big* to bet, raising precision). Raises Sharpe by suppressing false positives even if raw return dips ([Hudson & Thames](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/); [Wikipedia](https://en.wikipedia.org/wiki/Meta-Labeling)). **Caveat: meta-labeling can't create direction from a directionless primary** — if KAI's generator is ~coin-flip, it mostly tells you *not to trade* (itself cost-saving) ([QuantConnect](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/)).
- **Feature importance done right:** MDI (in-sample, dilutes correlated features) and MDA (permutation, fooled by substitution) both mislead; use **Single Feature Importance** and **Clustered Feature Importance** (permute correlated blocks) ([mlfinlab](https://www.mlfinlab.com/en/latest/feature_importance/afm.html); [LdP CFI SSRN 3517595](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3517595)).

**Multiple-testing corrections.** Harvey-Liu "Backtesting" gives a **nonlinear Sharpe haircut** (a flat 50% haircut is wrong): SR→t (`t=SR·√T`) → p → adjust for M tests → invert to haircut Sharpe. Worked example: SR=0.75, T=240, M=200 → **~56–60% haircut**; marginal Sharpes cut ~100%, top Sharpes only modestly ([Harvey-Liu SSRN 2345489](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489); [OpenSourceQuant](https://opensourcequant.wordpress.com/2016/11/17/r-view-backtesting-harvey-liu-2015/)). Methods: **Bonferroni** (`p_adj = min(M·p,1)`, FWER, strict), **Holm** (sequential FWER), **Benjamini-Hochberg/BHY** (FDR: sort p ascending, reject the largest i with `p_(i) ≤ (i/m)·q`) ([Statistics How To](https://www.statisticshowto.com/benjamini-hochberg-procedure/)). Data-snooping tests over a full strategy universe: **White's Reality Check** and the more powerful, recentered **Hansen SPA** (use SPA as default; RC can be gamed by stuffing junk rules) ([White](https://www.researchgate.net/publication/4896389_A_Reality_Check_for_Data_Snooping); [Hansen SPA](https://cdr.lib.unc.edu/downloads/zp38wf793); Python [arch.bootstrap.SPA](https://arch.readthedocs.io/en/latest/multiple-comparison/generated/arch.bootstrap.SPA.html)).

### Dimension 3 — Where real crypto alpha lives (small-shop lens)

| Source | Carries alpha? | Accessible to a Pi shop? | Horizon | Binding constraint |
|---|---|---|---|---|
| Microstructure / OBI / OFI | Yes (tiny, fast) | **No** | ms–s | Latency: corr(latency, PnL) ≈ **−0.775** ([arXiv 2507.22712](https://arxiv.org/html/2507.22712v1)) |
| Funding / basis carry | **Yes** | Yes, *with capital* | days–wks | Real capital + tail risk; basis compressed **25%→5%** ([CME](https://www.cmegroup.com/openmarkets/equity-index/2025/Spot-ETFs-Give-Rise-to-Crypto-Basis-Trading.html)) |
| Cross-exchange / triangular arb | Mostly gone | No | ms | Latency, withdrawal fees ([ScienceDirect](https://www.sciencedirect.com/science/article/pii/S154461232401537X)) |
| On-chain / stablecoin flows | Modest | **Yes** | hrs–days | Data-revision look-ahead ([arXiv 2411.06327](https://arxiv.org/abs/2411.06327)) |
| Liquidation heatmaps | Weak/reflexive | Yes, low edge | min–hrs | Front-run / self-defeating ([CoinGlass](https://www.coinglass.com/learn/how-to-use-liqmap-to-assist-trading-en)) |
| Options VRP / skew | Yes (short-vol) | No (capital/margin) | days–wks | Fat left tails; VRP ~3–4× TradFi ([Quantpedia](https://quantpedia.com/strategies/volatility-risk-premium-effect)) |
| **Event: unlocks / listings** | **Yes** | **Yes (calendar)** | hrs–days | Borrow/slippage, crowding ([SSRN unlocks](https://papers.ssrn.com/sol3/Delivery.cfm/6632838.pdf?abstractid=6632838&mirid=1)) |
| **TS-momentum (majors)** | **Yes (best factor)** | **Yes (OHLCV)** | days | Costs, decay ([Liu-Tsyvinski-Wu](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131)) |
| Cross-sectional factors | Real-but-fragile | Yes data-wise | days | Data-snooping; mostly a liquidity proxy ([crypto factor zoo](https://www.sciencedirect.com/science/article/abs/pii/S1057521926000645)) |
| Stat-arb / pairs | Marginal | Yes data-wise | hrs–days | Costs, unstable cointegration |
| Market-making | Yes (spread) | No | ms–s | Latency + adverse selection ([Avellaneda-Stoikov, hummingbot](https://hummingbot.org/blog/technical-deep-dive-into-the-avellaneda--stoikov-strategy/)) |

Specifics: **TS-momentum > cross-sectional momentum** in crypto (~31.96%/yr, better risk-adjusted), but momentum profits shrink sharply once costs + daily moves are modeled ([Han-Kang-Ryu SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)). The Liu-Tsyvinski-Wu 3-factor model (market, size, momentum) captures the crypto cross-section ([JF 2022, SSRN 3379131](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131)). Exchange listings: **+5.7% listing day, +9.2% over 3 days**, but reverses (half of 2024 listings later lost >45%), effect fading late 2025 ([BRL working paper](https://www.blockchainresearchlab.org/wp-content/uploads/2019/10/Exploring-Market-Reactions-to-Exchange-Listings-of-Cryptocurrencies-BRL-working-paper3.pdf); [Coindesk](https://www.coindesk.com/markets/2023/01/06/binance-effect-means-41-price-spike-for-newly-listed-tokens)). On-chain: USDT net inflows predict higher BTC/ETH returns; ETH inflows negatively predict ETH ([arXiv 2411.06327](https://arxiv.org/abs/2411.06327)); but whale-alert signal-to-noise is poor (internal transfers, custody rotations) ([blofin](https://blofin.com/en/academy/education/whale-watching-on-chain)). Funding-rate reversion (KAI's own shadow finding) is real only at multi-σ extremes over multi-day horizons — *not* KAI's seconds-to-hours window ([yellow.com](https://yellow.com/learn/how-to-read-funding-rates-crypto-reversals)). Liquidation cascades are mechanically real but the *predictive heatmap* is widely watched and front-run, so often self-defeating ([CoinGlass](https://www.coinglass.com/learn/how-to-use-liqmap-to-assist-trading-en)). Cash-and-carry basis has historically reached Sharpe ~4.84 but funding can flip income→cost on a single cascade ([buildix](https://www.buildix.trade/blog/cash-and-carry-crypto-delta-neutral-funding-rate-strategy-2026)).

### Dimension 4 — Data sources that carry signal (cheap/free tiers)

**Use now, free, signal-bearing:**
- **Native exchange L2 WebSockets (Binance/Bybit/OKX)** — best *free* raw microstructure source; Binance spot WS allows 1024 streams/conn ([Binance docs](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)). A Pi can *collect* but not win the sub-second race — use for point-in-time features on hour-horizon models.
- **Coinalyze** — free API (40 calls/min): funding, OI, liquidations, long/short, basis across venues ([api.coinalyze.net](https://api.coinalyze.net/v1/doc/)). Best free derivatives API.
- **Dune Analytics** — free 2,500 query credits/mo + API (DEX flows, stablecoin mints/burns); Plus $399/mo ([dune.com/pricing](https://dune.com/pricing)).
- **The Graph** — 100k free queries/mo, then $2/100k ([thegraph.com](https://thegraph.com/studio-pricing/)); plus raw JSON-RPC.
- **Messari / Tokenomist** — free token-unlock calendars (the event edge's fuel); Messari free tier 20 req/min ([messari.io/api](https://messari.io/api); [tokenomist.ai](https://tokenomist.ai/)).
- **Tardis.dev OSS collector** — free real-time normalized data direct from exchange WS (self-build history instead of paying; full history is $350–6,000/mo) ([github tardis-node](https://github.com/tardis-dev/tardis-node); [tardis.dev](https://tardis.dev/)).
- **Velo Data** — free aggregated derivatives data favored by traders; exact free-API limits unconfirmed, verify directly ([velodata.app](https://velodata.app/)).

**Cheap upgrades if a signal proves out:** CryptoQuant API ($99/mo — but *snapshot live* to avoid revision bias; API token needs Professional+, [pricing](https://cryptoquant.com/pricing)), CoinGlass Hobbyist ($29/mo) only for endpoints Coinalyze lacks ([coinglass.com/pricing](https://www.coinglass.com/pricing)), Glassnode Advanced ($49/mo) for research context ([studio.glassnode.com/pricing](https://studio.glassnode.com/pricing)), Laevitas options via x402 pay-per-request ([docs.laevitas.ch](https://docs.laevitas.ch/options/analytic)).
**Skip for budget/fit:** Kaiko (from $1,000/mo, [datarade](https://datarade.ai/data-providers/kaiko-data/profile)), Amberdata (enterprise, [amberdata.io/pricing](https://www.amberdata.io/pricing)), options data until options is in scope.
**"Nice dashboard, not alpha":** Glassnode aggregates, CoinGlass liquidation heatmap, most lagging on-chain composites — regime context only, dangerous as triggers.

### Dimension 5 — Quantitative approaches beyond TA, and the cost-recovery math

**Execution alpha is the strongest evidence-backed lever for KAI [synthesis].** RL genuinely helps *execution* (not direction): RL-Exec beat TWAP/VWAP on BTC-USD by **+2.7 bps @30min, +7.6 @60min, +23 @120min** with FDR-corrected significance ([arXiv 2511.07434](https://arxiv.org/html/2511.07434v1)); Hendricks-Wilcox improved implementation shortfall **~10.3%** over Almgren-Chriss ([arXiv 2411.06389](https://arxiv.org/pdf/2411.06389)). Conversely, RL for *directional* prediction is poorly supported — "lacks substantial evidence of practical efficacy," degrades live due to non-stationarity ([arXiv 2512.10913](https://arxiv.org/html/2512.10913v1)).

The four cost levers and their evidence:
- **(a) Taker→maker:** ~3–7 bps round-trip fee saved (Binance perp 5→2 bps; OKX top tier +1.5→**−0.5 rebate**) ([OKX](https://www.okx.com/en-us/learn/crypto-futures-fees-compared); fee schedules: [Binance via cryptopotato](https://cryptopotato.com/binance-fees/), [Bybit via bitdegree](https://www.bitdegree.org/crypto/tutorials/bybit-fees)). **Catch:** maker fills are adversely selected — fill-probability correlates *negatively* with post-fill return ([Market Maker's Dilemma, arXiv 2502.18625](https://arxiv.org/html/2502.18625v2)), so you don't keep the full saving.
- **(b) Passive fills / timing:** the documented RL-execution win above.
- **(c) Cost-gating (trade only when spread/vol low):** spread is the dominant implicit cost and varies from ~2.7 bps on BTC to >10% on illiquid alts ([arXiv 2201.01392](https://arxiv.org/pdf/2201.01392); [Binance Academy](https://academy.binance.com/en/articles/bid-ask-spread-and-slippage-explained)) — refusing wide-spread alt windows removes the worst fills.
- **(d) Churn reduction:** turnover multiplies every cost; meta-labeling is the principled churn-cutter ([macrosynergy](https://macrosynergy.com/research/transaction-costs-and-portfolio-strategies/)).

**Net realistic recovery [opinion]: ~5–10 bps (conservative, after adverse selection) up to ~10–25 bps (best case).** This can move −24 bps toward break-even **only if the generator's *gross* (pre-cost) edge is ≥ 0**. **Falsifiable test:** measure gross mean separately. If gross ≥ 0 → execution alpha is high-EV. If gross < 0 → execution only slows the bleed; kill/replace signals or trade far less. Market impact follows the **square-root law** (confirmed on >1M BTC metaorders, exponent ≈0.5, [arXiv 1412.4503](https://arxiv.org/abs/1412.4503)) — but at KAI's tiny sizes impact is negligible; spread+fees+churn dominate. Almgren-Chriss splits cost into temporary + permanent components ([Gatheral](http://mathfinance.sns.it/wp-content/uploads/2010/12/Gatheral_Optim_Exec.pdf)).

Regime detection (HMM/change-point) is useful *if* fed stationary inputs (returns, not prices) and retrained on distribution shift ([QuantStart](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/); [QuantifiedStrategies](https://www.quantifiedstrategies.com/hidden-markov-model-market-regimes-how-hmm-detects-market-regimes-in-trading-strategies/)). Bayesian model averaging / ensembles reduce overfitting via diversity ([BMA FX +15%](https://www.researchgate.net/publication/23775647_Bayesian_Model_Averaging_and_Exchange_Rate_Forecasts)).

### Dimension 6 — Public post-mortems / replication crisis

- **Quantopian "All That Glitters Is Not Gold"** (888 algos, ≥6mo OOS): in-sample Sharpe predicts OOS performance essentially not at all (**R² < 0.025**); the more backtesting a user did, the larger the IS–OOS gap (direct overfitting evidence). Only volatility, max drawdown, and portfolio-construction features predicted ([SSRN 2745220](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220); [Quantpedia](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/)).
- **McLean & Pontiff (JF 2016):** 97 predictors → returns **26% lower out-of-sample, 58% lower post-publication** ([SSRN 2156623](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623); [Wiley](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365)).
- **Hou-Xue-Zhang "Replicating Anomalies"** (452 anomalies): **65% fail t>1.96; ~82% fail at the multiple-testing bar t=2.78** ([SSRN 3275496](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3275496); [NBER w23394](https://www.nber.org/system/files/working_papers/w23394.pdf)).
- **Bailey-Borwein-López de Prado-Zhu "Pseudo-Mathematics"** (Notices of the AMS, 2014): minimum backtest length must grow with the number of configurations tried; most analysts never report trials, so overfitting is undetectable ([SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659); [AMS PDF](https://www.ams.org/notices/201405/rnoti-p458.pdf)).
- **Crypto-specific, adversarially verified:** a Lucky-Factors + SPA test over **7,846 technical rules + fundamentals across 12 coins** found **only ~1–2 signals survive (short-term MA-ratio and the Hashrate Index), and Bitcoin itself shows weak/no OOS profitability** ([Wiley ijfe.2863](https://onlinelibrary.wiley.com/doi/full/10.1002/ijfe.2863); [open PDF](https://eprints.gla.ac.uk/302711/1/302711.pdf)). After proper data-snooping correction, almost nothing survives, and what does is concentrated and fragile — fully consistent with KAI's own 6/6 net-negative TA finding.

---

## C. Quantitative Validation Gate (adopt before claiming ANY edge)

A strategy/signal may be promoted **only if it passes ALL of these.** Implement as a Python gate in KAI's pipeline.

1. **Pre-register & count every trial.** Log every config/feature/threshold ever tried (including discarded). This count `N` feeds DSR deflation. ([Bailey-LdP](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551))
2. **Minimum Backtest Length:** require backtest span ≥ `MinBTL = 2·ln(N) / E[max_N]²` years. Rule of thumb: with ~5 years of data, **do not test more than ~45 independent configs** or a spurious in-sample Sharpe ≈ 1 (true OOS 0) is near-guaranteed. ([SSRN 2308659](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659))
3. **Cost-net + buy-and-hold-controlled.** Returns net of realistic round-trip cost (**model 25–30 bps alt-taker, ≥10–15 bps majors-maker**), and the edge must beat passive B&H on the same universe. ([stratproof](https://stratproof.com/blog/paper-trading-22-strategies-real-fees))
4. **Point-in-time, survivorship-safe data.** Include later-delisted symbols; use only data available at time *t*; for on-chain use only **live-collected timestamped snapshots**, never vendor "history." ([SSRN 4287573](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573); [CryptoQuant](https://userguide.cryptoquant.com/api/btc-exchange-flows))
5. **Purged + embargoed CPCV.** Purge label-overlapping rows; ~1% embargo; report the **distribution** of OOS results across `φ(N,k)=(k/N)·C(N,k)` paths, not a single walk-forward. ([Wikipedia Purged CV](https://en.wikipedia.org/wiki/Purged_cross-validation))
6. **PBO < 0.2** (hard reject at ≥ 0.5) via CSCV — the in-sample-best config must not underperform the OOS median. ([SSRN 2326253](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253); [EmergentMind](https://www.emergentmind.com/topics/cscv-pbo-diagnostic))
7. **Deflated Sharpe Ratio ≥ 0.95**, computed with the *actual N trials*, skew, kurtosis, sample length; report `SR₀`. Use *effective* N (PCA) if trials are correlated. ([SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551))
8. **MinTRL satisfied** — at least `(1 − γ₃·SR + ((γ₄−1)/4)·SR²)·(z_{1−α}/(SR−c))²` observations for the claimed Sharpe at 95%. (KAI's n=51 likely fails for any modest Sharpe.) ([Portfolio Optimizer](https://portfoliooptimizer.io/blog/the-probabilistic-sharpe-ratio-bias-adjustment-confidence-intervals-hypothesis-testing-and-minimum-track-record-length/))
9. **Multiple-testing haircut** — apply the Harvey-Liu nonlinear haircut (NOT a flat 50%). Use **BHY/FDR** when screening many candidate signals (tolerate q≈0.10); use **Bonferroni/Holm (FWER)** for the irreversible go-live capital decision (near-zero tolerance for any false edge). ([Harvey-Liu SSRN 2345489](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489); [Statistics How To](https://www.statisticshowto.com/benjamini-hochberg-procedure/))
10. **New-factor t > 3.0** (not 2.0), per Harvey-Liu-Zhu; rises to ~3.4 for recent vintages. ([SSRN 2513152](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2513152))
11. **Reality Check / SPA** when claiming the best rule of a searched universe beats a benchmark; prefer Hansen SPA. ([Hansen SPA](https://cdr.lib.unc.edu/downloads/zp38wf793))
12. **Outlier-robustness** — re-run dropping the single best trade/day; P must not collapse. (KAI already does this; keep it.)
13. **Sacred chronological holdout** — a final untouched forward/paper window the optimizer never saw; expect ~26–58% decay vs in-sample and require it *still* positive. ([McLean-Pontiff](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2156623))
14. **Minimum n** — treat n<100 closed trades as "suggestive only"; **require ≥100–200** decisive, epoch-clean trades before any "edge" label, 500+ for "robust." ([Medium synthesis](https://medium.com/@trading.dude/how-many-trades-are-enough-a-guide-to-statistical-significance-in-backtesting-093c2eac6f05))

---

## D. Prioritized Roadmap (P0/P1/P2)

### P0 — Execution alpha + cost control (highest EV, lowest data cost, fits constraints)
- **Hypothesis:** KAI's generator is ~coin-flip gross; the −24 bps is cost/churn, recoverable.
- **First action (this week):** instrument **gross (pre-cost) per-trade mean** separately from costs. This single number decides everything below.
- **Build:** (1) taker→maker via post-only limit entries; (2) a **cost-gate** blocking trades when spread/vol exceed a threshold (especially alts); (3) **meta-labeling** to suppress low-confidence trades and cut churn; (4) optionally an RL/heuristic passive-execution layer.
- **Data:** free exchange L2 WS + own fills.
- **Expected gross edge / capacity / decay:** recovery ~5–25 bps round-trip; capacity large at KAI's size; decay low (execution efficiency is structural, not arbitraged) but adverse selection caps the gain ([arXiv 2502.18625](https://arxiv.org/html/2502.18625v2)).
- **Validation gate:** cost-net A/B (maker vs taker, gated vs ungated) over ≥100 trades; DSR on the *post-execution* return series.
- **Effort:** moderate (execution + labeling layer).
- **Caveat:** if gross < 0, this only slows the bleed — then trade far less, don't optimize entries.

### P1 — Event-driven token-unlock calendar shorts
- **Hypothesis:** large cliff unlocks (>1% of circulating, >2.4× ADV) exert negative 72h pressure ([SSRN](https://papers.ssrn.com/sol3/Delivery.cfm/6632838.pdf?abstractid=6632838&mirid=1)); short into / fade the unlock when demand is weak.
- **Data:** free unlock calendars (Messari/Tokenomist).
- **Expected gross edge / capacity / decay:** large per-event (88.5% negative-72h headline) but conditional; net much smaller after borrow/slippage; capacity small per name (illiquid tokens); decay medium (crowding as it gets known).
- **Validation gate:** event-study with bootstrap CI, cost-net incl. borrow, conditional on regime; require ≥30–50 events.
- **Effort:** low–moderate (calendar ingest + event logic). Paper-first, no latency needed.

### P1 — Time-series momentum / trend on majors, net-of-cost
- **Hypothesis:** TS-momentum is the most credible crypto factor; current market return predicts up to ~8 weeks ([Liu-Tsyvinski-Wu](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3379131); [Han-Kang-Ryu](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)).
- **Data:** OHLCV only (free).
- **Expected gross edge / capacity / decay:** real but cost-sensitive (~31%/yr gross in studies, much less net); capacity good on majors; decay medium-slow.
- **Validation gate:** CPCV + DSR≥0.95, PBO<0.2, cost-net, B&H-controlled, MinTRL met; treat any cross-sectional "factor survivor" with heavy skepticism (liquidity-proxy risk).
- **Effort:** low.

### P2 — Live-collected on-chain / stablecoin flow features (hourly)
- **Hypothesis:** USDT net inflows predict higher BTC/ETH returns; ETH inflows negatively predict ETH ([arXiv 2411.06327](https://arxiv.org/abs/2411.06327)).
- **Data:** Dune/The Graph/RPC + Coinalyze, **collected live and timestamped** (never vendor history).
- **Expected gross edge / capacity / decay:** modest, hourly–daily; capacity good (majors); decay medium.
- **Validation gate:** as P1, with strict point-in-time discipline (this is the bias trap). Use as a *feature/filter*, not a standalone trigger.
- **Effort:** moderate (live collectors + storage).

### P2 — Microstructure features as slow-horizon inputs (NOT HFT)
- **Hypothesis:** OBI/OFI/micro-price carry information untradeable at ms but usable as **features on minute–hour models or as the cost-gate's vol proxy**.
- **Data:** free L2 WS.
- **Expected gross edge / capacity / decay:** small as a feature; do not attempt standalone microstructure trading — the −0.775 latency curve closes it ([arXiv 2507.22712](https://arxiv.org/html/2507.22712v1)).
- **Validation gate:** same gate. **Effort:** moderate.

### Future / gated (separate real-capital workstream)
- **Funding-rate / basis carry** — the strongest *real* small-shop edge but mid-single-digit % APY now (compressed from 25%→5%, [CME](https://www.cmegroup.com/openmarkets/equity-index/2025/Spot-ETFs-Give-Rise-to-Crypto-Basis-Trading.html)), with fat-tail blowup risk; it's a deploy-capital strategy, not paper/seconds-to-hours. Revisit post-edge, with strict risk limits.

---

## E. What NOT to keep doing (dead ends)

1. **Naive TA/price-signal hypothesis expansion.** Statistically doomed ([Sullivan-Timmermann-White](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=65140); KAI's own 6/6 net-negative rules). Each new untracked trial *raises* the spurious-Sharpe baseline.
2. **Claiming edge on thin n.** n=51 is noise-dominated; "P=16.5%, not outlier-robust" is the correct read — don't re-slice hoping for a positive cohort (that's p-hacking).
3. **Treating funding-rate alignment or OI as a directional edge on seconds-to-hours.** KAI's shadow tests already showed sub-cost/anti-predictive; literature agrees funding reversion lives only at multi-σ extremes over multi-day horizons ([yellow.com](https://yellow.com/learn/how-to-read-funding-rates-crypto-reversals)).
4. **Liquidation-heatmap triggers.** Reflexive and front-run — context feature at best, no proven net edge ([CoinGlass](https://www.coinglass.com/learn/how-to-use-liqmap-to-assist-trading-en)).
5. **Microstructure-HFT, market-making, cross-exchange/triangular arb.** All latency-gated; a Pi over public internet is structurally on the wrong side ([arXiv 2507.22712](https://arxiv.org/html/2507.22712v1); [ScienceDirect tri-arb](https://www.sciencedirect.com/science/article/pii/S154461232401537X)).
6. **Backtesting on vendor on-chain "history" or survivorship-pruned universes.** Both inject look-ahead/survivorship inflation (up to 62% annualized, [SSRN 4287573](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4287573)).
7. **Believing in-sample Sharpe.** Quantopian: R² < 0.025 vs OOS ([SSRN 2745220](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2745220)). Until a signal clears the Section-C gate, it is a hypothesis, not an edge.
8. **Expecting meta-labeling/ML to manufacture direction.** It only sizes/filters an existing edge; on a directionless generator its main value is telling you to *not trade* (cost saving), not to predict ([QuantConnect](https://www.quantconnect.com/forum/discussion/14706/why-meta-labeling-is-not-a-silver-bullet/)).

---

*Honesty notes: decay and crowding are pervasive (basis 25%→5%, listing effect fading, cross-exchange arb gone, cross-sectional momentum weak); every headline magnitude is pre-cost and likely an upper bound. The silent killers are data-revision look-ahead (on-chain) and survivorship bias (dead coins). The single strongest, evidence-backed recommendation for KAI's constraints: measure gross (pre-cost) per-trade edge in isolation, then pursue execution-alpha + cost-gating + churn-reduction as the primary path — it can recover ~5–25 bps and is larger than most price signals' gross edge, but only helps if gross edge is ≥ 0.*
