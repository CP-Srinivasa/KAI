# TV-Pfad: reparieren, messbar machen, entscheiden

Status: **Entwurf zur Freigabe (Rev. 2)** · Datum: 2026-08-19 ·
Operator-Review 1 + 2 eingearbeitet · alle Code-Aussagen live gegengeprueft

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

### A1 Die zwei betroffenen Units — unterschiedlich behandelt

**`kai-tv-auto-promote`** bekommt `OnCalendar=*:0/5` (alle fünf Minuten zur
Wanduhr) plus `Persistent=true` (das dort **wirkt**). Die Aufgabe soll schlicht
kontinuierlich laufen; die Wanduhr-Kadenz bringt hier Klarheit, und der gemessene
`NextElapse=infinity`-Vorfall rechtfertigt den Wechsel. **Kein** permanenter zusätzlicher Bootstrap-Trigger in derselben
Unit — bei `OnCalendar` + `Persistent` holt systemd den verpassten Lauf beim
Aktivieren nach, ein zweiter Trigger erzeugt nur Doppelaktivierungen.

**Kein pauschaler manueller Bootstrap.** Bei `OnCalendar` + `Persistent` holt
systemd einen verpassten Lauf beim Aktivieren selbst nach; ein zusätzlicher
Handstart erzeugt dann zwei Läufe unmittelbar hintereinander. Rollout-Reihenfolge:

1. Timer deployen/neu starten,
2. `NextElapse` prüfen (muss endlich sein),
3. beobachten, ob systemd den erwarteten Lauf **selbst** ausführt,
4. **nur falls** innerhalb einer definierten Frist kein Lauf kommt: kontrollierter
   manueller Service-Smoke.

**`kai-oracle-earnings-booking` bekommt KEINEN Kalender-Trigger.** Produktions-
verhalten zu ändern, nur damit ein früherer Kommentar wieder wahr wird, wäre die
falsche Richtung. Stattdessen die kleinste Reparatur:

```
OnActiveSec=60min        # restart-fester Initialanker (neu)
OnUnitActiveSec=60min    # Wiederholung wie bisher
Persistent=false         # war ohne OnCalendar ohnehin wirkungslos
```

Die Bedeutung „ungefähr eine Stunde zwischen Aktivierungen" bleibt erhalten.

Die falsche Zusicherung aus #729 wird **ersetzt**, nicht repariert. Statt

> „`Persistent=true` holt einen verpassten Lauf nach"

steht künftig der tatsächlich belegbare Grund — im Code gegengeprüft, der
Docstring von `scripts/book_oracle_earnings.py` sagt es wörtlich:

> Ein verpasstes Intervall verliert fachlich nichts, weil jeder Lauf die settled
> Invoices listet und **idempotent** bucht; der nächste Lauf verarbeitet die noch
> ungebuchten.

**Ehrliche Grenze dieser Aussage:** die Nachholbarkeit ist durch das
Listing-Fenster `num_max_invoices=1000` begrenzt. Bei lifetime 0 sat ist das
theoretisch — der Test pinnt trotzdem genau diese Grenze, statt „geht nicht
verloren" unbedingt zu behaupten.

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

**Der Contract verlangt NICHT `OnCalendar`.** Die Invariante lautet:

> Ein `RECURRING`-Timer muss nach Aktivierung/Neustart **garantiert wieder einen
> zukünftigen Trigger** besitzen — er muss *restart-safe scheduleable* sein.

Zwei erlaubte Bauformen erfüllen das:

| Bauform | Initialer Trigger | Wiederholung |
|---|---|---|
| `CALENDAR_RECURRING` | `OnCalendar=…` (Wanduhr, restart-fest) | dieselbe Angabe; `Persistent=true` optional |
| `MONOTONIC_RECURRING` | `OnActiveSec=…` — relativ zur **Timer**-Aktivierung | `OnUnitActiveSec=…` — relativ zur letzten **Service**-Aktivierung |

Der Unterschied ist genau die Ursache des Vorfalls: `OnUnitActiveSec` verankert
sich am Service, `OnBootSec` am Boot. Wird ein Timer lange nach dem Boot neu
gestartet und der Service lief seit langem nicht, existiert **kein** Anker.
`OnActiveSec` verankert sich am Timer selbst und überlebt daher jeden Neustart.

Damit müssen 60+ Timer **nicht** in eine andere Zeitsemantik migriert werden —
ein fehlender `OnActiveSec`-Bootstrap genügt als Reparatur.

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

1. jede Unit besitzt eine Klassendeklaration,
2. jede `RECURRING`-Unit besitzt einen **restart-sicheren initialen** Trigger
   (`OnCalendar` **oder** `OnActiveSec`) — ein Timer mit ausschliesslich
   `OnBootSec` + `OnUnitActiveSec` bricht den Test,
3. jede `RECURRING`-Unit besitzt einen Wiederholungstrigger,
4. `Persistent=` steht nur an Units mit `OnCalendar` — sonst ist es wirkungslos
   und damit eine falsche Zusicherung.

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
mu_t     = fmean( log1p(volume[t-20 … t-1]) )
sigma_t  = pstdev( log1p(volume[t-20 … t-1]) )      # Population, ddof = 0

volume_z_20[t] = ( log1p(volume[t]) − mu_t ) / sigma_t

SPIKE[t]  ⇔  volume_z_20[t] >= 2.0
```

Baseline sind die **20 vorherigen abgeschlossenen** Kerzen; die aktuelle Kerze
geht **nicht** in ihre eigene Referenzverteilung ein.

`pstdev`, nicht `stdev` — KAI benutzt beim Funding-Z-Score bereits
`statistics.fmean` + `statistics.pstdev` (`app/analysis/features/funding_align.py::_rolling_z`,
live gegengeprüft). Dieselbe Funktion liefert dort auch `None` bei zu kurzem
Fenster und bei `std <= 0`. `volume_z_20` übernimmt diese Semantik **wörtlich**,
statt eine zweite Z-Score-Konvention zu erfinden.

Die Spike-Schwelle **2,0** ist neu festgelegt, weil die manuelle
TradingView-Regel keine explizite Volumenschwelle mitliefert (die empfangenen
Signale tragen nur `ticker` und `action`). Existiert operatorseitig doch eine
ursprüngliche Schwelle, gilt **diese** — sie zu übernehmen ist besser, als eine
zu erfinden.

`log1p`, weil Volumen stark rechtsschief ist und einzelne Spikes Mittelwert und
Standardabweichung sonst massiv verzerren. **Keine** nachträgliche Auswahl
zwischen raw/log und 10/20/30-Fenstern nach Ergebnislage — eine Definition,
begründet, versiegelt.

### B1b `rsi_14_prev` — Crossover ohne Schnittstellenbruch

Der Decider-Vertrag ist heute `FeatureRow -> {-1, 0, +1}` (live geprüft:
`def rsi_oversold_long(r: FeatureRow) -> int`). Ein Decider sieht **nur** die
aktuelle Zeile, nicht `t-1` — ein Crossover ist damit derzeit **nicht
ausdrückbar**.

Die Schnittstelle auf `(rows, index)` umzubauen wäre der große Hebel mit
Auswirkung auf alle bestehenden Hypothesen. Die kleine Erweiterung genügt:

```
FeatureRow.rsi_14_prev
rsi_14_prev[t] = rsi_14[t-1]        # deterministisch in der Feature-Matrix
```

Damit bleibt die Hypothese ein gewöhnlicher Decider — kausal, trivial testbar,
allgemein wiederverwendbar, kompatibel mit allen bestehenden Regeln.

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

Eine neue Regel `rsi_reentry_volume_confirmed` — der Name trägt die Semantik,
denn es ist ein **Re-Entry aus dem Extrembereich**, nicht der Eintritt hinein:

```
LONG   ⇔  rsi_14_prev < 30  AND  rsi_14 >= 30  AND  SPIKE
SHORT  ⇔  rsi_14_prev > 70  AND  rsi_14 <= 70  AND  SPIKE
sonst  →  0
```

Sie feuert genau **einmal pro Übergang** statt in jeder Kerze des Zustands, was
Churn und Gebührenlast senkt — bei einem Buch, dessen Verlust fast vollständig
Gebühr ist, ist das der entscheidende Unterschied.

**Sie ist damit wirklich neu.** Die −23,27 bps von `rsi_oversold_long` widerlegen
sie **nicht**, weil jene Regel `rsi_14 < 30` misst — eine Level-Regel, nicht
diesen Übergang.

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

### C3 Ausführungskonvention — Signalzeitpunkt ist nicht Einstiegszeitpunkt

Das Label des Runners ist heute reines Close-zu-Close (live geprüft,
`app/analysis/features/forward_returns.py`):

```
fwd_bps[i] = 10000 * ( close[i + horizon] / close[i] − 1 )
```

Für die bisherigen explorativen Features ist das eine definierte
Research-Konvention. Für ein konfirmatorisches, später eventuell automatisiertes
Signal ist sie **nicht** übernehmbar: RSI und Volumen der Kerze `t` stehen erst
**nach** deren Schluss fest — man kann nicht gleichzeitig behaupten, exakt zu
`close(t)` eingestiegen zu sein.

Verbindlich für dieses Experiment:

```
SIGNAL_OBSERVED_AT = close(t)
ENTRY              = open(t+1)
EXIT               = close(t + horizon)
```

Beispiel: die 1h-Kerze 12:00–12:59 schließt, RSI und Volumen stehen fest, das
Signal gilt als festgestellt — frühester regelkonformer Einstieg ist das **Open
der 13:00-Kerze**.

Das verlangt eine **zusätzliche** Label-Funktion neben der bestehenden; die alte
bleibt unangetastet, damit die zwölf bereits versiegelten Close-zu-Close-Verdikte
gültig bleiben. Die Rohdaten liegen bereits vor: `build_feature_matrix` bekommt
vollständige `OHLCV`-Kerzen und zieht schon `highs`/`lows`/`closes` heraus, der
Runner hat `history.candles` in Reichweite. `opens` und `volumes` sind je eine
Zeile Datenweg — kein neuer Pfad.

Ein konfirmatorischer `PASS`, der Signal-at-close mit Entry-at-same-close
vermischt, wird **nicht** akzeptiert.

### C3b Folge für die FDR-Familie — Entscheidung erbeten

Weil das Label wechselt, **kann** das neue Experiment die alte Familie gar nicht
teilen: dieselbe Regel unter anderem Label ist eine andere Messung. Die
Forderung „eigene eingefrorene Familie" ist damit nicht nur Vorsicht, sondern
technisch erzwungen.

Offen ist die Größe der Familie:

| Variante | Familie | Wirkung |
|---|---|---|
| **(a)** | nur `rsi_reentry_volume_confirmed` (n=1) | BH-FDR degeneriert zu blossem Alpha — schwächster Schutz |
| **(b)** | alle 12 bestehenden Regeln **plus** die neue, sämtlich unter dem Next-Open-Label | ehrliche Mehrfachtest-Schranke; alte Close-zu-Close-Verdikte bleiben unberührt |

**Empfehlung: (b).** Es kostet fast nichts (dieselbe Datenbasis, nur ein zweites
Label), hebt die Schranke für die neue Regel statt sie zu senken, und lässt die
versiegelte Vergangenheit unangetastet.

### C4 Was die Präregistrierung festnagelt

`rsi_reentry_volume_confirmed` mit `prev<30 && cur>=30` bzw. `prev>70 && cur<=70` ·
`SPIKE ⇔ volume_z_20 >= 2.0` · `volume_z_20`-Formel inkl. `pstdev`/ddof=0 ·
Ausführungskonvention `signal close(t) → entry open(t+1) → exit close(t+h)` ·
Timeframe 1h · Venue/Quelle · Horizon · Forward-OOS-Startzeit T0 · T1 · T2 ·
Sample Unit · `n_min` · Verlängerungsregel · Umgang mit missing/inconclusive ·
Kostenmodell · Slippage · primäre Kennzahl · ökonomische Mindesthürde ·
BH-FDR-Familie (Variante aus C3b) · Alpha/FDR-Level · Evaluator-Version ·
Code-SHA · Datenschnitt · keine Nachoptimierung.

### C5 Fristende ist kein FAIL

```
Deadline erreicht,  n = 27,  n_min = 100
   →  INCONCLUSIVE / NOT_MATURE
   →  NICHT NOT_MET
```

Diese Lektion war bei ND-v2 teuer. Versiegelt werden ein **fixes OOS-Fenster**
und ein **Mindest-n**. Verdikt genau **einmal** nach Fensterende, sofern
`n ≥ n_min`. Kein optional stopping bei hübschem oder hässlichem Zwischenstand.

**Verlängerungsregel — jetzt mitversiegelt, nicht später beschlossen:**

```
Primärfenster T0 → T1

bei T1:  n >= n_min  →  Verdikt
         n <  n_min  →  KEINE Performancezahlen ansehen
                     →  automatisch bis T2 verlängern

bei T2:  n >= n_min  →  Verdikt
         sonst       →  INCONCLUSIVE
```

Der entscheidende Punkt: die Verlängerung steht **vor** dem Start fest. Sie nach
Sichtung der Performance zu beschliessen wäre optional stopping durch die
Hintertür.

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
   RSI-Regel (**entschieden: Re-Entry-Crossover**), Volume-Regel, FDR-Familie
   (offen: C3b), Kosten, OOS-Fenster T0/T1/T2, `n_min`, Ausführungskonvention.
4. **B implementieren** — `volume` und `open` → `FeatureRow` bzw. Label;
   `volume_z_20`; `rsi_14_prev`; Next-Open-Label als **zusätzliche** Funktion.
   Während der Entwicklung ausschließlich synthetische/Unit-Tests, **kein**
   echter Hypothesenlauf.
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
| FDR-Familiengröße (C3b) | **offen** — Empfehlung (b): 12 + 1 unter Next-Open-Label |
| manuelle Alert-Pflege einstellen | erst bei sauberem `NOT_MET` **und** leerem Abhängigkeitsgraph |

Kein Punkt berührt den Seed-Freeze (ADR-0012): es wird keine neue Quelle
onboardet, sondern ein bereits geladenes Rohfeld in eine bestehende Pipeline
durchgereicht.
