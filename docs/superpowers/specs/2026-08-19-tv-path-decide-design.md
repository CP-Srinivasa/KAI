# TV-Pfad: reparieren, messbar machen, entscheiden

Status: **Entwurf zur Freigabe** · Datum: 2026-08-19 · Operator-Review eingearbeitet

## Warum

Der TradingView-Pfad hing zweimal wochenlang tot am Operator: Alerts laufen ab,
niemand merkt es. Die naheliegende Antwort — „automatisieren, damit der Operator
das nicht mehr von Hand tun muss" — wäre falsch herum. Gemessen liefert der Pfad
seit Epoche `paper_v2_attested`:

| Größe | Wert |
|---|---|
| empfangene TV-Signale | 3.407 |
| davon promotet | **2** |
| zurechenbare Paper-Closes | n=29 |
| brutto / Gebühren / netto | −30,99 / 51,32 / **−82,31 USD** |

Der Verlust ist fast vollständig Gebühr. Und die zugrunde liegende Bedingung ist
nicht ungetestet: die Edge-Discovery hat auf 1h/180d über 5 Symbole zwölf Regeln
geprüft, **0 Survivors**, darunter

```
rsi_oversold_long      −23,27 bps netto
rsi_overbought_short   −23,15 bps netto
tsmom_vol_scaled       −16,10 bps netto
bester von zwölf       −13,03 bps  (tsmom_adx_confirmed)
```

Die RSI-Komponente der Operator-Alerts gehört damit zu den **schlechtesten** der
Familie. Neu ist ausschließlich die **Konjunktion** mit einem Volumen-Spike.

Deshalb: erst beweisen, ob die Idee einen messbaren Wert hat — dann erst
Infrastruktur. Nicht automatisieren, weil etwas lästig war.

## Zweiter Befund, unabhängig vom TV-Pfad

`kai-tv-auto-promote.timer` meldet `enabled` **und** `active`, hat aber
`NextElapseUSecMonotonic=infinity` und feuerte zuletzt am **2026-07-12 12:39:45**
— fünf Wochen tot, von `systemctl --failed` nicht gezeigt, versteckt hinter dem
toten Ingest.

Die Fehlbedingung ist präzise (live nachgemessen, nicht vermutet): die Unit hat
**nur monotone Trigger** (`OnBootSec` + `OnUnitActiveSec`). `OnUnitActiveSec`
verankert sich am letzten **Service**-Lauf. Wird der Timer lange nach dem Boot
neu gestartet, während der Service seit langem nicht lief, existiert kein Anker —
und es entsteht nie wieder ein Termin.

Gegenprobe: `kai-oracle-earnings-booking.timer` und `kai-premium-healthcheck.timer`
haben dieselbe Bauform und wurden am 18.08. ebenfalls neu gestartet — beide sind
**gesund** (endliche Next-Elapse, Läufe am 19.08. um 11:35 bzw. 11:50), weil ihr
Service einen frischen Anker hatte. Die Bauform allein genügt also nicht; es
braucht den Neustart ohne Anker.

**Eigener Fehler, hier korrigiert:** `Persistent=true` wirkt laut systemd
ausschließlich bei `OnCalendar=`. In #729 habe ich die Kadenz-Senkung von
`kai-oracle-earnings-booking` damit begründet, dass `Persistent=true` einen
verpassten Lauf nachholt — bei einem rein monotonen Timer ist das **wirkungslos**,
und die Begründung steht als Test (`test_missed_runs_are_still_caught_up`) und im
Unit-Kommentar. Falsche Begründungen im Repo sind schlimmer als fehlende; Teil A
korrigiert beides.

---

## A — Timer-Contract statt Einzelreparatur

### A1 Betroffene Units auf Kalender-Trigger umstellen

`OnCalendar=*:0/5` (alle fünf Minuten zur Wanduhr) plus `Persistent=true`
(das dort **wirkt**). **Kein** permanenter zusätzlicher Bootstrap-Trigger in derselben
Unit — bei `OnCalendar` + `Persistent` holt systemd den verpassten Lauf beim
Aktivieren nach, ein zweiter Trigger erzeugt nur Doppelaktivierungen.

Beim Rollout **ein einziger** kontrollierter Bootstrap-Smoke: den Service gezielt
einmal starten, Ergebnis prüfen. Danach ausschließlich der Timer.

Ebenfalls in A: die falsche `Persistent`-Begründung bei
`kai-oracle-earnings-booking` korrigieren und die Unit auf `OnCalendar=hourly`
ziehen, damit die Aussage wieder stimmt. Das ändert die Semantik bewusst von
„60 min nach dem letzten Lauf" auf „zur vollen Stunde" — bei einer
Buchhaltung über bereits settled Rechnungen ohne Belang, und erst dadurch
greift `Persistent=true` wie im Kommentar behauptet.

### A2 Timer-Taxonomie

Der Wächter darf **nicht** stumpf auf alle 63 Timer angewandt werden — nicht
jeder Timer muss einen nächsten Termin besitzen. Jede Unit bekommt eine
deklarierte Klasse:

| Klasse | Bedeutung | harte Invariante |
|---|---|---|
| `RECURRING` | soll dauerhaft in fester Kadenz laufen | ja |
| `DEADLINE` / `ONE_SHOT` | einmalig bzw. bis zu einem Stichtag | nein |
| `CONDITIONAL` | nur unter Vorbedingung aktiv | nein |
| `MANUAL` | wird bewusst von Hand ausgelöst | nein |

### A3 Zwei unabhängige Invarianten

**Scheduleability** — soll wiederkehrend laufen, besitzt aber keinen zukünftigen
Trigger:

```
klasse == RECURRING  AND  enabled  AND  active
AND  kein zukünftiger Trigger        →  FINDING
```

Systemd führt für Kalender- und monotone Timer **getrennte** Next-Elapse-Werte
(`NextElapseUSecRealtime` bzw. `NextElapseUSecMonotonic`). Der Wächter muss beide
korrekt beurteilen: leer/`infinity` in **beiden** heißt „kein Termin".

**Cadence** — letzter erfolgreicher Lauf liegt deutlich länger zurück als
erwartete Kadenz plus definierte Grace-Period. Fängt den Fall, in dem formal ein
Termin existiert, die Pipeline aber trotzdem nicht arbeitet.

### A4 Tests

Der bestehende Timer-Konventionstest pinnt bisher vor allem das frühere
`Requires=`-Anti-Pattern. Er wird um einen **Recurring-Timer-Contract** erweitert:
jede als `RECURRING` deklarierte Unit muss einen Kalender-Trigger besitzen, und
die Klassendeklaration muss für jede Unit vorhanden sein.

### A5 Deploy-Hinweis

Seit **#730** synchronisiert `kai_deploy.sh` geänderte `.timer`-Units automatisch
und startet sie neu; geschützte Writer laufen dabei durch den Freeze-Guard. A ist
damit **keine reine Codeänderung** — der Timer-Restart gehört als *erwartete
Mutation* in den OPS-Claim, samt Vorher-Erfassung der Timer- und Writer-Zustände
und Nachher-Beobachtung mehrerer realer Auslösungen.

---

## B — Volumen in die bestehende Research-Pipeline, kein neuer Generator

`OHLCV.volume` existiert bereits, wird aber nicht bis `FeatureRow` durchgereicht.
Damit ist ein lokaler Signalgenerator die **falsche** Architektur. Stattdessen:

```
vorhandene Rohdaten → bestehende Feature-Pipeline
                    → bestehender Hypothesenrunner
                    → bestehende BH-FDR-Korrektur
```

### B1 `volume_z_20` — Definition, vor dem ersten Lauf eingefroren

```
x_t          = log1p(volume_t)
volume_z_20  = ( x_t − mean(x[t-20 … t-1]) ) / std(x[t-20 … t-1])
```

Baseline sind die **20 vorherigen abgeschlossenen** Kerzen; die aktuelle Kerze
geht **nicht** in ihre eigene Referenzverteilung ein.

`log1p`, weil Volumen stark rechtsschief ist und einzelne Spikes Mittelwert und
Standardabweichung sonst massiv verzerren. **Keine** nachträgliche Auswahl
zwischen raw/log und 10/20/30-Fenstern nach Ergebnislage — eine Definition,
begründet, versiegelt.

### B2 Zeitsemantik

| Fall | erlaubt |
|---|---|
| Signal am Candle-Close | `volume_t` darf verwendet werden |
| Signal intrabar | nur vollständig abgeschlossene vorherige Kerze |

Unvollständiges Intrabar-Volumen erzeugt sonst ein kaum reproduzierbares Feature.

### B3 Fehlende Werte

- Volumen fehlt → `NaN` / *unavailable*, **nicht** `0`
- `std == 0` → *unavailable*, **nicht** künstlich `z=0`
- Venue und Timeframe müssen eindeutig sein

### B4 Tests

Nicht „`volume_z_20` existiert", sondern: **zeitlich korrekt, deterministisch,
source-stabil, ohne Lookahead**. Ein Lookahead-Test muss rot werden, wenn die
aktuelle Kerze in die Baseline gerät.

### B5 Die Hypothese

Eine neue Regel in `default_hypotheses()`: RSI-Crossover **und** Volumen-Spike als
Konjunktion. Sie läuft durch **dieselbe** BH-FDR-Familie wie die bestehenden
Kandidaten — der Runner sagt das selbst: *„no free pass"*.

**Kandidatenfamilie vor dem Lauf fixieren:** exakt N Kandidaten, keine Kandidaten
nach Sichtung entfernen, keine Variante nachschieben, kein Threshold-Tuning nach
Resultaten. Bereits versiegelte historische Verdikte werden **nicht** rückwirkend
umetikettiert, nur weil ein dreizehnter Test hinzukommt — das neue Experiment
bekommt seine **eigene eingefrorene** FDR-Familie.

---

## C — Präregistrierung, ausschließlich forward/out-of-sample

### C1 Der Bias, den wir benennen müssen

Die Kombination RSI+Volumen wurde **nach Sichtung** vorhandener Resultate
motiviert (RSI ≈ −23,3 bps, `tsmom_vol_scaled` ≈ −16,1 bps). Das ist legitime
Hypothesengenerierung — aber dieselbe historische Population darf anschließend
**nicht** als konfirmatorischer Beweis dienen.

```
historische Daten  →  Exploration / Hypothesengenerierung
Daten NACH Versiegelung  →  konfirmatorische Out-of-Sample-Prüfung
```

Wird RSI+Volumen auf denselben historischen Daten plötzlich positiv, ist das
interessant — aber **kein** belastbarer PASS.

### C2 Erwartung ≠ Urteil

- **PRE-RUN EXPECTATION:** wahrscheinlich `NOT_MET`. Vorher dokumentiert, damit
  hinterher niemand behaupten kann, das Ergebnis sei ohnehin offensichtlich gewesen.
- **FORMAL VERDICT:** ausschließlich mechanisches Gate. Die Erwartung hat **null**
  Einfluss auf das Urteil.

### C3 Offene Definitionsfrage — vor Schritt 3 zu klären

Die bestehenden Regeln sind **Level**-Regeln (`rsi_14 < 30`), der Operator hat
aber einen **Crossover** genannt. Das ist nicht dasselbe: „RSI liegt unter 30"
feuert in jeder Kerze des Zustands, „RSI kreuzt 30 von unten nach oben" feuert
genau einmal am Übergang. Unterschiedliche Trefferzahlen, unterschiedliche
Haltedauern, unterschiedliche Kostenlast — und `rsi_oversold_long` (−23,27 bps)
misst die Level-Variante, nicht die Crossover-Variante.

Welche der beiden gemeint ist, muss der Operator entscheiden, **bevor** in
Schritt 3 versiegelt wird. Beide zu testen und danach die bessere zu wählen,
wäre genau die Nachoptimierung, die C5 ausschließt.

### C4 Was die Präregistrierung festnagelt

exakte RSI-Crossover-Definition · exakte Volume-Spike-Definition ·
`volume_z_20`-Formel · Timeframe · Venue/Quelle · Forward-OOS-Startzeit ·
Endzeit · Sample Unit · `n_min` · Umgang mit missing/inconclusive · Kostenmodell ·
Slippage · primäre Kennzahl · ökonomische Mindesthürde · BH-FDR-Familie ·
Alpha/FDR-Level · Evaluator-Version · Code-SHA · Datenschnitt · keine
Nachoptimierung.

### C5 Fristende ist kein FAIL

```
Deadline erreicht,  n = 27,  n_min = 100
   →  INCONCLUSIVE / NOT_MATURE
   →  NICHT NOT_MET
```

Diese Lektion war bei ND-v2 teuer. Versiegelt werden ein **fixes OOS-Fenster**
und ein **Mindest-n**. Verdikt genau **einmal** nach Fensterende, sofern
`n ≥ n_min`. Kein optional stopping bei hübschem oder hässlichem Zwischenstand.

---

## Konsequenzen — beide Richtungen kontrolliert

### NOT_MET → Abhängigkeitsgraph prüfen, dann kontrolliert einstellen

Vor dem Abschalten der manuellen Alert-Pflege prüfen: Nutzen andere
KAI-Komponenten den Alert-Pfad? Dient er Provenance/Monitoring? Fließen daraus
Vergleichsdaten? Existiert eine andere validierte Hypothese darauf?

Nur wenn überall „nein": `deprecated` → keine neuen manuellen Alerts →
bestehende Evidenz archivieren → Ingress/Health **nicht** sofort zerstören →
kurze Shadow-off-Phase → dann entfernen. Reversibel und auditierbar.

### PASS → noch kein Generator

Ein statistischer PASS beantwortet „gibt es dort wahrscheinlich ein Signal?",
nicht „ist es operational handelbar?". Danach ein **zweites** Gate: Data
freshness · Latency · Duplicate control · Availability · reale Kosten ·
Paper/Shadow-Verhalten · keine Source-Drifts.

Erst wenn auch das hält, ist ein lokaler deterministischer Generator
gerechtfertigt — und er benutzt **dieselbe Feature-Funktion** wie der
Research-Runner. Keine zweite RSI- oder Volumen-Implementierung, sonst entsteht
der nächste Truth-Drift („Research sagt A, Production rechnet A′").

---

## Ausführungsreihenfolge

1. **A als eigener OPS-/Infrastruktur-PR** — `OnCalendar` + `Persistent`, kein
   permanenter Doppel-Bootstrap; Taxonomie; Scheduleability- und
   Cadence-Wächter; Tests für Next-Elapse und Kadenz; `Persistent`-Fehlbegründung
   aus #729 korrigiert.
2. **A kontrolliert deployen** — Timer- und Writer-Zustände vorher erfassen;
   erwarteter Timer-Restart per #730 im Claim; danach Next-Elapse **und**
   mehrere reale Auslösungen beobachten.
3. **Research-Definitionen einfrieren, bevor Resultate gesehen werden** —
   RSI-Regel, Volume-Regel, FDR-Familie, Kosten, OOS-Fenster, `n_min`.
4. **B implementieren** — `volume` → `FeatureRow` → `volume_z_20`; während der
   Entwicklung ausschließlich synthetische/Unit-Tests, **kein** echter
   Hypothesenlauf.
5. **Präregistrierung versiegeln** — nach grünem Code Code-SHA und Evaluator-SHA
   eintragen; die OOS-Uhr startet erst danach.
6. **Genau ein konfirmatorischer Forward-Lauf**, danach mechanisch:
   `PASS` → operationales Shadow-Gate → ggf. Generator ·
   `NOT_MET` → Dependency-Check → manuellen Pfad deprecaten ·
   `INCONCLUSIVE` → weder Generator bauen noch „Hypothese widerlegt" behaupten.

## Gates

| Gegenstand | Status |
|---|---|
| A — Timer-Contract | **GO** |
| B — Volumen in die Research-Pipeline | **GO** |
| C — Präregistrierung, forward/OOS | **GO** |
| lokaler Generator | **NO-GO** bis C `PASS` **und** operationales Gate besteht |
| manuelle Alert-Pflege einstellen | erst bei sauberem `NOT_MET` **und** leerem Abhängigkeitsgraph |

Kein Punkt berührt den Seed-Freeze (ADR-0012): es wird keine neue Quelle
onboardet, sondern ein bereits geladenes Rohfeld in eine bestehende Pipeline
durchgereicht.
