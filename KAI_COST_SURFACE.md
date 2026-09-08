# KAI cost surface — 2026-09-08

Targeted code audit, base `074be1c6`. ACTIVE means a production call path exists,
not proof that a service is enabled or billed.

The first table was written without Pi access. The section "Measured on the live
Pi" below was added on 2026-09-08 from a read-only probe of `kai-pi5` and
**overrides the table wherever the two disagree** — three rows disagreed.
Invoices are still unread: **NEEDS BILLING DATA**.

| Surface | Classification | Evidence | Action |
|---|---|---|---|
| OpenAI | ACTIVE | `app/analysis/factory.py` primary chain; AI-controlled chat/STT/intent | VERIFY BILL; cheaper routine model only after quality comparison |
| Anthropic | **ACTIVE** (measured; table row was wrong) | Factory shadow chain; 2,650 calls / 30 days on the Pi | REDUCE continuous shadow if benefit unproven |
| Google Gemini | **CONFIGURED, ZERO TRAFFIC** (measured) | Key set, therefore in the primary chain — 0 calls / 30 days | KEEP as fallback; costs nothing while unused |
| xAI/Grok | **DISABLED** (measured) | Key set but `XAI_FALLBACK_ENABLED=false` — not in the chain, 0 calls / 30 days | Decide whether to keep paying for an unused key |
| X API | ACTIVE | `scripts/paper_trading_cron.sh`: every sixth tick, about hourly at 10-min cadence | REDUCE only after actual consumption/use check |
| CoinGecko | ACTIVE, **free tier** (measured) | `COINGECKO_API_KEY` is empty on the Pi; overview refresh enabled for 20 symbols | No bill to verify; preserve the dependency |
| YouTube | ACTIVE | Cron every twelfth tick (~2h), per configured channel; downstream analysis | VERIFY BILL / REDUCE unused sources |
| NewsData | OPTIONAL; scheduled path UNUSED | Manual CLI remains; Cron block already retired | ON-DEMAND; check whether subscription persists |
| Messari | OPTIONAL | `app/ingestion/messari/adapter.py`, metadata-configured API access | VERIFY BILL |
| RSS | ACTIVE | Server scheduler plus Cron run-all every fourth tick (~40min) | KEEP; inspect overlapping fetches |
| LiteLLM | OPTIONAL | `app/ai/runtime.py`, mode/route-gated transport | KEEP existing architecture; SHADOW adds calls |
| Local Intelligence | OPTIONAL | `scripts/llm_shadow_tick.py` requires enabled + shadow and an existing daily review | ON-DEMAND |
| Pi/domain/Cloudflare/backups | ACTIVE dependencies, tariff UNKNOWN | Existing deployment/services | KEEP / VERIFY BILL |
| Claude/ChatGPT/TradingView/signal subscriptions | UNKNOWN | API keys and code do not establish subscriptions | VERIFY BILL |

## Measured on the live Pi (2026-09-08, read-only)

Source: `/home/kai/current/.env` (key names and set/empty only, never values) and
`/home/kai/current/artifacts/llm_telemetry.jsonl` over a 30-day window. This is
the part of "NEEDS BILLING DATA" that could be closed without invoice access:
**consumption is now measured, prices are applied only where authoritative.**

### Which AI providers actually run

| Provider | Key on Pi | In chain | Calls / 30 d | Failures |
|---|---|---|---|---|
| OpenAI (`gpt-4o`) | set | primary | 4,723 | 0 |
| Anthropic (`claude-sonnet-4-6`) | set | shadow | 2,650 | 4 |
| Gemini | set | primary fallback | **0** | — |
| Grok | set | **not** (`XAI_FALLBACK_ENABLED=false`) | **0** | — |

Two keyed providers carry no traffic at all. Neither can be removed for savings
— an unused key costs nothing — but neither belongs in a spend estimate either.

### Token consumption, and why the estimate has a floor and a ceiling

`cost_usd` is null on **every** telemetry row, and token counts are present on
only a minority of rows (OpenAI 1,310 of 4,723; Anthropic 420 of 2,650). So two
numbers are reported, and the truth lies between them:

| Provider / model | Rows with tokens | Input tokens | Output tokens |
|---|---|---|---|
| OpenAI `gpt-4o` (both model labels) | 1,310 / 4,723 | 2,843,552 | 414,005 |
| Anthropic `claude-sonnet-4-6` | 420 / 2,650 | 1,434,130 | 361,098 |

**Anthropic, 30 days** — at the published `claude-sonnet-4-6` rate of
$3.00 / $15.00 per million tokens (input / output):

- measured floor (only the rows that carry tokens): **≈ $9.70**
- extrapolated to all 2,650 calls (×6.31): **≈ $61**

The floor is a fact; the extrapolation assumes the untokened rows resemble the
tokened ones, which is unverified and probably biased upward (rows without a
usage block are often failed or short attempts). **OpenAI is deliberately left
unpriced** — no authoritative `gpt-4o` rate was available to this audit, and a
guessed price would make the whole table untrustworthy.

Either way the earlier "≈ $10 / month" figure in the Kostenbild v0 is too low:
Anthropic alone reaches it on its measured floor, before OpenAI is counted.

### One priced lever, not applied

The Pi runs the shadow chain on `claude-sonnet-4-6` ($3.00 / $15.00 per MTok).
The current-generation model of the same class, `claude-sonnet-5`, is
$2.00 / $10.00 per MTok — **33 % cheaper on both input and output**. That is a
production behaviour change, so it was measured and written down here, not made.

### Still open after this measurement

- Invoices themselves (subscriptions, X API, YouTube, Messari, TradingView).
- Per-call cost: `cost_usd` is never written. Until the AI gateway records it,
  every spend figure stays an extrapolation. This is the single change that
  would turn this document into an accounting rather than an estimate.
- `SOURCE_DISCOVERY_ENABLED=false` on the Pi — the discovery path bills nothing.


## Potential waste

- `app/cli/main.py` and ingestion commands pass `create_shadow_provider()` into
  pipelines: primary plus independent shadow can charge twice. The pipeline
  already guards overlapping ensemble/shadow providers; preserve that guard.
- RSS schedules can overlap. `app/storage/document_ingest.py` deduplicates before
  analysis; overlapping schedules do not prove duplicate AI charges.
- `--top-n` limits displayed results, not processed documents or spend.
- Direct OpenAI/Anthropic/Gemini providers allow up to three Tenacity attempts
  for retryable errors; fallback and provider SDK retries may add attempts.
  LiteLLM retries are bounded by `app/ai/retry.py`. Count actual attempts,
  not only successful logical requests; no exact bill multiplier inferred.
- Telemetry contains outer wrapper and per-attempt rows: do not sum both.
- No configured provider is proven unused on the live system. No behavior-
  preserving provider deletion was established, so no provider/config removed.

## Immediate safe savings

Use the existing brief from persisted analyses without triggering a new ingest
or LLM run. Retain the existing NewsData scheduled-fetch removal. No new paid
probes, subscriptions, always-on shadow runs or price estimates for this audit.
Realized savings remain unmeasured; **NEEDS BILLING DATA**.

## Maximum five actions

1. Reconcile 30 days of bills against the measured call and token counts above.
2. Record ten real brief uses in `KAI_RESEARCH_TRIAL.md`.
3. Record `cost_usd` (or at minimum usage tokens) on every telemetry row —
   without it no later audit can do better than the floor/ceiling above.
4. Reduce only demonstrated waste; preserve fallback, pricing and safety paths.
5. Verify the reduction on the next bill, including operator time.
