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

## Datenfluss (TV-1 → TV-3)

```
TradingView (Browser) ──[Widget Script]──> <TradingViewChart/>  (read-only Visualisierung)

TradingView (Alerts)  ──[HTTPS POST]────>  POST /tradingview/webhook
                                              ├── Auth (HMAC | shared_token | hmac_or_token)
                                              ├── Body ≤ 64 KiB, JSON parse
                                              ├── Replay-cache via payload hash
                                              ├── Persist → artifacts/tradingview_webhook_audit.jsonl
                                              └── IF webhook_signal_routing_enabled (TV-3):
                                                   normalize → TradingViewSignalEvent
                                                   append  → artifacts/tradingview_pending_signals.jsonl
                                                   (Operator-Approval erforderlich; kein Auto-Trade)
                                              └── Return 202 Accepted
```

TV-3-Grenze: Pending-Events werden **nicht** automatisch zu `SignalCandidate`
promoted. Die Promotion ist ein separater, operatorgesteuerter Schritt.
Rationale: TV-Alerts enthalten nicht die vom KAI Decision-Schema geforderten
Felder (thesis, confluence, risk assessment). Synthetische Defaults würden die
Datenbasis der späteren Quality-Bar-Messung verfälschen.

## Sicherheitsmodell

- **Transport:** HTTPS only (Cloudflare Tunnel, bestehend). HTTP-Ingress wird auf Edge abgelehnt.
- **Authentifikation:** Drei Modi (TV-2.1), default unverändert HMAC:
  - `hmac` (default): HMAC-SHA256 über Raw-Body, Header `X-KAI-Signature: sha256=<hex>`. Secret in `TRADINGVIEW_WEBHOOK_SECRET`, mindestens 32 Zeichen. Body-Integrität verifiziert.
  - `shared_token` (TV-2.1): Statisches Token im Header `X-KAI-Token: <secret>`. Constant-time Vergleich gegen `TRADINGVIEW_WEBHOOK_SHARED_TOKEN`. **Schwächer als HMAC** — keine Body-Integrität. Notwendig, weil TradingView's nativer Webhook keine Body-HMACs erzeugen kann.
  - `hmac_or_token` (TV-2.1): Beide Header werden akzeptiert; HMAC wird zuerst geprüft (stärker). Nutzt man, wenn parallel ein Relay HMAC erzeugt und ein Direkt-Pfad das Shared-Token verwendet.
  Auth-Methode wird im Audit-Log unter `auth_method` und `provenance.auth_method` festgehalten.
- **Autorisierung:** Nur Alerts mit gültiger Auth werden persistiert. Ungültige Credentials → 401, Audit-Log-Eintrag mit Rejection-Grund.
- **Rate-Limit / Body-Size:** Bestehende `RequestGovernanceMiddleware` erzwingt `APP_MAX_REQUEST_BODY_BYTES`. Zusätzliche Idempotency-Cache-Größe 256 Einträge.
- **Replay-Schutz:** Payload-Hash wird im In-Memory-LRU für 5 Minuten gecacht. Doppelte Einreichungen werden als Replay markiert und ignoriert.
- **Secret-Rotation:** Operator-Task. Beim Rotieren: Env umsetzen, Server-Neustart, TradingView-Alert-Templates anpassen.

## Feature-Flag-Matrix

| Flag | Default | Wirkung |
|---|---|---|
| `TRADINGVIEW_WEBHOOK_ENABLED` | `false` | Router liefert 404 wenn `false`. Kein Listener. |
| `TRADINGVIEW_WEBHOOK_SECRET` | `""` | Pflicht in `hmac` / `hmac_or_token` Modus. Leer → Endpoint 404. |
| `TRADINGVIEW_WEBHOOK_AUTH_MODE` | `hmac` | `hmac` \| `shared_token` \| `hmac_or_token`. |
| `TRADINGVIEW_WEBHOOK_SHARED_TOKEN` | `""` | Pflicht in `shared_token` / `hmac_or_token` Modus. Leer → Endpoint 404 bzw. Settings-Validation-Fehler. |
| `VITE_TRADINGVIEW_ENABLED` | `false` | Frontend-Komponente zeigt Disabled-Placeholder. |
| `BINANCE_ENABLED` | `false` | TV-2 OHLCV-Adapter. Adapter wird nur konstruiert wenn `true`. CoinGecko bleibt Default-Provider. |
| `TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED` | `false` | TV-3: akzeptierte Payloads werden normalisiert und in die Pending-Queue geschrieben. Default off ⇒ TV-1/TV-2-Verhalten (audit-only). |
| `TRADINGVIEW_WEBHOOK_PENDING_SIGNALS_LOG` | `artifacts/tradingview_pending_signals.jsonl` | Pfad zur Pending-Queue (append-only JSONL). |

## TV-3: Signal-Routing (Webhook → Pending-Queue)

Wenn `TRADINGVIEW_WEBHOOK_SIGNAL_ROUTING_ENABLED=true`, wird jedes **akzeptierte**
Webhook-Payload durch `app/signals/tradingview_event.py` normalisiert:

- **Pflichtfelder:** `ticker`, `action ∈ {buy, sell, close}`. Fehlt oder ungültig → Audit markiert `routing.status=normalize_failed`, **kein** Event in der Pending-Queue. 202 bleibt erhalten (Webhook ist angekommen), aber kein Signal-Path-Id in der Response.
- **Optional:** `price` (positiv, numerisch oder numerischer String), `note`, `strategy`.
- **Output:** `TradingViewSignalEvent(event_id, received_at, ticker, action, price, note, strategy, source_request_id, source_payload_hash, provenance)` mit `provenance.signal_path_id=tvpath_<hex>` für spätere Quality-Bar-Attribution.
- **Persistenz:** One-line-JSON je Event, append-only in `artifacts/tradingview_pending_signals.jsonl`.

**Kein Auto-Trade in TV-3.** Events warten in der Pending-Queue bis Operator
sie in einem späteren Schritt (TV-4+) promotet. Die Promotion befüllt die
vom KAI Decision-Schema geforderten Felder (thesis, confluence, risk) — der
TV-Alert allein reicht dafür nicht.

**Bedrohungsmodell:** Bei aktivem `shared_token`-Modus + aktivem Signal-Routing
kann ein Angreifer mit Token-Kenntnis beliebige Events in die Pending-Queue
einspeisen. Approval-Gate vor Promotion macht das für TV-3 aushaltbar; für
Live-Trading nicht ausreichend. Daher bleibt Live-Trading off und jede
Promotion ist manuell.

## LLM-Datenfluss (Vorbereitung, nicht TV-3)

TV-1 persistiert Webhook-Payloads mit Audit-Trail. TV-2 erweitert OHLCV +
RSI als Signal-Zutaten. TV-3 erzeugt Pending-Events mit Provenienz-Tags
(`source`, `version`, `signal_path_id`). Erst in einer späteren Phase
werden Pending-Events operatorgesteuert in `SignalCandidate`-Objekte
promoted — inkl. LLM-/Rule-Augmentation der fehlenden Decision-Felder.

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
