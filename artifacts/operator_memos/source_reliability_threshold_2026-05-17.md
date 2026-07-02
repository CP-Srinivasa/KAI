# Source-Reliability: Threshold-Dokumentation + Diagnose — 2026-05-17

**Kontext:** V7-Task aus `/goal`-Sprint 2026-05-17. Memory-Pin `[[session-2026-05-16-goal-sprint-pause-handover]]`: "8 Sources erfasst, alle noch insufficient". Frage des Operators: was ist `n_min` für `sufficient`, und wo ist der Sufficient-Visibility-Tile?

---

## 1. Bestehende Thresholds (Code-State)

Quelle: `app/learning/source_reliability.py`

| Konstante | Wert | Bedeutung |
|---|---|---|
| `_MIN_N_FOR_DEMOTE` | 20 | Minimaler Sample-Count, bevor eine Source eine `low`/`watch`-Tier bekommen kann |
| `_MIN_N_FOR_PROMOTE` | 30 | Minimaler Sample-Count für `trusted`-Tier |
| `_WILSON_LOW_THRESHOLD` | 0.30 | Wilson-Lower-Bound < 0.30 → `low` Tier (Demote -2) |
| `_WILSON_HIGH_THRESHOLD` | 0.65 | Wilson-Lower-Bound > 0.65 → `trusted` Tier (Promote +1) |
| `_DEFAULT_WINDOW_DAYS` | 90 | Rolling-Window für Source-Outcome-Aggregation |
| `_Z_95` | 1.96 | Zwei-seitiger 95%-Konfidenz-Z-Score |

**Sufficient-Definition (informell):** Eine Source ist `sufficient`, sobald `n ≥ _MIN_N_FOR_DEMOTE = 20` Hard-Outcomes (hit + miss) im 90-Tage-Fenster erreicht. Vorher: Tier = `insufficient`, `priority_modifier = 0`.

Dies ist nirgendwo formell dokumentiert; das hier ist die Re-Konstruktion aus dem Code.

---

## 2. Pi-Live-Snapshot (2026-05-17, monitor/source_reliability.json)

Source-Reliability-Report generiert am 2026-05-16 12:21 UTC, `window_days=90`:

| Source | n | hits | miss | Wilson-Lower | Tier |
|---|---|---|---|---|---|
| tradingview_webhook | 12 | 2 | 10 | 0.047 | insufficient |
| beincrypto | 6 | 4 | 2 | 0.300 | insufficient |
| YouTube | 4 | 2 | 2 | 0.150 | insufficient |
| cryptobriefing | 3 | 2 | 1 | 0.208 | insufficient |
| cryptoslate | 3 | 2 | 1 | 0.208 | insufficient |
| cointelegraph | 2 | 1 | 1 | 0.095 | insufficient |
| coindesk | 1 | 1 | 0 | 0.207 | insufficient |
| thedefiant | 1 | 1 | 0 | 0.207 | insufficient |
| **Sum** | **32** | **15** | **17** | — | — |

**Alle 8 Sources insufficient.** Stärkste Source (tradingview_webhook, n=12) ist 8 Hard-Outcomes vom Threshold entfernt.

---

## 3. Kritischer Befund: 91% Source-Mapping-Lücke

**Daily-Strategy meldet 382 hard-resolved Alerts (Pi-Bootstrap 2026-05-17 06:00 UTC).**

**Source-Reliability sieht nur 32 hard-resolved Alerts (Pi-Report 2026-05-16 12:21 UTC).**

Differenz: 350 Alerts (91.6%) sind hard-resolved (hit oder miss annotiert), haben aber kein Source-Mapping in `source_by_doc`. Sie werden vom Source-Reliability-Aggregator komplett übergangen (`app/learning/source_reliability.py:196-198`):

```python
source = source_by_doc.get(rec.document_id)
if not source:
    continue
```

**Konsequenz:** Threshold-Reduktion (z.B. `_MIN_N_FOR_DEMOTE=10`) würde 1–2 Sources nominal `sufficient` machen, aber das Grundproblem nicht lösen — der Lernstack arbeitet mit 8.4% der verfügbaren Hard-Outcome-Daten.

**Verdacht zur Wurzel:** Die `source_by_doc`-Mapping-Pipeline (vermutlich aus `alert_audit.jsonl.source`) liefert für die Mehrheit der Documents keinen Source-Eintrag. Mögliche Ursachen:
- Alerts vor Implementierung der Source-Auflösung (Backlog-Drift)
- Multi-Channel-Documents ohne primären Source-Tag
- Telegram-Listener-Documents ohne Source-Mapping (premium-channel-Source ist häufig unique-id, nicht der Channel)

---

## 4. Empfehlung

**Keine Threshold-Reduktion im Re-Entry-Fenster.** Das wäre Symptom-Bekämpfung. Stattdessen:

**Reihenfolge (P1 → P3):**

1. **P1: Source-Mapping-Pipeline-Forensik** (~60min, lokal/Pi)
   - `python -c "from app.learning.source_reliability import _build_source_by_doc; ..."` — wie viele Documents haben source = None?
   - Stichproben aus den 350 unmapped Alerts: welcher Source-Header steht in `alert_audit.jsonl` für sie?
   - Output: `artifacts/source_mapping_audit_2026-05-17.json` mit Mapping-Lücken-Statistik.

2. **P1: Source-Mapping-Fix** (~120-180min, P0-PR-Kandidat falls Code-Fix)
   - Falls Telegram-Premium-Channel-Documents systematisch ohne Source-Mapping: Mapping-Logik in `_build_source_by_doc` erweitern.
   - Re-Run `python scripts/source_reliability_recalc.py` nach Fix.

3. **P2: Dashboard-Tile "Source-Reliability"** (~90min, DALI-Scope)
   - `/dashboard/learning/sources` neue Section mit Tabelle aus `monitor/source_reliability.json`.
   - Visualisierung: Tier-Bar (insufficient → low → watch → neutral → trusted), n-Bar mit Threshold-Marker (n=20).
   - **WICHTIGER UI-Hinweis**: Tile muss „**X/8 Sources sufficient**" als Header zeigen, NICHT die einzelnen Wilson-Lower-Bound-Werte als Hauptzahl. Operator soll auf einen Blick sehen: „der Lernstack hat genug Source-Daten für seine Decisions" oder nicht.

4. **P3: Threshold-Re-Review** (~30min, nach 7d-Beobachtungsfenster)
   - Wenn nach Source-Mapping-Fix immer noch <2 Sources sufficient: dann ist `_MIN_N_FOR_DEMOTE=20` zu hoch und Lockerung auf 10–15 diskussionswürdig.
   - Vorher nicht.

---

## 5. NICHT-Decisions

- ❌ `_MIN_N_FOR_DEMOTE` von 20 auf 10 senken — nicht jetzt, Source-Mapping-Fix zuerst.
- ❌ `_DEFAULT_WINDOW_DAYS` von 90 auf 180 erweitern — würde unmapped-Alert-Anteil nicht verbessern.
- ❌ Inconclusive in den Tier-Algorithmus aufnehmen — eigene Analyse, separates ADR (Wilson-Lower ist für Hard-Labels designed).

---

## 6. Provenance

- **Datenquelle:** Pi `/home/kai/ai_analyst_trading_bot/monitor/source_reliability.json` (2.3K, generated 2026-05-16 12:21 UTC).
- **Vergleichsquelle:** Pi `/home/kai/ai_analyst_trading_bot/artifacts/alert_outcomes.jsonl` (3.7M, last write 2026-05-17 23:26 CEST).
- **Code:** `app/learning/source_reliability.py`, `scripts/source_reliability_recalc.py`.
- **Related Memos:** `re_entry_decision_2026-05-16.md` §3.5 (Inconclusive-Rate als Sekundär-Gate), `re_entry_adr_cluster_2026-05-17.md` ADR-2 (Tier-Verteilung defer).
