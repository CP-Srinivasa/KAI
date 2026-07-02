# max_open_positions 3->6 Impact-Window-Check — 2026-05-21

**Auftrag:** DS-20260521-V4 (P2) — Prüfen, ob die Schema-Cap-Erhöhung `max_open_positions 3->6` (commit `d2a3e73`, deployed 2026-05-20 21:29:38 UTC) im Beobachtungsfenster reale Position-Slot-Auslastung erzeugt hat.
**Methode:** Auswertung `artifacts/paper_execution_audit.jsonl` (268 events, Mär-Mai 2026).
**Verfasser:** Claude Code Mid-Window-Forensik. Read-only.

---

## 1. Post-Deploy-Fenster (seit 2026-05-20 21:29:38 UTC)

| Metrik | Wert |
|---|---|
| Beobachtungsfenster | 21:29 UTC 20.05. → 06:40 UTC 21.05. = **~9h 11min** |
| Audit-Events total | 23 |
| Buy-Fills (Position-Open) | **1** (BTC/USDT @ 04:14:07 UTC, mittlerweile 06:40 UTC via `stop` geschlossen) |
| MAX gleichzeitige Open-Positions | **1** |
| Aktuell offen am Stream-Ende | 0 (BTC ist `position_closed reason=stop`) |

**Befund:** Cap-Bump 3->6 ist im Beobachtungsfenster **noch nicht zum Tragen gekommen** — kein einziger Slot oberhalb von 1 wurde genutzt. Die Operator-Decision ist nach ~9h Beobachtung **funktional symbolisch**.

## 2. All-Time-Kontext (Pre-Deploy)

| Metrik | Wert |
|---|---|
| Stream-Anfang | 2026-03-26T00:05:25 UTC |
| Stream-Ende | 2026-05-21T06:40:56 UTC |
| Events total | 268 |
| MAX gleichzeitige Open-Positions (all-time) | **12** am 2026-05-20T20:10:12 UTC (ON, Q, XNY, PIEVERSE, TRUTH, BAS, ASTER, DASH, BIRB, BTC, BEAT, US — je `/USDT`) |

**Interpretation:** Das All-Time-Max von 12 widerspricht NICHT der Pre-Deploy-Cap von 3. Erklärung (aus commit `c583922` body): mehrere dieser Positions (ASTER, BAS, TRUTH, PIEVERSE) wurden über `/position-repair` manuell ins Audit reingeschoben (`idempotency_key="repair-close:<sym>:<unix_ts>"`-Pattern). Der Repair-Pfad umgeht den schema-cap. Reguläre Auto-Trades hatten pre-Deploy nie >3 parallel.

## 3. Konsequenz für EOW-Review 23.05.

- Die Cap-Erhöhung 3->6 ist **deployed-aber-ungenutzt**.
- Erst wenn Signal-Frequenz nennenswert steigt (z.B. nach Priority-Scoring-Decision Option A/B aus [[priority-scoring-decision-brief-2026-05-23]]) wird der Cap-Bump empirisch relevant.
- Bis dahin: keine zusätzlichen Sizing-Anpassungen, kein erhöhtes Risiko-Exposure aus dieser Decision allein.

## 4. Empfehlung

**Heute:** Kein Eingriff. Befund dokumentieren.

**Re-Audit-Trigger:** sobald Post-Deploy-Fenster >= 7 Tage UND >= 5 Buy-Fills enthält — dann erneut max-concurrent prüfen. Falls dann immer noch <=3: Cap-Bump war Operator-Symbolik-Decision (legitim, dokumentiert).

**Kein Eskalations-Pfad nötig.**

## 5. Datenquellen

- `artifacts/paper_execution_audit.jsonl` (268 events, schema_version v2)
- commit `d2a3e73` (Deploy 2026-05-20 21:29:38 UTC)
- commit `c583922` (Trail-API + Repair-Pfad-Doku)
- Analyse-Script: `/tmp/v4_analysis.py` (lokal in temp)
