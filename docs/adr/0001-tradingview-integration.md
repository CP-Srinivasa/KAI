# ADR 0001 — TradingView Integration

**Status:** Accepted (2026-04-16, D-125)
**Stufe:** TV-1 (Foundation, non-signal-critical)
**Re-Entry Quality-Bar:** 2026-05-16

## Kontext

KAI hat in PHASE 5 (D-98/D-124) eine Quality-Bar für Signal-Promotion nicht erreicht:
Precision 41,94 % auf n=93 resolved directional alerts. Bei dieser Stichprobengröße liegt das
95 %-Konfidenzintervall bei ±10 pp — Threshold-Tuning produziert in diesem Bereich keine
zuverlässig messbaren Verbesserungen. Zusätzlich: 0 echte Paper-Fills mit PnL.

D-125 verschiebt die Quality-Bar um 30 Tage und nutzt das Fenster, um die Signal-Pipeline
zu verbreitern: TradingView als zusätzliche Signal-Quelle (Webhooks) und Daten-Quelle (OHLCV
via separaten Adapter), sowie lokale Indikatoren.

## Nicht-verhandelbare Leitplanken

1. **Nur offizielle Wege.** Widget-Embed, Webhook-Alerts, öffentliche Docs. Kein DOM-Scraping, kein privates WebSocket-Mitschneiden, kein Credential-Abgriff.
2. **Keine TradingView-Credentials im Repo.** Webhook-Secret via `.env`, niemals geloggt.
3. **Keine Library-Artefakte im Public-Repo.** Falls Charting-Library (Advanced Charts / Trading Platform) später beantragt wird: separate private Distribution.
4. **Fail-closed statt fail-open.** Invalid HMAC → 401. Oversized body → 413. Flag off → 404.
5. **Live-Trading bleibt default OFF.** Webhook-Payloads sind in TV-1 **nur Audit**, keine Signal-Pipeline-Anbindung.
6. **Provider-Abstraktion im Frontend.** `chart_mode = widget | advanced | trading_platform` als Enum; Widget ist der einzige in TV-1 implementierte Modus; spätere Stufen stecken am selben Interface ein.

## Entscheidungsbaum

| Option | In TV-1 | In TV-2/3 | Blocker |
|---|---|---|---|
| A. Widget-Embed | ✅ Default | ✅ Fallback | — |
| B. Advanced Charts Library | ❌ | optional | Lizenz-Antrag an TradingView (Operator-Task) |
| C. Trading Platform | ❌ | optional | Lizenz + Datenfeed-Provider |
| D. Eigene UI-Hülle um Chart (Panels/Watchlist) | teilweise | ✅ | — |

**Gewählt:** A + D für TV-1. B/C sind durch **externe Lizenzanträge geblockt** und nicht selbst auflösbar.

## Datenfluss (TV-1)

```
TradingView (Browser) ──[Widget Script]──> <TradingViewChart/>  (read-only Visualisierung)

TradingView (Alerts)  ──[HTTPS POST]────>  POST /tradingview/webhook
                                              ├── HMAC-SHA256 verify (shared secret)
                                              ├── Body ≤ 64 KiB, JSON parse
                                              ├── Idempotency via payload hash
                                              ├── Persist → artifacts/tradingview_webhook_audit.jsonl
                                              └── Return 202 Accepted
                                           NO signal-pipeline wiring in TV-1.
```

## Sicherheitsmodell

- **Transport:** HTTPS only (Cloudflare Tunnel, bestehend). HTTP-Ingress wird auf Edge abgelehnt.
- **Authentifikation:** HMAC-SHA256 über Raw-Body, Header `X-KAI-Signature: sha256=<hex>`. Secret in `TRADINGVIEW_WEBHOOK_SECRET`, mindestens 32 Zeichen.
- **Autorisierung:** Nur Alerts mit gültiger Signatur werden persistiert. Ungültige Signaturen → 401, Audit-Log-Eintrag mit Rejection-Grund.
- **Rate-Limit / Body-Size:** Bestehende `RequestGovernanceMiddleware` erzwingt `APP_MAX_REQUEST_BODY_BYTES`. Zusätzliche Idempotency-Cache-Größe 256 Einträge.
- **Replay-Schutz:** Payload-Hash wird im In-Memory-LRU für 5 Minuten gecacht. Doppelte Einreichungen werden als Replay markiert und ignoriert.
- **Secret-Rotation:** Operator-Task. Beim Rotieren: Env umsetzen, Server-Neustart, TradingView-Alert-Templates anpassen.

## Feature-Flag-Matrix

| Flag | Default | Wirkung |
|---|---|---|
| `TRADINGVIEW_WEBHOOK_ENABLED` | `false` | Router liefert 404 wenn `false`. Kein Listener. |
| `TRADINGVIEW_WEBHOOK_SECRET` | `""` | Bei `""` + `ENABLED=true` → Startup-Warnung, alle Requests fail-closed. |
| `VITE_TRADINGVIEW_ENABLED` | `false` | Frontend-Komponente zeigt Disabled-Placeholder. |

## LLM-Datenfluss (Vorbereitung, nicht TV-1)

TV-1 persistiert Webhook-Payloads mit vollständigem Audit-Trail. In TV-2/TV-3 werden diese
Events in normalisierte Signal-Events übersetzt und mit Provenienz-Tags (`source`, `version`,
`signal_path_id`) in die Signal-Pipeline eingebracht.

**In TV-1 keine LLM-Anbindung** — der Webhook ist reiner Audit-Endpoint.

## Betriebsmodell

- **Audit-Pfad:** `artifacts/tradingview_webhook_audit.jsonl` (append-only, eine JSON-Zeile pro Request inkl. Rejections).
- **Monitoring:** Bestehende `/health`-Route; zusätzliche Feld-Erweiterung folgt in TV-2.
- **Rollback:** `TRADINGVIEW_WEBHOOK_ENABLED=false` setzen, Restart. Daten bleiben im Audit-Log.

## Offene Punkte (ehrlich)

1. **Charting-Library (Advanced Charts)** — Operator muss bei TradingView beantragen. Bis dahin Widget-only.
2. **Account-Inhalte** (Layouts, private Indikatoren) — per Design nicht offiziell synchronisierbar. Keine Umgehung geplant.
3. **Webhook-Zustellung** setzt öffentlich erreichbaren Endpoint voraus. Der bestehende Cloudflare-Quick-Tunnel wird in der Reminder-Memory durch einen Named Tunnel ersetzt.

## Referenzen

- TradingView Widgets: https://www.tradingview.com/widget-docs/
- TradingView Webhooks: https://www.tradingview.com/support/solutions/43000529348-about-webhooks/
- DECISION_LOG D-125
- MEMORY `project_tv_pivot.md`
