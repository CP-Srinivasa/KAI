# KAI cost surface — 2026-09-08

Targeted code audit, base `074be1c6`. ACTIVE means a production call path exists,
not proof that a service is enabled or billed. No Pi access or secrets read.
Actual usage, subscriptions and spending: **NEEDS BILLING DATA**.

| Surface | Classification | Evidence | Action |
|---|---|---|---|
| OpenAI | ACTIVE | `app/analysis/factory.py` primary chain; AI-controlled chat/STT/intent | VERIFY BILL; cheaper routine model only after quality comparison |
| Anthropic | OPTIONAL | Factory shadow chain, supplied by CLI pipelines when keyed | REDUCE continuous shadow if benefit unproven |
| Google Gemini | OPTIONAL | Primary fallback; shadow fallback without Anthropic | KEEP necessary fallback, avoid duplicate full analysis |
| xAI/Grok | OPTIONAL | Factory requires key AND fallback flag | ON-DEMAND |
| X API | ACTIVE | `scripts/paper_trading_cron.sh`: every sixth tick, about hourly at 10-min cadence | REDUCE only after actual consumption/use check |
| CoinGecko | ACTIVE | `app/market_data/coingecko_adapter.py`: free/pro endpoint and market consumers | VERIFY BILL; preserve critical market-data dependency |
| YouTube | ACTIVE | Cron every twelfth tick (~2h), per configured channel; downstream analysis | VERIFY BILL / REDUCE unused sources |
| NewsData | OPTIONAL; scheduled path UNUSED | Manual CLI remains; Cron block already retired | ON-DEMAND; check whether subscription persists |
| Messari | OPTIONAL | `app/ingestion/messari/adapter.py`, metadata-configured API access | VERIFY BILL |
| RSS | ACTIVE | Server scheduler plus Cron run-all every fourth tick (~40min) | KEEP; inspect overlapping fetches |
| LiteLLM | OPTIONAL | `app/ai/runtime.py`, mode/route-gated transport | KEEP existing architecture; SHADOW adds calls |
| Local Intelligence | OPTIONAL | `scripts/llm_shadow_tick.py` requires enabled + shadow and an existing daily review | ON-DEMAND |
| Pi/domain/Cloudflare/backups | ACTIVE dependencies, tariff UNKNOWN | Existing deployment/services | KEEP / VERIFY BILL |
| Claude/ChatGPT/TradingView/signal subscriptions | UNKNOWN | API keys and code do not establish subscriptions | VERIFY BILL |

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

1. Reconcile 30 days of bills with actual provider/model attempts and tokens.
2. Record ten real brief uses in `KAI_RESEARCH_TRIAL.md`.
3. Compare X polling and shadow-analysis cost with actual operator use.
4. Reduce only demonstrated waste; preserve fallback, pricing and safety paths.
5. Verify the reduction on the next bill, including operator time.
