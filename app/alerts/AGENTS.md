# AGENTS.md — app/alerts/

> Modul-Kontrakt für das Alerting-System (Sprint 3).
> Verbindlich für alle Agenten, die in diesem Modul arbeiten.

---

## Verantwortung

Dieses Modul ist zuständig für:
- Schwellwert-Prüfung (ThresholdEngine)
- Nachrichtenformatierung (formatters.py)
- Channel-Delivery: Telegram + E-Mail
- Digest-Akkumulierung (DigestCollector)
- Orchestrierung via AlertService

**Zuständiger Architekt**: Claude Code

---

## Modul-Struktur

```
app/alerts/
  base/
    interfaces.py     → AlertMessage, AlertDeliveryResult, BaseAlertChannel (ABC)
  channels/
    telegram.py       → TelegramAlertChannel (httpx async, Telegram Bot API)
    email.py          → EmailAlertChannel (smtplib via executor)
  formatters.py       → Pure-function text formatters (Telegram Markdown + plain Email)
  threshold.py        → ThresholdEngine — wraps is_alert_worthy()
  digest.py           → DigestCollector — deque-basierter Akkumulator
  service.py          → AlertService — einziger Entry-Point für Alert-Dispatch
```

---

## Interface-Grenzen

| Grenze | Regel |
|---|---|
| **Alert Gate** | `ThresholdEngine.should_alert()` ist das einzige Gate. Kein direkter Score-Zugriff in Channel-Code. |
| **Channel-Dispatch** | Immer über `AlertService`. Nie direkt `TelegramAlertChannel.send()` im Business-Code. |
| **Formatters** | Pure functions — kein I/O, kein State. Vollständig testbar ohne Mock. |
| **Settings** | Immer über `AlertSettings` aus `AppSettings`. Kein `os.environ` direkt. |
| **Dry-Run** | `ALERT_DRY_RUN=true` (default) — kein echter HTTP/SMTP-Request. |

---

## Key Models

| Klasse | Datei | Zweck |
|---|---|---|
| `AlertMessage` | `base/interfaces.py` | Normalisiertes Alert-Payload (provider-agnostisch) |
| `AlertDeliveryResult` | `base/interfaces.py` | Ergebnis eines einzelnen Delivery-Versuchs |
| `BaseAlertChannel` | `base/interfaces.py` | ABC für alle Channels |
| `ThresholdEngine` | `threshold.py` | Konfigurierbare Schwellwert-Prüfung |
| `DigestCollector` | `digest.py` | Batch-Akkumulator mit maxlen-Schutz |
| `AlertService` | `service.py` | Orchestrierung (from_settings factory) |

---

## Invarianten

1. **Kein Alert ohne Threshold-Check** — `process_document()` prüft immer.
2. **Fehler nie raised** — Channels geben `AlertDeliveryResult(success=False)` zurück.
3. **Dry-Run ist default** — `ALERT_DRY_RUN=true` schützt vor versehentlichem Flood.
4. **Formatters sind pure** — kein I/O, kein State, kein Mock nötig.
5. **Core Domain bleibt sauber** — kein Import von `httpx`, `smtplib` in `app/core/`.

---

## Konfiguration (AlertSettings)

| Variable | Default | Bedeutung |
|---|---|---|
| `ALERT_DRY_RUN` | `true` | Kein echter HTTP/SMTP-Request |
| `ALERT_MIN_PRIORITY` | `7` | Schwellwert (1–10) für Alert-Trigger |
| `ALERT_TELEGRAM_ENABLED` | `false` | Telegram aktivieren |
| `ALERT_TELEGRAM_TOKEN` | `""` | Bot-Token |
| `ALERT_TELEGRAM_CHAT_ID` | `""` | Ziel-Chat-ID |
| `ALERT_EMAIL_ENABLED` | `false` | E-Mail aktivieren |
| `ALERT_EMAIL_HOST` | `""` | SMTP-Server |
| `ALERT_EMAIL_PORT` | `587` | SMTP-Port (STARTTLS) |
| `ALERT_DIGEST_ENABLED` | `false` | Digest-Modus |
| `ALERT_DIGEST_INTERVAL_MINUTES` | `60` | Digest-Intervall |

---

## Was NICHT erlaubt ist

- Neue Provider (Slack, Discord, etc.) ohne Spec und Operator-Freigabe
- Direkter Score-Zugriff im Channel-Code
- `os.environ` direkt lesen
- Blocking I/O im async Context ohne `run_in_executor`
- Hard-coded Tokens oder API-Keys
