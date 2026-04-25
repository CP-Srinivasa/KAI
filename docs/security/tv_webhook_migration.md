# TradingView Webhook Auth Migration (V8-f)

**Status:** in progress · started 2026-04-25
**Owner:** Operator
**Reference:** SATOSHI SAT-T-001 / SAT-P-001 · DECISION_LOG D-196 (pending)

## Why

The shared-token webhook auth path (`webhook_auth_mode=shared_token` /
`hmac_or_token`) does **not** bind the credential to the request body. A
leaked token replays any payload — Symbol, Side, Size all forgeable. Layer-1
payload-hash dedup catches *identical* replays only; tampered bodies pass.
D-179 event-id replay-guard helps **only** when the body carries `event_id`.

This document tracks the deprecation path toward HMAC-style body-binding.

## Target state

The webhook accepts only modes that bind the credential to the body:

| Mode | Body integrity | Use when |
|---|---|---|
| `hmac` | full HMAC-SHA256 over raw body | client can compute HMAC (proxy/worker present) |
| `hmac_strict_event_id` | shared-token + mandatory `event_id` + `ts` skew | TradingView native webhook, no proxy |
| `shared_token`, `hmac_or_token` | **deprecated** | none; one warning per process; remove after migration |

The kill-switch `webhook_shared_token_disabled=true` short-circuits every
token-mode at the door without rotating env. Use it during incident response
or once migration is complete.

## Strict mode (`hmac_strict_event_id`)

Same credential check as `shared_token` (constant-time `X-KAI-Token`
compare), with two additional body fields enforced:

- `event_id` — string, ≥ 8 chars. Defends against replay across restarts
  (D-189 PersistentReplayCache picks this up automatically).
- `ts` — ISO-8601 with timezone. Server requires `|now − ts| ≤ skew_seconds`
  (default 300s, configurable via `TRADINGVIEW_WEBHOOK_STRICT_TS_SKEW_SECONDS`).

Reject reasons surface in the audit log under `reason=strict_<detail>`:
`strict_missing_event_id`, `strict_event_id_too_short`, `strict_missing_ts`,
`strict_invalid_ts`, `strict_clock_skew`, `strict_not_a_dict`. All are
counted in the brute-force bucket (D-193) — repeated strict-mode failures
trip the rate limiter exactly like a credential failure.

### TradingView alert template (Pine v5)

```text
{
  "ticker": "{{ticker}}",
  "action": "{{strategy.order.action}}",
  "price": "{{close}}",
  "event_id": "{{strategy.order.id}}-{{time}}",
  "ts": "{{timenow}}",
  "note": "{{strategy.order.alert_message}}"
}
```

`{{time}}` and `{{timenow}}` both yield ISO timestamps; `{{strategy.order.id}}`
provides per-fill uniqueness. For non-strategy alerts use `"event_id":
"alert-{{plot_0}}-{{time}}"` or any composition that yields a stable,
fill-specific identifier.

### Operator setup

1. Set `.env`:
   ```
   TRADINGVIEW_WEBHOOK_AUTH_MODE=hmac_strict_event_id
   TRADINGVIEW_WEBHOOK_SHARED_TOKEN=<existing token>
   TRADINGVIEW_WEBHOOK_STRICT_TS_SKEW_SECONDS=300
   ```
2. Update each TradingView alert message body using the template above.
3. Restart `kai-server` (Pi: `sudo systemctl restart kai-server`).
4. Smoke: send one TV alert; verify in `artifacts/alert_audit.jsonl` that
   `provenance.auth_method == "shared_token"`, `routing.status == "emitted"`,
   no `strict_*` reject. If `strict_clock_skew` repeats, check NTP sync on
   the source side.

## Full HMAC mode (target end-state)

TradingView cannot compute HMAC client-side. To reach `hmac` mode you need
a thin proxy between TV and KAI that signs the body with a shared HMAC
secret. Cloudflare Worker example (drop-in, ~30 lines):

```javascript
// Worker route: tv-webhook.<your-domain>/webhook  → KAI /tradingview/webhook
// Env: KAI_WEBHOOK_URL, KAI_WEBHOOK_SECRET (32+ random bytes hex)
export default {
  async fetch(req, env) {
    if (req.method !== "POST") return new Response("method", { status: 405 });
    const body = await req.text();
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(env.KAI_WEBHOOK_SECRET),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"],
    );
    const sig = await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(body),
    );
    const sigHex = [...new Uint8Array(sig)]
      .map(b => b.toString(16).padStart(2, "0")).join("");
    return fetch(env.KAI_WEBHOOK_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-KAI-Signature": `sha256=${sigHex}`,
      },
      body,
    });
  },
};
```

KAI `.env` for this path:
```
TRADINGVIEW_WEBHOOK_AUTH_MODE=hmac
TRADINGVIEW_WEBHOOK_SECRET=<same as KAI_WEBHOOK_SECRET in the Worker>
TRADINGVIEW_WEBHOOK_SHARED_TOKEN=     # cleared
```

The Worker URL becomes the TradingView alert webhook URL; KAI sees only
HMAC-signed requests. Shared-token path can then be hard-disabled with the
kill-switch.

## Migration phases

| Phase | When | Action |
|---|---|---|
| 1. Strict-mode opt-in | now (2026-04-25) | Code shipped. Update TV templates, flip env to `hmac_strict_event_id`. |
| 2. Deprecation warnings | now | Logs emit `tradingview_webhook_auth_mode_deprecated` once per process for legacy modes. Monitor for unexpected legacy traffic. |
| 3. HMAC migration window | post-Pi (after 2026-05-01) | Stand up the Cloudflare Worker, validate parallel-traffic, switch TV to the Worker URL. |
| 4. Final cutover | when zero legacy hits in 7 days | Set `TRADINGVIEW_WEBHOOK_AUTH_MODE=hmac`, set `TRADINGVIEW_WEBHOOK_SHARED_TOKEN_DISABLED=true`, remove the token from env. |
| 5. Code cleanup | post-cutover | Remove `shared_token` / `hmac_or_token` modes from settings + auth code. Strict mode collapses into HMAC. |

## What remains weaker than full HMAC

Even in strict mode, a leaked `webhook_shared_token` enables:

- forging requests *within the skew window* with attacker-chosen `event_id`
  (PersistentReplayCache de-dupes per id, but the *first* attacker request
  for a fresh id still lands)
- not body tampering — body fields beyond `event_id` and `ts` are still not
  signed

These residuals motivate Phase 3+. Do not treat strict mode as the final
state; treat it as the bridge that closes the *most exploited* lane while
the HMAC migration is staged.

## Incident response

Token leak suspected:
1. Set `TRADINGVIEW_WEBHOOK_SHARED_TOKEN_DISABLED=true`, restart server.
2. Rotate `TRADINGVIEW_WEBHOOK_SHARED_TOKEN` to a new value (32+ random
   bytes).
3. Update every TV alert with the new token.
4. Set `TRADINGVIEW_WEBHOOK_SHARED_TOKEN_DISABLED=false`, restart.
5. Audit `artifacts/alert_audit.jsonl` for `auth_method=shared_token` rows
   between leak window start and rotation; cross-check `external_event_id`
   replay-cache misses for impossible bodies.
