# Quality Cache Semantics

`GET /dashboard/api/quality` keeps `Cache-Control: no-store`; the response body carries cache
state so the frontend can render freshness without changing HTTP semantics.

Top-level `cache` fields:

- `generated_at_utc`: UTC timestamp of the payload computation stored in the in-process cache.
- `age_s`: seconds since that cached payload was generated.
- `stale`: `true` only when an expired payload was served while a newer computation was already running.
- `ttl_s`: freshness window for normal cached responses, currently `20.0`.
- `compute_ms`: measured server-side compute time for the payload refresh.

Frontend follow-up scope: render `stale=true` as a visible stale-while-revalidate state, not as a
metric failure. The rest of the payload remains the metric source of truth.
