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
`/home/kai/current/artifacts/llm_telemetry.jsonl`, counted over the **v2 window**
(2026-09-02 20:11 onwards, 5.62 days) and scaled to 30 days — see the next
section for why a raw 30-day count is wrong here. This is the part of
"NEEDS BILLING DATA" that could be closed without invoice access:
**consumption is now measured, prices are applied only where authoritative.**

### Which AI providers actually run

| Provider | Key on Pi | In chain | Calls / 30 d (scaled) | Failures |
|---|---|---|---|---|
| OpenAI (`gpt-4o`) | set | primary | ~3,560 | 0 |
| Anthropic (`claude-sonnet-4-6`) | set | shadow | ~2,300 | 4 |
| Gemini | set | primary fallback | **0** | — |
| Grok | set | **not** (`XAI_FALLBACK_ENABLED=false`) | **0** | — |

Two keyed providers carry no traffic at all. Neither can be removed for savings
— an unused key costs nothing — but neither belongs in a spend estimate either.

### Token consumption — and the mistake that produced the first version of this section

The first version of this section counted a 30-day window and concluded from
"only a minority of rows carry tokens" that telemetry was broken. That was
wrong, and the error is worth keeping visible because the wrong answer read
exactly like a measurement:

    schema v1: 13,074 rows, 2026-07-11 .. 2026-09-02 19:33  (8 fields, NO usage)
    schema v2:  1,812 rows, 2026-09-02 20:11 .. now         (usage complete)

**The schema change sits in the middle of the 30-day window.** The "missing"
tokens are 24 days of legacy rows, not a defect. Within v2, essentially every
row carries usage: Anthropic 428/432, OpenAI 667/667.

**How to count this stream correctly:**

1. Filter to `schema_version == "v2"`.
2. Take exactly ONE chain level — `chain_position == -1` (the outer row) OR
   `>= 0` (the per-attempt rows), never both. They describe the same calls:
   OpenAI shows 1,442,260 vs 1,499,219 input tokens for the same traffic.
3. Restrict `provider` to {openai, anthropic, gemini, grok} — the stream also
   carries source names such as CNBC or BeInCrypto in that field.
4. Only then scale to a month.

Measured over the v2 window (5.62 days, factor 5.34 to 30 days):

| Provider / model | Calls / 30 d | Input / 30 d | Output / 30 d | Cost / 30 d |
|---|---|---|---|---|
| Anthropic `claude-sonnet-4-6` | ~2,300 | 7.82 M | 1.97 M | **≈ $53** at $3 / $15 per MTok |
| OpenAI `gpt-4o` | ~3,560 | 7.71 M | 1.12 M | deliberately unpriced |

The operator's invoice for 2026-08 reads **$47.67** for the Anthropic API — the
estimate lands 11 % high, which is what a five-day sample scaled to a month
should do. OpenAI stays unpriced: no authoritative `gpt-4o` rate was available
to this audit, and a guessed price would make the whole table untrustworthy.

`cost_usd` is still null on every row. Recording it (or at least usage tokens
on every path) is what would turn this document from an estimate into an
accounting.

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

1. Reconcile the next invoice against the scaled counts above — the Anthropic
   estimate currently lands 11 % high against the 2026-08 bill.
2. Record ten real brief uses in `KAI_RESEARCH_TRIAL.md`.
3. Record `cost_usd` on every telemetry row — usage tokens are already there
   in v2; the price is the missing half, and without it every figure here
   stays an extrapolation from a five-day sample.
4. Reduce only demonstrated waste; preserve fallback, pricing and safety paths.
5. Verify the reduction on the next bill, including operator time.
