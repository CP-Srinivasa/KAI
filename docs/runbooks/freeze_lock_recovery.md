# Freeze-Lock-Recovery (Frozen Evaluation Contract)

`write_frozen_artifact` haelt beim Veroeffentlichen eines eingefrorenen
Datenschnitts einen exklusiven Lock je Checkpoint-Verzeichnis:

```
artifacts/research/prereg/<activation_sha256>/frozen/<T1|T2>/.freeze.lock
```

Der Lock wird ueber `O_CREAT | O_EXCL` genommen; `foreign`-Check, Revalidierung
eines vorhandenen Artefakts und das Schreiben liegen **vollstaendig** darin.
Bleibt er nach einem harten Absturz liegen, laufen alle weiteren Schreiber nach
30 Sekunden in `FrozenInputError` und brechen ab.

**Das ist Absicht und wird nicht automatisiert.** Es gibt bewusst keine
Stale-Erkennung — weder ueber das Alter der Datei noch ueber die PID darin:

* Ein Zeitlimit beweist nichts. Ein langsamer, aber lebender Writer sieht aus
  wie ein toter.
* Eine PID beweist nichts. PIDs werden wiederverwendet, und nach einem Reboot
  ist Prozessidentitaet ohnehin nicht mehr eindeutig.

Ein faelschlich geloeschter Lock erlaubt einen **zweiten Freeze** desselben
Checkpoints. Ein blockierter Checkpoint kostet Zeit; ein zweiter Datenschnitt
kostet die Beweisbarkeit des Verdikts. Deshalb ist Blockieren die richtige
Richtung, und das Aufloesen ist eine auditierte Operator-Handlung.

---

## Wann dieses Runbook gilt

Ein Lauf meldet:

```
... /frozen/T1/.freeze.lock ist seit ueber 30s belegt — ein anderer
Einfrier-Vorgang laeuft oder ist abgestuerzt. Kein zweiter Freeze.
```

## Pruefreihenfolge

Jeder Schritt kann in HOLD enden. **HOLD heisst: Lock bleibt liegen, kein
Freeze, kein EVALUATE, Operator entscheidet.**

1. **Laeuft noch ein Writer?**
   Aktive Evaluationsprozesse und die zugehoerigen Units pruefen. Laeuft einer —
   auch langsam — ist die Antwort **abwarten**, nicht loeschen.

2. **Ist die Identitaet eindeutig?**
   `activation_sha256` und Checkpoint aus dem Pfad gegen `ACTIVE` und
   `activation.json` halten. Zeigen sie auf verschiedene Aktivierungen: HOLD.

3. **Wie viele Artefakte liegen im Checkpoint-Verzeichnis?**

   ```
   ls artifacts/research/prereg/<sha>/frozen/<T1|T2>/evaluation_input_*.json
   ```

   * **0** — es wurde nichts veroeffentlicht. Weiter mit 4.
   * **1** — weiter mit 4.
   * **>1** — Beschaedigung. **Sofort HOLD.** Ein Checkpoint traegt genau einen
     Datenschnitt; zwei sind der Beleg zweier Einfrier-Versuche.

4. **Was sagt das Journal?**
   `checkpoints.jsonl` und `verdicts.jsonl` lesen. Steht fuer diesen Checkpoint
   bereits ein `EVALUATE` mit `evaluation_input_sha256`? Steht ein Verdikt?

5. **Vorhandenes Artefakt vollstaendig revalidieren.**
   Nicht der Dateiname zaehlt, sondern der Inhalt: kanonische Bytes neu hashen
   und gegen den Hash im Namen **und** gegen den im Journal referenzierten Hash
   halten. Jede Abweichung: HOLD.

6. **Journal und Artefakt gegeneinander.**
   Liegt ein `EVALUATE` mit gueltigem, revalidiertem Artefakt vor, ist der
   einzige zulaessige Weg **Resume auf genau diesem Artefakt** — kein neuer
   Freeze, kein neuer Datenabruf, kein neuer Stichtag. Widersprechen sich
   Journal und Artefakt: HOLD.

7. **Erst jetzt darf der Lock entfernt werden** — und nur, wenn *alles*
   zutrifft: kein aktiver Writer, eindeutige Identitaet, hoechstens ein
   Artefakt, Artefakt und Journal widerspruchsfrei. Zuerst
   `RECOVERY_PREPARED` schreiben und syncen (siehe unten), dann entfernen, dann
   den Ausgang festhalten:

   ```
   rm artifacts/research/prereg/<sha>/frozen/<T1|T2>/.freeze.lock
   ```

   Danach `RECOVERY_COMPLETED` (Lock nachweislich weg) oder `RECOVERY_FAILED`
   (mit errno) anhaengen — beide mit derselben `attempt_id`.

## Audit der Intervention

Jede Entfernung wird festgehalten, sonst ist sie spaeter nicht von einem
zweiten Freeze zu unterscheiden. Der Nachweis hat **einen** Ort, append-only:

```
artifacts/research/prereg/<activation_sha256>/lock_recovery.jsonl
```

Bewusst dort und nirgends sonst: der Pfad liegt im prereg-Baum, den
`scripts/kai_backup_artifacts.sh` sichert, und steht damit nach einem Restore
neben Activation, Journalen und Artefakten. Eine freie Notiz in einem Ticket
oder einer Datei irgendwo im Repo waere genau der Nachweis, der beim naechsten
Restore fehlt.

### Zwei Phasen, zwei Zeilen — nie eine bearbeitete

Ein einzelner Eintrag vor dem Entfernen beweist die Absicht, nicht das Ergebnis.
Scheitert das `rm`, steht im Journal exakt dasselbe wie nach einem geglueckten
Lauf, und spaeter ist

    Audit-Eintrag vorhanden + Lock nicht mehr da

nicht mehr aufloesbar zwischen "dieser Versuch hat ihn entfernt" und "dieser
Versuch scheiterte, ein spaeterer Vorgang hat ihn entfernt". Ein Runbook, das
eine neue Mehrdeutigkeit in die Truth-Kette traegt, ist schlechter als keines.

Deshalb zwei Zeilen, verbunden ueber `attempt_id`, beide angehaengt, **keine
davon nachtraeglich veraendert**:

```
1. RECOVERY_PREPARED     alle Pruefungen, Hashes, Operator   -> fsync
2. rm .freeze.lock
3. RECOVERY_COMPLETED    removed=true,  error=null
   oder
   RECOVERY_FAILED       removed=false, error=<errno/Meldung>
```

Bricht der Vorgang zwischen 1 und 3 ganz ab, bleibt ein `RECOVERY_PREPARED`
ohne Abschluss stehen. Das ist kein Mangel, sondern die ehrliche Aussage: der
Ausgang ist unbekannt und muss von Hand geklaert werden.

Gemeinsame Felder beider Zeilen:

| Feld | Inhalt |
| --- | --- |
| `schema_version` | Version dieses Eintragsformats |
| `event_type` | `RECOVERY_PREPARED`, `RECOVERY_COMPLETED` oder `RECOVERY_FAILED` |
| `attempt_id` | verbindet die Zeilen eines Versuchs; UUID4 genuegt (`python -c "import uuid; print(uuid.uuid4())"`). Deterministisch ist erlaubt, darf aber ueber zwei Versuche hinweg nicht kollidieren |
| `activation_sha256` | die Aktivierung, zu der der Checkpoint gehoert |
| `checkpoint` | `T1` oder `T2` |
| `recorded_at_utc` | Zeitpunkt, **strikt UTC** mit `+00:00` oder `Z` — keine lokale Zeit, kein blosses "timezone-behaftet" |

Zusaetzlich in `RECOVERY_PREPARED`:

| Feld | Inhalt |
| --- | --- |
| `lock_contents` | Inhalt von `.freeze.lock` VOR dem Entfernen, woertlich |
| `artifact_names` | alle `evaluation_input_*.json` im Verzeichnis |
| `artifact_sha256` | die Hashes dazu, ueber die Bytes neu berechnet |
| `checkpoint_action` | was im Checkpoint-Journal steht (`EVALUATE`, `EXTEND_TO_T2`, …) oder `null` |
| `journal_evaluation_input_sha256` | der vom Journal referenzierte Input-Hash oder `null` |
| `verdict_present` | ob fuer diesen Checkpoint ein Verdikt existiert |
| `revalidation_result` | Ergebnis aus Schritt 5, inklusive der Begruendung |
| `recovery_reason` | warum entfernt wurde — kein "war alt" |
| `operator` | wer die Entfernung vornimmt |

Zusaetzlich in `RECOVERY_COMPLETED` / `RECOVERY_FAILED`:

| Feld | Inhalt |
| --- | --- |
| `removed` | `true` nur, wenn `.freeze.lock` danach nachweislich fehlt |
| `completed_at_utc` | Abschlusszeitpunkt, strikt UTC |
| `error` | `null` bei Erfolg, sonst errno/Meldung woertlich |

`artifact_sha256` wird neu berechnet und nicht aus dem Dateinamen abgeschrieben.
Der Name ist kein Beweis; das ist derselbe Grund, aus dem
`write_frozen_artifact` ein vorhandenes Artefakt byte-genau revalidiert.

## Was NICHT eingebaut wird

* Kein automatisches Loeschen nach Zeitablauf.
* Keine PID-Pruefung, die eine Loeschberechtigung begruendet.
* Kein "Lock erneuern"-Mechanismus, der einen Writer am Leben behauptet.

Die Lock-Datei darf spaeter mehr Diagnose tragen — PID, Hostname, Boot-ID,
Zeitpunkt der Aufnahme. Diese Angaben helfen dem Operator bei Schritt 1 und
duerfen **nie** automatisch die Berechtigung zum Loeschen begruenden.
