# ADR 0005 — Premium-Telegram Fastlane (30-Tage-Testfenster)

- Status: accepted
- Datum: 2026-06-05
- Kontext-Goal: „Telegram Premium Signal Fastlane" (Operator-Auftrag)

## Problem

Authentische Premium-Telegram-Signale wurden geparst, gespeichert und z.T.
approved, öffneten aber **keine** Paper-Position: der globale Kill-Switch
`entry_mode=disabled` **und** `premium.paper_execution_enabled=false` blockten
jede neue Premium-Paper-Entry. Damit entstanden keine Forward-Daten, mit denen
sich Premium-Signalqualität (Pfad A/B, Premium-Bonus, Forward-Precision) messen
ließe. Pre-Trade-Quality-Gates „töteten" Signale, bevor je ein Trade entstand.

## Entscheidung

Eine **scoped, paper-only Fastlane** für ein kontrolliertes 30-Tage-Fenster:
authentische Premium-Telegram-Signale werden in Paper/Testnet/Demo **sofort**
durchgereicht. Der globale `entry_mode`/`premium_paper`-Block wird **nur für
diese Quelle, nur für nicht-live Routen** zu einem beobachteten Hinweis
herabgestuft — der klassische Pfad jeder anderen Quelle bleibt unverändert.

Kern-SSOT ist die reine Funktion
`app.execution.premium_fastlane.should_route_premium_fastlane(envelope, settings)`.
Bridge (`envelope_to_paper_bridge`) und Runtime-Endpoint
(`/api/premium-signals/runtime`) konsultieren dieselbe Funktion, damit
Operator-Wahrheit und Ausführungsverhalten nicht driften.

## Gate-Matrix

| Gate            | Classic        | Fastlane Paper/Testnet/Demo | Live          |
|-----------------|----------------|-----------------------------|---------------|
| Manual Approval | blockierend    | bypass                      | blockierend   |
| Source Allowlist| blockierend    | premium-auth bypass         | blockierend   |
| entry_mode      | blockierend    | bypass (observe)            | blockierend   |
| Quality/Bonus   | observe/block  | observe-only                | blockierend   |
| Schema          | blockierend    | blockierend                 | blockierend   |
| SL/TP-Geometrie | blockierend    | blockierend                 | blockierend   |
| Duplicate       | blockierend    | blockierend                 | blockierend   |
| Notional-Cap    | blockierend    | blockierend                 | blockierend   |

## Harte Invarianten

- **Live bleibt geschützt.** Eine Live-Route ist nur erlaubt, wenn ALLE DREI
  gesetzt sind: `PREMIUM_FASTLANE_LIVE_ENABLED=true` +
  `PREMIUM_LIVE_EXECUTION_ENABLED=true` +
  `PREMIUM_LIVE_CANARY_EXPLICIT_ACK=I_UNDERSTAND_REAL_CAPITAL_RISK`. Die
  Paper-Bridge sendet ohnehin nie eine Live-Order.
- **Mindest-Guards nie gelockert:** Schema, Entry/SL/Targets/Side/Symbol,
  Duplicate, `quantity>0`, Notional in `[min,max]`, SL/TP-Geometrie, auflösbarer
  Scale (sonst `requires_scale_review`).
- **Fail-closed:** `PREMIUM_FASTLANE_ENABLED` default **False**; die Runtime/Pi
  schaltet bewusst per `.env` ein.
- **Nur authentische Premium-Quelle** (Source-Tag `telegram_premium*` + stabile
  Telegram-Identität) erhält den Allowlist-Bypass.

## Konfiguration (Auszug, Defaults)

`PREMIUM_FASTLANE_ENABLED=false` · `_DURATION_DAYS=30` ·
`_MODE=paper_testnet_demo` · `_LIVE_ENABLED=false` ·
`_BYPASS_ENTRY_MODE_FOR_PAPER=true` · `_BYPASS_SOURCE_ALLOWLIST=true` ·
`_BYPASS_MANUAL_APPROVAL=true` · `_DEFAULT_LEVERAGE=10` · `_MAX_LEVERAGE=10` ·
`_DEFAULT_NOTIONAL_USDT=100` · `_MIN_NOTIONAL_USDT=10` · `_MAX_NOTIONAL_USDT=250` ·
`_MAX_OPEN_POSITIONS=50` · `_PAPER_EQUITY_USDT=10000`.
Live-Triple: `PREMIUM_FASTLANE_LIVE_ENABLED`, `PREMIUM_LIVE_EXECUTION_ENABLED`,
`PREMIUM_LIVE_CANARY_EXPLICIT_ACK`.

## Replay

`python -m scripts.replay_premium_fastlane --fixture tests/fixtures/latest_premium_signals.json`
(15 Pflichtsymbole, deterministisch via `mock_spot`, ohne Live-Market).

## Bewusst (noch) nicht in diesem Sprint

- Echte Testnet-/Demo-/Simulated-Exchange-Submission (Route-Auswahl + Audit
  vorhanden; Submission-Adapter folgt). Aktuell läuft die Ausführung über die
  bestehende Paper-Engine.
- Getrennter `premium_fastlane_paper_account` (Settings-Feld
  `paper_equity_usdt` vorhanden; physische Account-Trennung folgt).
- Eigenständiges Fastlane-Dashboard-Panel mit allen 25 Metriken; der
  Runtime-Banner zeigt bereits den Fastlane-aktiv-Zustand + Classic-Hinweise.
- Vollständig event-getriebener Sofort-Submit; aktuell trägt der Bridge-Tick.
