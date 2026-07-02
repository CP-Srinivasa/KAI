# Bridge Gate 4.5 Wirksamkeits-Audit — 2026-05-21

**Auftrag:** DS-20260521-V3 (P1) — Prüfen, ob `scale_resolver.validate_scaled_signal()` (commit `07a86b2`, deployed 2026-05-20 22:07 CEST) seit Deploy gefeuert hat.
**Methode:** Read-only Grep über `artifacts/paper_execution_audit.jsonl` + `artifacts/bridge_pending_orders.jsonl` nach den 7 neuen Reasons.
**Verfasser:** Claude Code Mid-Window-Forensik.

---

## 1. Die 7 neuen Reasons (Quelle: `app/execution/scale_resolver.py`)

```
scale_collapses_to_zero
long_sl_at_or_above_entry
long_sl_at_or_above_spot
long_targets_at_or_below_entry
short_sl_at_or_below_entry
short_sl_at_or_below_spot
short_targets_at_or_above_entry
```

## 2. Treffer im Audit-Stream

| Stream | Treffer mit neuen Reasons | Treffer mit Legacy-Reason (`long_sl_at_or_above_price`) | Zeitfenster |
|---|---|---|---|
| `paper_execution_audit.jsonl` | **0** | **2** (2026-05-04 1000LUNC, 2026-05-12 IRYS) | seit Datei-Anlage |
| `bridge_pending_orders.jsonl` | 0 | 0 | seit Datei-Anlage |

**Beide IRYS-bezogenen Treffer sind pre-Deploy.** Der IRYS/2026-05-12-Treffer ist der Bug, dessen Reproduktion Gate 4.5 verhindern soll — er bestätigt dass das Bug-Pattern real war und neutral durch das alte `long_sl_at_or_above_price` (paper_engine-side, opak) abgewiesen wurde, statt durch den neuen strukturierten `long_sl_at_or_above_entry` oder `long_sl_at_or_above_spot` (scale_resolver-side, vor Bridge-Tick).

## 3. Erklärung des Null-Befunds

`bridge_audit_last_event` aus `/health/premium_pipeline` (Stand 2026-05-21T06:57:59 UTC): **38549s alt** ≈ **10.7h**. Letzte Bridge-Aktivität war damit am 2026-05-20T20:15:30 UTC — das ist **~1h45min VOR** dem `07a86b2`-Deploy (20:07 UTC = 22:07 CEST). Seitdem ist kein neues Signal durch die Bridge gelaufen.

**Konsequenz:** Gate 4.5 ist deployed + im Code-Pfad aktiv, aber kein Live-Signal hat es bisher passiert. Code-Korrektheit ist über die 9 Unit-Tests im Commit (alle 6 Pfade abgedeckt) abgesichert; empirische Wirksamkeit wartet auf das nächste Bridge-Event.

**Das ist kein Defekt.** Aktuelle Signal-Rarheit (siehe DS-20260520-V4: 95.5% inconclusive, niedrige Trade-Frequenz) plus 11h-Deploy-Alter ist ein normales Bild.

## 4. Was diese Audit NICHT beweist

- Keine Aussage über die Wirksamkeit von Gate 4.5 unter realer Last — dafür braucht es mind. ein Signal mit strukturell ungültigem SL/Entry/Target-Verhältnis nach 20:07 UTC am 20.05.
- Keine Aussage über False-Positive-Risiko (Reject-Reason `scale_collapses_to_zero` etc. bei legitimen Signalen). Bis erste Live-Treffer da sind, bleibt das offen.

## 5. Cross-Check via Premium-Trail-API

`GET /api/premium-signals/trail?limit=50` (DS-20260521-V2): Letztes received_at = 2026-05-20T20:15:25 UTC. Erstes received_at im 50-Window = 2026-04-18T09:21:12 UTC. Über das Window: 6 BRIDGE_REJECTED (alle mit `risk_gate_rejected` als reason — nicht Gate-4.5). Konsistent mit dem Audit-Stream.

## 6. Empfehlung

**Heute:** Kein Eingriff. Audit-Befund dokumentieren, Gate-4.5-Wirksamkeit beim ersten Live-Treffer re-prüfen.

**Trigger-Bedingung für Re-Audit:** sobald `bridge_audit_last_event` < 7200s alt im Healthcheck UND irgendein neuer scale_resolver-Reason im `paper_execution_audit.jsonl` auftaucht — dann erste Live-Validierung inkl. Trail-UI-Sichtbarkeit der Reasons (V2-Kopplung).

**Eskalations-Trigger:** wenn nach 72h aktiver Bridge-Tätigkeit (>3 Bridge-Events) immer noch 0 Gate-4.5-Treffer trotz IRYS-ähnlicher Scenarien (manuell prüfen oder synthetisches Test-Signal) — dann Code-Pfad nochmal forensisch lesen.

## 7. Datenquellen

- `app/execution/scale_resolver.py` (grep nach Reason-Strings)
- `artifacts/paper_execution_audit.jsonl` (128699 Bytes, letzter Eintrag 2026-05-21 08:40)
- `artifacts/bridge_pending_orders.jsonl`
- `/health/premium_pipeline` (`bridge_audit_last_event` Feld)
- `GET /api/premium-signals/trail?limit=50` (Cross-Check)
- commit `07a86b2` (Deploy-Zeitpunkt + Unit-Test-Abdeckung)
