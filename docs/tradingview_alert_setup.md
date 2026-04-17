# TradingView Alert Setup Guide

## Webhook-URL

```
https://kai-trader.org/tradingview/webhook
```

Bis die Domain aktiv ist (Nameserver-Umstellung ausstehend):
```
http://127.0.0.1:8000/tradingview/webhook
```

## Authentifizierung

TradingView kann keine Custom-Headers senden. Deshalb wird der Auth-Token
als `"token"`-Feld direkt im JSON-Body mitgeschickt. KAI entfernt das Feld
automatisch bevor der Payload gespeichert oder weitergeleitet wird.

## Payload-Format

KAI erwartet JSON mit diesen Feldern:

| Feld | Pflicht | Typ | Werte |
|---|---|---|---|
| `token` | ja | string | Shared Token (siehe Templates) |
| `ticker` | ja | string | z.B. `"BTCUSDT"`, `"ETHUSDT"` |
| `action` | ja | string | `"buy"`, `"sell"`, `"close"` |
| `price` | nein | number | aktueller Kurs |
| `note` | nein | string | Kontext / Grund |
| `strategy` | nein | string | Name der Strategie / Indikator |

## Alert-Templates (Copy-Paste in TradingView)

### RSI Oversold (Buy Signal)

**Alert Name:** `KAI RSI Oversold {{ticker}}`
**Condition:** RSI(14) crosses up 30
**Message (Webhook Body):**

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "note": "RSI(14) crossed above 30 (oversold recovery)",
  "strategy": "rsi_oversold_14"
}
```

### RSI Overbought (Sell Signal)

**Alert Name:** `KAI RSI Overbought {{ticker}}`
**Condition:** RSI(14) crosses down 70
**Message (Webhook Body):**

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "sell",
  "price": {{close}},
  "note": "RSI(14) crossed below 70 (overbought reversal)",
  "strategy": "rsi_overbought_14"
}
```

### EMA Cross Bullish (Buy Signal)

**Alert Name:** `KAI EMA Cross Bull {{ticker}}`
**Condition:** EMA(9) crosses up EMA(21)
**Message (Webhook Body):**

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "note": "EMA(9) crossed above EMA(21) -- bullish momentum",
  "strategy": "ema_cross_9_21"
}
```

### EMA Cross Bearish (Sell Signal)

**Alert Name:** `KAI EMA Cross Bear {{ticker}}`
**Condition:** EMA(9) crosses down EMA(21)
**Message (Webhook Body):**

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "sell",
  "price": {{close}},
  "note": "EMA(9) crossed below EMA(21) -- bearish momentum",
  "strategy": "ema_cross_9_21"
}
```

### MACD Signal Cross (Buy)

**Alert Name:** `KAI MACD Bull {{ticker}}`
**Condition:** MACD crosses up Signal
**Message (Webhook Body):**

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "note": "MACD crossed above signal line",
  "strategy": "macd_signal_cross"
}
```

### Generisches Template (frei anpassbar)

```json
{
  "token": "jR4Z4-T-CTTf-Ihf_FqZtduFD1MoZxXfwtJsFFpOFdo",
  "ticker": "{{ticker}}",
  "action": "buy",
  "price": {{close}},
  "note": "Eigene Beschreibung hier",
  "strategy": "custom"
}
```

## Empfohlene Paare

| Paar | TradingView Ticker |
|---|---|
| Bitcoin | `BINANCE:BTCUSDT` |
| Ethereum | `BINANCE:ETHUSDT` |
| Solana | `BINANCE:SOLUSDT` |
| Avalanche | `BINANCE:AVAXUSDT` |

## Alert erstellen in TradingView (Schritt-fuer-Schritt)

1. Chart oeffnen (z.B. BINANCE:BTCUSDT)
2. Indikator hinzufuegen (z.B. RSI)
3. Rechtsklick auf Indikator-Linie > "Add Alert"
4. Condition: z.B. "RSI(14) Crosses Up 30"
5. Notifications:
   - Webhook URL: `https://kai-trader.org/tradingview/webhook`
   - Message: Template von oben einfuegen (mit token-Feld!)
6. Name: z.B. "KAI RSI Oversold BTCUSDT"
7. Expiration: "Open-ended" (kein Ablauf)
8. Create

## Signal-Flow nach Webhook-Empfang

```
TradingView Alert
    |
    v
POST /tradingview/webhook (token im Body)
    |
    v
Auth geprueft --> token-Feld entfernt (nie gespeichert)
    |
    v
TradingViewSignalEvent normalisiert
    |
    v
artifacts/tradingview_pending_signals.jsonl
    |
    v
Operator-Approval noetig (kein Auto-Trading!)
    python -m app.cli.main tradingview list
    python -m app.cli.main tradingview promote <event_id>
    |
    v
SignalCandidate erzeugt (erst nach Approval)
```

## Troubleshooting

```bash
# Letzte Webhook-Events pruefen
cat artifacts/tradingview_webhook_audit.jsonl | tail -5 | python -m json.tool

# Pending Signals anzeigen
cat artifacts/tradingview_pending_signals.jsonl | tail -5 | python -m json.tool

# Health-Check
curl http://127.0.0.1:8000/health
```
