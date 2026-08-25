# Lock-Recovery (Frozen Evaluation Contract)

Drei Schreiber der Wahrheitsschicht nehmen einen exklusiven Lock. Sie teilen
sich **eine** Mechanik (`app/research/exclusive_lock.py`), aber **nicht**
dieselbe Recovery — was zu pruefen ist, haengt daran, was der Lock geschuetzt
hat.

| `lock_kind` | Pfad | geschuetzt wird |
| --- | --- | --- |
| `FROZEN_PUBLISH` | `…/<sha>/frozen/<T1\|T2>/.freeze.lock` | das Veroeffentlichen **eines** eingefrorenen Datenschnitts |
| `CHECKPOINT_JOURNAL` | `…/<sha>/.checkpoints.jsonl.lock` | Anhaengen an `checkpoints.jsonl` |
| `VERDICT_JOURNAL` | `…/<sha>/.verdicts.jsonl.lock` | Anhaengen an `verdicts.jsonl` |

(`…` = `artifacts/research/prereg`)

Der Lock wird ueber `O_CREAT | O_EXCL` genommen — die eine Operation, die das
Dateisystem selbst serialisiert. Beim Freeze liegen `foreign`-Check,
Revalidierung eines vorhandenen Artefakts und das Schreiben **vollstaendig**
darin; bei den Journalen das Lesen, die Konfliktpruefung und das Anhaengen.
Ohne ihn koennten zwei Prozesse beide "kein Eintrag vorhanden" sehen und beide
schreiben.

Bleibt ein Lock nach einem harten Absturz liegen, laufen alle weiteren Schreiber
nach 30 Sekunden in einen Abbruch (`FrozenInputError` beim Freeze,
`ExclusiveLockError` bei den Journalen).

**Das ist Absicht und wird nicht automatisiert.** Es gibt bewusst keine
Stale-Erkennung — weder ueber das Alter der Datei noch ueber die PID darin:

* Ein Zeitlimit beweist nichts. Ein langsamer, aber lebender Writer sieht aus
  wie ein toter.
* Eine PID beweist nichts. PIDs werden wiederverwendet, und nach einem Reboot
  ist Prozessidentitaet ohnehin nicht mehr eindeutig.

Ein faelschlich geloeschter Lock erlaubt beim Freeze einen **zweiten
Datenschnitt** desselben Checkpoints, bei den Journalen eine **zweite
autoritative Zeile**. Ein blockierter Checkpoint kostet Zeit; beides andere
kostet die Beweisbarkeit des Verdikts. Deshalb ist Blockieren die richtige
Richtung, und das Aufloesen ist eine auditierte Operator-Handlung.

---

## Wann dieses Runbook gilt

Ein Lauf meldet eine der drei Formen:

```
… /frozen/T1/.freeze.lock ist seit ueber 30s belegt — ein anderer
Einfrier-Vorgang laeuft oder ist abgestuerzt. Kein zweiter.

… /.checkpoints.jsonl.lock ist seit ueber 30s belegt — ein anderer
Checkpoint-Schreibvorgang laeuft oder ist abgestuerzt. Kein zweiter.

… /.verdicts.jsonl.lock ist seit ueber 30s belegt — ein anderer
Verdikt-Schreibvorgang laeuft oder ist abgestuerzt. Kein zweiter.
```

**Zuerst `lock_kind` aus dem Pfad bestimmen.** Danach gilt Teil A fuer alle drei
und genau **einer** der Teile B1/B2/B3.

Jeder Schritt kann in HOLD enden. **HOLD heisst: Lock bleibt liegen, kein
Freeze, kein EVALUATE, kein Journal-Schreiben, Operator entscheidet.**

---

## Teil A — gilt fuer jeden Lock

1. **Laeuft noch ein Writer?**
   Aktive Evaluationsprozesse und die zugehoerigen Units pruefen. Laeuft einer —
   auch langsam — ist die Antwort **abwarten**, nicht loeschen.

2. **Ist die Aktivierung eindeutig?**
   `activation_sha256` aus dem Pfad gegen `ACTIVE` und `activation.json` halten;
   der Hash in `activation.json` muss aus dessen eigenem Inhalt neu berechnet
   dazu passen. Zeigen sie auf verschiedene Aktivierungen: HOLD.

   ⚠ **Nur der Freeze-Lock traegt einen Checkpoint im Pfad.** Bei den beiden
   Journal-Locks gibt es kein `T1`/`T2` — wer dort einen Checkpoint aus dem Pfad
   ableitet, erfindet ihn. Im Audit bleibt `checkpoint` dann `null`.

3. **Ist der Baum als Ganzes noch stimmig?**
   `verify_prereg_tree(root, sha)` laufen lassen. Es rechnet die ganze Kette
   nach: Activation-Hash, Fingerabdruck je Checkpoint-Zeile, `result_sha256` je
   Verdikt, `evaluation_input_sha256` und `dataset_sha256` je journalisiertem
   Artefakt. Jede Abweichung: HOLD.

---

## Teil B1 — `FROZEN_PUBLISH`

4. **Wie viele Artefakte liegen im Checkpoint-Verzeichnis?**

   ```
   ls artifacts/research/prereg/<sha>/frozen/<T1|T2>/evaluation_input_*.json
   ```

   * **0** — es wurde nichts veroeffentlicht. Weiter mit 5.
   * **1** — weiter mit 5.
   * **>1** — Beschaedigung. **Sofort HOLD.** Ein Checkpoint traegt genau einen
     Datenschnitt; zwei sind der Beleg zweier Einfrier-Versuche.

5. **Vorhandenes Artefakt vollstaendig revalidieren.**
   Nicht der Dateiname zaehlt, sondern der Inhalt: kanonische Bytes neu hashen
   und gegen den Hash im Namen **und** gegen den im Journal referenzierten Hash
   halten. Jede Abweichung: HOLD.

6. **Journal und Artefakt gegeneinander.**
   Liegt ein `EVALUATE` mit gueltigem, revalidiertem Artefakt vor, ist der
   einzige zulaessige Weg **Resume auf genau diesem Artefakt** — kein neuer
   Freeze, kein neuer Datenabruf, kein neuer Stichtag. Widersprechen sich
   Journal und Artefakt: HOLD.

---

## Teil B2 — `CHECKPOINT_JOURNAL`

Hier gibt es **kein** Artefakt zu pruefen und **keinen** Checkpoint aus dem Pfad.
Gegenstand ist die Datei selbst.

4. **`checkpoints.jsonl` vollstaendig lesen.**
   `load_checkpoints(...)` gegen die Aktivierung laufen lassen. Es prueft jede
   Zeile streng: Feldtypen, Wertebereiche, `recorded_at_utc` mit Offset `+00:00`
   und den `decision_fingerprint` gegen den Zeileninhalt. Jeder Fehler: HOLD.

5. **Abgeschnittenes Ende?**
   Ein Absturz mitten im Anhaengen kann eine unvollstaendige letzte Zeile
   hinterlassen. Sie faellt in Schritt 4 als ungueltiges JSON auf. **Nicht
   reparieren, nicht abschneiden** — das waere eine Aenderung an einem
   append-only Journal. HOLD.

6. **Eindeutigkeit je Checkpoint.**
   Hoechstens ein Eintrag je `T1`/`T2`; zwei Eintraege mit verschiedenen
   Fingerabdruecken sind der Beleg zweier konkurrierender Entscheidungen. HOLD.

---

## Teil B3 — `VERDICT_JOURNAL`

Ebenfalls ohne Checkpoint im Pfad.

4. **`verdicts.jsonl` vollstaendig lesen.**
   `load_verdicts(...)` gegen die Aktivierung laufen lassen: Schema-Version,
   erlaubte Verdikte, `0 <= p <= 1`, `0 < alpha < 1`, echte Ganzzahlen,
   `recorded_at_utc` mit Offset `+00:00`, und `result_sha256` gegen den
   Zeileninhalt. Jeder Fehler: HOLD.

5. **Hoechstens ein Verdikt je Checkpoint.**
   Zwei autoritative Zeilen fuer denselben Checkpoint sind genau der Schaden,
   den der Lock verhindert. HOLD.

6. **Kette Verdikt → Checkpoint → Artefakt.**
   Jedes Verdikt muss auf ein `evaluation_input_sha256` zeigen, das ein
   Checkpoint-Eintrag nennt, und dieses Artefakt muss existieren und
   revalidieren. Ein Ergebnis ohne den Entschluss, der es hervorbrachte, ist
   kein Beweis. HOLD.

---

## Entfernen — zwei Phasen, zwei Zeilen

Erst wenn Teil A **und** der zutreffende Teil B vollstaendig durchlaufen sind,
darf der Lock entfernt werden.

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
2. rm <lock_path>
3. RECOVERY_COMPLETED    removed=true,  error=null
   oder
   RECOVERY_FAILED       removed=false, error=<errno/Meldung>
                                                             -> fsync
```

Bricht der Vorgang zwischen 1 und 3 ganz ab, bleibt ein `RECOVERY_PREPARED`
ohne Abschluss stehen. Das ist kein Mangel, sondern die ehrliche Aussage: der
Ausgang ist unbekannt und muss von Hand geklaert werden.

## Audit der Intervention

Der Nachweis hat **einen** Ort, append-only:

```
artifacts/research/prereg/<activation_sha256>/lock_recovery.jsonl
```

Bewusst dort und nirgends sonst: der Pfad liegt im prereg-Baum, den
`scripts/kai_backup_artifacts.sh` sichert, und steht damit nach einem Restore
neben Activation, Journalen und Artefakten. Eine freie Notiz in einem Ticket
oder einer Datei irgendwo im Repo waere genau der Nachweis, der beim naechsten
Restore fehlt.

Gemeinsame Felder beider Zeilen:

| Feld | Inhalt |
| --- | --- |
| `schema_version` | Version dieses Eintragsformats |
| `event_type` | `RECOVERY_PREPARED`, `RECOVERY_COMPLETED` oder `RECOVERY_FAILED` |
| `attempt_id` | verbindet die Zeilen eines Versuchs; UUID4 genuegt (`python -c "import uuid; print(uuid.uuid4())"`). Deterministisch ist erlaubt, darf aber ueber zwei Versuche hinweg nicht kollidieren |
| `activation_sha256` | die Aktivierung, zu der der Lock gehoert |
| `lock_kind` | `FROZEN_PUBLISH`, `CHECKPOINT_JOURNAL` oder `VERDICT_JOURNAL` |
| `lock_path` | der vollstaendige Pfad der Lock-Datei |
| `checkpoint` | `T1`/`T2` **nur** bei `FROZEN_PUBLISH`, sonst `null` — bei den Journal-Locks gibt es keinen |
| `recorded_at_utc` | Zeitpunkt, **strikt UTC** mit `+00:00` oder `Z` — keine lokale Zeit, kein blosses "timezone-behaftet" |

Zusaetzlich in `RECOVERY_PREPARED`:

| Feld | Inhalt |
| --- | --- |
| `lock_contents` | Inhalt der Lock-Datei VOR dem Entfernen, woertlich |
| `tree_verification` | Ergebnis von `verify_prereg_tree` (Teil A, Schritt 3) |
| `journal_state` | was die betroffenen Journale sagen: Checkpoint-Aktionen, Verdikte, referenzierte Input-Hashes |
| `artifact_names` | **nur** bei `FROZEN_PUBLISH`: alle `evaluation_input_*.json` im Verzeichnis |
| `artifact_sha256` | **nur** bei `FROZEN_PUBLISH`: die Hashes dazu, ueber die Bytes neu berechnet |
| `revalidation_result` | Ergebnis des zutreffenden Teils B, inklusive Begruendung |
| `recovery_reason` | warum entfernt wurde — kein "war alt" |
| `operator` | wer die Entfernung vornimmt |

Zusaetzlich in `RECOVERY_COMPLETED` / `RECOVERY_FAILED`:

| Feld | Inhalt |
| --- | --- |
| `removed` | `true` nur, wenn die Lock-Datei danach nachweislich fehlt |
| `completed_at_utc` | Abschlusszeitpunkt, strikt UTC |
| `error` | `null` bei Erfolg, sonst errno/Meldung woertlich |
