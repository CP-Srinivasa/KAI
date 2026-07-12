# Verdict-Bundle v0.1 — Threat Model (VERSIEGELT vor Implementierung)

**Stand:** 2026-07-12 · **Scope:** hart eingefroren (Operator-Direktive 07-11 Nachtrag 2/4/5). Kein Portal, keine Public API, kein Dashboard, **kein LLM im Beweisweg**, kein Netzwerk (`network_required=false` ist Schema-Konstante). Generator (`kai bundle create`) und Verifier (`kai verify <bundle>`) sind physisch und logisch getrennt; der Verifier ist bewusst dumm, deterministisch und offline.

## Was der Verifier beweist — und was nicht

**Beweist:** (1) Bundle unverändert (Manifest-Attestation + Datei-Hashes) · (2) Pass-Latte vor den Daten fixiert (prereg_hash ↔ preregistration.json, prereg_id-Bindung) · (3) Daten-Slice unverändert (Input-Hashes) · (4) Code-/Umgebungs-Anker sichtbar (code_sha, dependency_lock_hash) · (5) Slice truth-lint-sauber im Sinne der Bundle-Semantik · (6) Ergebnis reproduziert (result.json byte-identisch, `expected_verdict.result_sha256`).
**Beweist NICHT:** dass die Daten-*Erhebung* korrekt war (ein falsch aufgezeichneter Preis reproduziert falsch, aber byte-exakt), dass das Verdikt ökonomisch „richtig" ist, oder irgendetwas außerhalb des Slices. Diese Grenzen stehen im README jedes Bundles.

## Angreiferbild

Ein Dritter mit vollem Schreibzugriff auf das Bundle NACH Erzeugung (Mailanhang, Download, Mirror) und ein unehrlicher Betreiber (wir selbst) sind dieselbe Angriffsklasse: **niemandem muss geglaubt werden.** Der Verifier läuft auf der Maschine des Prüfers, offline, ohne unsere Mitwirkung.

## Bedrohungen → Gegenmaßnahmen → Pflichttests (INVALID-Fälle ZUERST implementieren)

| ID | Bedrohung | Gegenmaßnahme | Verifier-Ergebnis |
|---|---|---|---|
| T1 | Manifest nachträglich editiert (z. B. expected_verdict umgeschrieben) | Attestation-Rehash über kanonisches Manifest | INVALID |
| T2 | Datei im data_slice/ manipuliert/ersetzt/gelöscht/hinzugefügt | Input-Inventar vollständig + Hash je Datei; unbekannte Dateien im Slice = Verstoß | INVALID |
| T3 | Pfad-Traversal/Zip-Slip (`../`, absolute Pfade, Symlinks) beim Entpacken/Lesen | Schema-Pattern `^data_slice/…`, Normalisierung + Containment-Check vor JEDEM Dateizugriff; Symlinks werden nicht gefolgt | INVALID |
| T4 | Prä-Registrierung ausgetauscht (weichere Pass-Latte) | prereg_hash über kanonisches preregistration.json; prereg_id deterministisch aus Claim | INVALID |
| T5 | result.json „verbessert" | Reproduktion aus Slice + Vergleich gegen result_sha256; Report NUR aus eigenem Rechenlauf | FAIL/INVALID je nach Ort der Abweichung: Repro ≠ result.json ⇒ INVALID; Repro = result.json, aber Kriterium verfehlt ⇒ FAIL |
| T6 | Alter PASS-Bundle als aktuell ausgegeben (Replay) | generated_at_utc + code_sha + runtime_baseline_sha im attestierten Manifest; Verifier zeigt sie an — Aktualität bewertet der Mensch, der Verifier behauptet sie nie | PASS mit sichtbarem Zeit-/Code-Anker |
| T7 | Slice enthält Rows ohne beweisbare Provenance (TL-008-Klasse) | Truth-Lint auf dem Slice als Verify-Schritt 5; relevante Row ohne signal_path_id/Provenance | INVALID |
| T8 | Lint-WARNING im Slice als „schon ok" verkauft | Nur WARNINGs erlaubt, die in `preregistered_slice_warnings` stehen UND nicht evidenzkritisch sind; alles andere | INVALID |
| T9 | Ressourcen-Bombe (Riesen-Dateien, JSON-Bomben, Zip-Bomben) | Größen-Limits aus Manifest (`bytes` je Input) VOR dem Parsen geprüft; Streaming-Hashing; harte Gesamtgrenze | INVALID |
| T10 | Verifier-Substitution („benutze unser praktisches Prüfskript") | Verifier ist eigenständig, dependency-arm (stdlib-nah), Quelltext klein und lesbar; README nennt Hash des Verifiers; Prüfer kann Schritte auch manuell nachrechnen (dokumentiert) | — (organisatorisch) |
| T11 | Umgebungs-Drift (andere Dependency-Versionen ⇒ andere Zahlen) | dependency_lock_hash + Repro-Skripte pinnen die Umgebung; Clean-Room-Reproduktion ist Abnahme-Kriterium v0.1 | INCONCLUSIVE bei nicht herstellbarer Umgebung |
| T12 | Netz-/LLM-Einschleusung in den Beweisweg | Schema-Konstanten `network_required=false`, `llm_required=false`; Verifier öffnet keinerlei Sockets; Tests erzwingen das | INVALID bei Verstoß im Manifest |

## Statussemantik (abschließend, exakt vier Werte)

- **PASS** — Ergebnis reproduziert, Evidenz vollständig, Slice lint-sauber.
- **FAIL** — prä-registriertes Kriterium nicht erfüllt (ehrliches negatives Ergebnis; Bundle selbst integer).
- **INVALID** — Provenance, Integrität oder Reproduktion gebrochen (T1–T5, T7–T9, T12).
- **INCONCLUSIVE** — Mindestdaten oder Entscheidungsreife nicht erreicht (auch T11).

Der Verifier druckt GENAU einen dieser vier Werte plus die Prüfschritt-Liste; Exit-Codes: PASS=0, FAIL=1, INVALID=2, INCONCLUSIVE=3. Analyse/Forensik bleiben bei jedem Ergebnis möglich — gesperrt ist nur der belastbare Evidence-Claim.

## Verify-Reihenfolge (fest, Abbruch beim ersten INVALID)

1. Manifest gegen Schema v0.1 (inkl. Konstanten) · 2. Attestation-Rehash · 3. Input-Inventar vollständig + alle Hashes · 4. prereg_hash · 5. Truth-Lint auf Slice (Bundle-Semantik) · 6. Verdikt reproduzieren · 7. Vergleich erwartet ↔ reproduziert → Statuswert.
