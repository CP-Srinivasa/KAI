# G8 Akt 2 — Abbruch / Invalidierung

```
prereg_id                    ebbf451f432cbc80
prereg_name                  operator_back_edge_v2
registered_at_utc            2026-09-01T13:00:14Z
window_start_utc             2026-09-01T14:00:00Z
window_end_utc               2026-09-15T14:00:00Z
invalidation_decided_at_utc  2026-09-01T20:38:51Z

decision_state               INVALIDATED_PENDING_SUCCESSOR
substantive_verdict          NONE
reason                       MEASUREMENT_INSTRUMENT_DEFECT_DISCOVERED_POST_T0
```

## Der Grund

**Das Instrument, das die gemessene Population erzeugt, ist selbst defekt — und der
Defekt wurde bei offenem Fenster bewiesen.**

G8 misst, ob ein **zugestellter** Health-Befund eine passende Operator-Handlung
auslöst. Der Health-Check hat nach T0 **15 mal** eine altersblinde
Annotations-Warnung in genau diese Population geschrieben. Replay zum Zeitpunkt
jeder einzelnen Emission:

| Emission (UTC) | TOTAL | NOT_DUE | GRACE | **DUE** |
|---|---:|---:|---:|---:|
| 14:00:26 | 44 | 44 | 0 | **0** |
| 14:15:28 | 44 | 42 | 2 | **0** |
| 15:30:42 | 38 | 29 | 9 | **0** |
| 16:30:00 | 29 | 29 | 0 | **0** |
| 17:30:18 | 28 | 1 | 27 | **0** |

Bei **jeder** Emission nach T0 war die Zahl der tatsächlich überfälligen
Annotationen **null**. Der Auto-Annotator darf einen Alert seine ersten 4 h nicht
anfassen und läuft alle 6 h; die Warnung zählte frisch zugestellte Alerts ab
Ankunft als Rückstand. Über 60 Tage: 199 Warnstunden, echte Überfälligkeit **nie
über 5** gegen ein `>20`-Gate.

Die Population ist damit keine Stichprobe von Befunden, auf die der Operator hätte
reagieren sollen — sie ist mit Alarmen aufgefüllt, die das Instrument nie hätte
auslösen dürfen.

**Das ist ausdrücklich NICHT das Argument „#848 hat einen anderen Hash".** Ein
wartender Branch verändert Produktion nicht. Das Argument ist: das **laufende**
Instrument wurde gemessen und für falsch befunden.

## Was NICHT getan wurde

```
outcome_inspected             false
evaluator_executed            false
acted_count_inspected         false
interim_result_taken          false
substantive_outcome_evaluated false

emitted_count_inspected       true
emitted_inspection_scope      instrument_defect_proof_only
```

Kein Evaluator gelaufen, kein `acted`-Count gelesen, kein Zwischenergebnis genommen.
Der `emitted`-Strom wurde ausschließlich insoweit eingesehen, wie der Nachweis des
Instrumentendefekts es verlangte (15 Emissionen der altersblinden Warnung); dieses
Ergebnis dient allein dem Verwerfen des Fensters, nie seiner Deutung.

> **Korrektur 2026-09-02.** Die erste Fassung führte `emitted_count_inspected false`
> und „Es wurde keine Zahl gelesen." Das stand im Widerspruch zu
> `post_t0_emissions_observed = 15`, worauf sich die Begründung stützt. Ein
> Instrumentendefekt lässt sich nur nachweisen, indem man ansieht, was das Instrument
> ausgesendet hat — das ist legitim, die Formulierung war es nicht. Korrigiert wurde
> die **Behauptung**, nicht die Entscheidung: Zeitpunkt, Grund, `substantive_verdict`
> und alle Replay-Zahlen sind unverändert.
> Vorherige Fassung: `a6974b985746272e8ec7ac08d65fe5bd158f4fa4ee6ffd075c02ba5537fcb727`.

## Weitere Instrument-Defekte, die im offenen Fenster bewiesen wurden

- Timer-Karte meldete „10 OK" aus einem Snapshot, der bei einem Tages-Producer
  gegen ein flaches 2h-Budget **22 von 24 Stunden** strukturell veraltet war;
  installiert sind 56 Timer, nicht 10.
- 67 Envelopes standen als `position_open`, obwohl ein `position_closed` existierte.
- Das Label `HIGH-CONVICTION` wurde vergeben, während der gemessene Tier-Lift
  −7,4 pp beträgt.
- Fünf Dashboard-Prozentwerte ohne genannte Population oder Nenner.

## Population-verändernde Änderungen in STAB-FINAL (#848)

- `app/alerts/health_check.py` — Annotations-Block wird altersbewusst und verändert
  damit, **wie viele** Health-Befunde überhaupt entstehen.
- `app/alerts/health_check.py` — YouTube-Grund-SQL trennt Altbestand von echtem
  Defekt; ändert Wortlaut und Schweregrad eines zugestellten Befunds.
- `app/alerts/health_notify.py` — Per-Finding-Recovery fügt eine Klasse
  zugestellter Nachrichten hinzu, die es im Fenster nicht gab.

## Nachfolger

```
replacement_pending  true
replaced_by          null
watcher_id           null
cadence              null
next_review_utc      null
MATURITY_SPEC        none
```

**Keine Platzhalter-ID.** Die Nachfolger-ID wird aus dem **deployten** Zustand
abgeleitet (Mainline-SHA + `evaluator_sha256` + `health_notify_sha256` +
effektiver Config-SHA) und ist vor dem Deploy nicht bekannt. #843 hat bereits
gezeigt, warum eine vorausberechnete ID gefährlich ist.

Einzig zulässiger Folgeübergang:

```
INVALIDATED_PENDING_SUCCESSOR
  -> INVALIDATED_BEFORE_MEASUREMENT   (dann, und erst dann: replaced_by = <NEW_ID>)
```

`invalidated_at_utc` bleibt **2026-09-01T20:38:51Z** und wird nicht auf die
Deploy-Zeit verschoben. Der Entscheid wurde jetzt getroffen und gehasht.

## Vorgänger

```
f0803d911744e0c2   INVALIDATED   POST_T0_INSTRUMENTATION_CONTAMINATION   verdict NONE
```

---

Ab **2026-09-01T20:38:51Z** gilt Akt 2 unabhängig vom späteren Registerzustand
nicht mehr als auswertbarer Versuch.
