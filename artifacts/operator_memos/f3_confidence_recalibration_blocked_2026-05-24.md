# F3 — Confidence-Recalibration BLOCKED (Persistence-Lücke, 2026-05-24)

**Auftrag:** F3 aus Sprint-Plan [[kai-dispatch-filter-root-befund-20260524]] §6.
**Stichtag:** 2026-05-24, Read-only-Inspection.
**Befund:** Sprint kann nicht ohne V-0 Schema-Patch durchgeführt werden.

---

## Befund

F3 (Confidence-Threshold-Recalibration) braucht historische `directional_confidence`-Werte pro analysiertem Dokument, um eine 90d-Outcome-zu-Confidence-Korrelations-Analyse zu fahren. Aktuelle Schwellen:
- `MIN_DIRECTIONAL_CONFIDENCE_BULLISH = 0.8`
- `MIN_DIRECTIONAL_CONFIDENCE_BEARISH = 0.95`

Die Werte sollten gegen Outcomes geprüft werden (z.B. "ist Precision bei `0.7 <= confidence < 0.8` vergleichbar zu `0.8 <= confidence < 0.9`?"). Senken der Schwelle wäre nur dann gerechtfertigt.

**Persistence-Lücke:**
- `directional_confidence` ist im LLM-Output (`app/analysis/base/interfaces.py:34`), im Pipeline-Domain (`app/core/domain/document.py:251`) und im AlertMessage (`app/alerts/base/interfaces.py:35`).
- Wird durchgereicht bis `evaluate_directional_eligibility` und dort als Gate verwendet.
- **Wird ABER NIRGENDS persistiert:**
  - `canonical_documents`-Schema (SQLite/SQLAlchemy) hat kein `directional_confidence`-Feld
  - `AlertAuditRecord` (`app/alerts/audit.py:55`) hat es nicht
  - `BlockedAlertRecord` (`app/alerts/blocked_audit.py:46`) hat es nicht
  - Auch nicht in `alert_outcomes.jsonl`, `decision_journal.jsonl`, `bayes_confidence_audit.jsonl`

Ergebnis: Es gibt **keinen historischen Datensatz** mit `(directional_confidence, outcome)`-Paaren. F3 als "Recalibration aus Historie" ist datentechnisch nicht durchführbar.

---

## Drei Pfade für F3-Sprint

### Pfad A — V-0 Schema-Patch + ab heute Data-Sammlung (~1d Patch + 2-3w Wartezeit + ~2d Analyse)
- `canonical_documents` Migration: add column `directional_confidence FLOAT NULL`
- `AlertAuditRecord` + Persist-Code: ergänzen + audit-Stream backfill (für neue Alerts)
- `BlockedAlertRecord`: ergänzen — dann sind auch blocked-by-confidence Cases auswertbar
- Sammle 2-3 Wochen, dann F3-Analyse
- **Aufwand gesamt:** ~3d Patch-Arbeit + Daten-Wartezeit ~3w

### Pfad B — Replay-Analyse via Re-Klassifikation (1-2d, kostenintensiv)
- Sample 200 documents aus Q2 (mit hit/miss outcomes)
- Re-run gegen aktuellen LLM-Pfad
- Vergleiche aktuelle confidence mit outcome
- Token-Kosten: ~200 × ~$0.02 = ~$4, dazu Zeit
- **Risiko:** LLM-Antworten nicht-deterministisch; Re-Run gibt nur Indikation, keine produktiv-historische Wahrheit
- **Aufwand:** ~1-2d, niedrig empirisch valide

### Pfad C — Indirekt via blocked_alerts-Stream (~0.5d, schwach)
- `blocked_alerts.jsonl` hat 105 `low_directional_confidence`-Blocks in 8d
- ABER der konkrete `directional_confidence`-Wert ist NICHT im JSONL-Record
- Wir wissen also nur "confidence < 0.8" (bei bullish), nicht den Wert.
- Kann nur sagen "X% aller direktionalen Bullish bekommen confidence < 0.8 -> dispatched-Anteil ist Y%"
- **Erkenntniswert:** sehr begrenzt — beantwortet die eigentliche Frage (sind Schwellen richtig) nicht.

---

## Empfehlung

**Pfad A (V-0 Schema-Patch + Sammlung), aber explizit als 30.05.-Decision-IRRELEVANT markieren.**

Begründungen:
1. F3 ist die teuerste der vier Sprints (F1-F4) und liefert nur Marginal-Gewinn (wenige pp Schwellen-Anpassung).
2. F1-Merge bringt die Hauptmasse der Operator-Material-Verfügbarkeit zurück (Recovery 93% im 14d-Sample).
3. Pfad A ist als V-0 (Schema + Persist) deploybar bis ~28.05.; Daten-Sammlung läuft 2-3 Wochen, F3-Analyse erst Ende Juni.
4. Operator-Time-Pressure 30.05. ist mit F1+F4 abgedeckt; F3 kann nach 30.05.-Decision in Ruhe laufen.

**Konkrete V-0-Spec:**
- Migration `0023_add_directional_confidence.py` für `canonical_documents`
- `AlertAuditRecord` + `BlockedAlertRecord` Schema-Erweiterung
- `service.py` Persist-Pfad ergänzen
- Tests für Schema-Round-Trip
- Empirischer F3-Run frühestens 2026-06-15 (>=3 Wochen Sammlung)

---

## Decision-Implikation für 30.05.

F3 hat **keinen Einfluss auf die 30.05.-Priority-Scoring-Decision**. Die Decision ist auf F1-Recovery-Mass und F2-D-142-Bestätigung gestützt. F3 ist eine Folge-Optimierung, kein Decision-Pflichtbestandteil.

Empfehlung: F3 aus dem 30.05.-Decision-Scope nehmen, V-0-Spec als P2-Folge-Sprint anlegen.

---

## Cross-Links

- [[kai-dispatch-filter-root-befund-20260524]] §6 F3 — dieses Memo dokumentiert den Persistence-Block
- `app/alerts/eligibility.py:259-260` — aktuelle Schwellen
- `app/storage/migrations/versions/` — nächste Migration-Nummer für V-0-Patch
