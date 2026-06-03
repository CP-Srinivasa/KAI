# NEO-P-002-r3 — Real SignalGenerator Shadow Path (Spezifikation)

**Status:** SPEC (Operator-Entscheidung 2026-06-03: *Spezifikation jetzt,
Implementation nach dem 04.06.-Report-Fenster*). Kein Runtime-Code, kein Deploy
mit diesem Dokument.

## Kontext

V1 (PR #137 + #140) hat die **Messhygiene** repariert: der Shadow-Report trennt
jetzt `canary_probe` und unverifizierte Vor-V1-Records (`unattributed`) vom
echten Signal und liefert ehrlich `real_resolved=0 → INSUFFICIENT_DATA`. Damit
ist die Messpipeline nicht mehr der Hauptverdächtige.

Der neue Hauptverdächtige ist der **Funnel davor**: Es gibt aktuell **keine**
echten `autonomous_generator`-Kandidaten im Ledger, weil der autonome Loop unter
`PAPER_CRON_PROFILE=canary_bullish` nur die hartkodierte Control-Plane-Probe
fährt. `real_resolved=0` heißt deshalb „noch nicht gemessen", nicht „keine Edge".

## Ziel

Echte autonome `SignalGenerator`-Kandidaten unter `EXECUTION_ENTRY_MODE=disabled`
**read-only messbar** machen — ohne irgendeinen Execution-Pfad zu aktivieren.

## Nicht-Ziele (hart)

- kein `paper` / `probe` / `live`
- kein Risk-Gate `enforce`
- keine Änderung am Directional-/Sentiment-Gate (`service.py:61`)
- keine Strategieparameter-Änderung, keine TP/SL-Änderung
- kein Premium-Bridge-Hook (sofern nicht explizit separat geplant)
- kein Auto-Promote, keine `entry_mode`-Änderung durch Code

## Schalter (fail-safe, Default OFF)

```
EXECUTION_SHADOW_REAL_GENERATOR=false   # Default — Status quo, kein realer Generator im Shadow-Pfad
EXECUTION_SHADOW_REAL_GENERATOR=true    # nur nach explizitem Operator-Sign-off
```

Bei `true`: der echte `SignalGenerator` läuft im Shadow-Pfad und schreibt
hypothetische Kandidaten (`source="autonomous_generator"`). Er darf **nur
beobachten** — keinen Ausführungspfad berühren.

## Ledger-Pflichtfelder (zusätzlich zu V1-`ShadowCandidate`)

| Feld | Werte / Zweck |
|---|---|
| `source` | `autonomous_generator` (neu) — feeds `REAL_SOURCES` der V1-Report-Allowlist |
| `candidate_kind` | `signal_candidate` \| `gate_candidate` \| `would_have_traded` \| `rejected_candidate` |
| `source_stage` | `signal_generator` \| `sentiment_gate` \| `risk_gate` |
| `signal_origin` | Herkunft des auslösenden Signals |
| `document_id` / `alert_id` | falls vorhanden |
| `priority` | `recommended_priority` |
| `sentiment` | Sentiment-Label |
| `directional_state` | `bullish` \| `bearish` \| `mixed` \| `neutral` |
| `confidence_score` | echter Generator-Confidence (NICHT konstant) |
| `bayes_confidence_score` | falls vorhanden |
| `score_source` | woher der Score stammt |
| `gate_decision` | Gate-Verdikt |
| `gate_reason_codes` | Liste |

## Funnel-Zähler (Pflicht — der Ledger darf nicht schweigen)

Wenn der Generator nichts Directional liefert, soll ein Funnel-Zähler geschrieben
werden, damit aus `real_resolved=0` ein **erklärbarer** Befund wird:

```
raw_alerts
priority_rejected
sentiment_rejected
non_directional
directional_accepted
shadow_candidates_written
```

## Report-Buckets

Der Report (`build_shadow_report`) trennt sauber:

- `real_resolved` — nur `REAL_SOURCES` (jetzt inkl. `autonomous_generator`)
- `canary_probe_resolved` — Control-Plane-Probe
- `unattributed_resolved` — Vor-V1-Legacy ohne Provenance
- `rejected_funnel` — die Funnel-Zähler oben

`real_resolved=0` ⇒ `INSUFFICIENT_DATA`, **nie** `EDGE_NEGATIVE`. Legacy/
unattributed bleibt außerhalb von Headline und `primary_class`.

## Akzeptanzkriterien

1. `entry_mode` bleibt `disabled`.
2. `EXECUTION_SHADOW_REAL_GENERATOR` Default `false`.
3. Bei `true` schreibt der echte Generator Shadow-Kandidaten mit `source=autonomous_generator`.
4. Keine Paper-Fills, keine Positionen, keine Orders.
5. `non_directional` / `priority_rejected` / `sentiment_rejected` werden als Funnel-Metriken sichtbar.
6. Reports trennen `real_resolved`, `canary_probe`, `unattributed`, `rejected_funnel`.
7. `real_resolved=0` führt zu `INSUFFICIENT_DATA`, nicht zu `EDGE_NEGATIVE`.
8. Legacy/`unattributed` bleibt außerhalb von Headline/`primary_class`.
9. Tests beweisen **No-Execution** auch bei validem realem Signal (Fill-/Order-/Position-Count == 0).
10. Pi-Deploy nur nach CI-grün **und** Operator-Sign-off.

## No-Execution-Invariante (Test-Pflicht)

Ein Test muss zeigen: bei `EXECUTION_SHADOW_REAL_GENERATOR=true` **und** einem
validen, directional, high-priority echten Signal entsteht ein Shadow-Kandidat,
aber `order_created == False`, `position_count == 0`, kein `order_filled`-Event.
Das ist die härteste Invariante des Sprints.

## Interpretation des 04.06.-Reports (Baseline)

Der 04.06.-Report ist die **ehrliche Baseline** vor r3:

- `real_resolved = 0` → kein echtes Signal-Sample, **kein Edge-Urteil**.
- `unattributed_resolved > 0` → Legacy/Altbestand, **nicht** für `primary_class`.
- `primary_class = INSUFFICIENT_DATA` → korrektes Ergebnis, kein Fehler.
- canary/unattributed → nur Pipeline-/Legacy-Diagnose, **keine** Signal-Evidence.

Der eingefrorene negative Verdict aus dem alten Paper-Experiment bleibt als
**Warnsignal** bestehen, darf aber nicht als Beweis gegen den aktuell *nicht
gemessenen* `autonomous_generator` überdehnt werden.

## Reihenfolge

1. **Jetzt:** diese Spec. Kein Runtime-Code.
2. **04.06.:** Report laufen lassen, als Baseline-Artefakt sichern.
3. **Danach:** r3 implementieren (Default-OFF, Funnel-Zähler, harte No-Execution-Invariante), CI-grün, Operator-Sign-off, dann Pi.

## Cross-Ref

- V1: PR #137 (Canary-Attribution) + #140 (unattributed-Quarantäne), `app/observability/shadow_candidate_ledger.py` (`REAL_SOURCES`).
- `app/orchestrator/trading_loop.py` (`_record_shadow_candidate`, `build_loop_trigger_analysis`).
- Bleed-Breaker: `app/risk/promotion_gate.py`.
