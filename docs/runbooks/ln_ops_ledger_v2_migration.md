# Runbook — Lightning Ops-Ledger v2 Migration (operator)

Ziel: das historische, flache `artifacts/ln_ops_ledger.jsonl` (v1) in das
hash-verkettete, redigierte v2-Schema `artifacts/ln_ops_ledger_v2.jsonl` überführen —
ohne Preimages, rohe BOLT11-Rechnungen, Empfänger-Pubkeys oder Route-Hops.

**Kein Schritt hier bewegt Kapital, keiner ruft LND.** Das Migrationsskript verändert
oder löscht die Quelle nie; das Einsetzen der neuen Datei ist eine separate, bewusste
Operator-Aktion.

## Invarianten (nicht verhandelbar)

1. **Das Original wird nie gelöscht.** Es bleibt on-box als
   `artifacts/ln_ops_ledger.v1.jsonl` liegen (ADR-0016 Invariante 1 „append-only",
   Operator-Beschluss 05.08.: die historischen Test-Rows bleiben dokumentiert). Die
   v2-Datei ist redigiert — die einzige verbleibende Forensik-Quelle für Route-Details
   und Fehlertexte ist genau dieses Original.
2. **Kein Writer läuft während der Umstellung.** Sonst schreibt ein Prozess in die alte
   Datei, während der Operator die neue einsetzt → stiller Verlust eines Geld-Events.
3. **Keine unverifizierte Datei geht in Betrieb.** `verification.ok=true` im Report ist
   die Freigabebedingung, nicht die Abwesenheit von Fehlermeldungen.
4. **Seit PR-C ist v2 das Live-Journal.** Jeder Spend schreibt seinen Intent
   write-ahead nach v2, das Tages-Cap kommt aus `spent_today_sat_v2`, und der
   Dashboard-Read folgt automatisch (v2 wenn vorhanden, sonst v1). `append_ln_op` /
   `spent_today_sat` haben keinen Aufrufer mehr und bleiben ausschliesslich als
   Rollback-Fläche stehen. **Empfangen ist entkoppelt:** Invoice-Mints laufen in
   `artifacts/ln_receive_ledger.jsonl` und sind von diesem Journal völlig
   unabhängig — eine kaputte oder fehlende v2-Datei stoppt Spends, aber niemals den
   `/oracle`-Einnahmepfad.
5. **Ohne Migration keine Spends.** Fehlt `ln_ops_ledger_v2.jsonl`, während v1 noch
   Zeilen hat, verweigert die Wert-Schicht jeden Spend („run the migration first")
   statt eine zweite Geldhistorie mit Cap-Reset bei 0 zu beginnen. Ein Deploy VOR
   der Migration ist damit ungefährlich, aber sende-unfähig.

## 0. Dry-Run gegen eine KOPIE (Pflicht, vor allem anderen)

Nie zuerst gegen die Live-Datei. Kopie ziehen, Migration gegen die Kopie fahren,
Report lesen:

    cp artifacts/ln_ops_ledger.jsonl /tmp/ln_ops_ledger_copy.jsonl
    python scripts/redact_ln_ops_ledger.py \
      /tmp/ln_ops_ledger_copy.jsonl /tmp/ln_ops_ledger_copy.v2.jsonl --no-attest

`--no-attest` hält den Dry-Run aus der Truth-Kette heraus. Prüfen:

* `verification.ok` = `true` und `verification.open_intents` = `[]`
* `source_records` = Anzahl der erwarteten Zeilen, `written_records` = 2 × migrierte
  Zeilen (jede terminale Legacy-Zeile bekommt einen synthetischen `intent` davor)
* `skipped` durchlesen — **jede** übersprungene Zeile steht dort einzeln mit
  `{line, reason, state}`. Erwartbar sind nur nicht-terminale Zustände
  (`planned`, `disabled`). Alles andere (`unparseable json`) ist ein Befund, kein
  Rauschen → erst klären, dann migrieren.

Die synthetischen Intent-Zeilen tragen Provenance: `"migrated": true`,
`"synthetic_intent": true`, `"source_line": <n>`. Ein Leser kann sie damit nie für eine
echte, vom Operator vorbereitete Absicht halten. Die zugehörige Outcome-Zeile trägt
`migrated` + `source_line`, aber **kein** `synthetic_intent` (sie ist echt).

Doppelte `payment_hash` in der Historie brechen die Migration **nicht** ab (M-12d):
der zweite Vorgang bekommt eine eigene Intent-Zeile mit eigener `source_line`. Die
M-4-Dedup-Sperre gilt nur für neue Zahlungen, nicht für die Wiedergabe der Vergangenheit.

## 1. Writer stoppen

    sudo systemctl stop kai-truth-anchor.timer
    sudo systemctl stop kai-server.service

`kai-truth-anchor.timer` zuerst: der Anchor-Lauf liest die Geld-Journal-Spitze und
würde eine halb umgestellte Datei attestieren. Achtung: `kai-server` stoppen
kaskadiert auf abhängige Timer/`kai-entry-watch` — das ist gewollt, aber einplanen.

Kontrolle (muss leer sein):

    systemctl list-units --state=running 'kai-*'

## 2. Original sichern (bleibt on-box)

    cp -a artifacts/ln_ops_ledger.jsonl artifacts/ln_ops_ledger.v1.jsonl
    sha256sum artifacts/ln_ops_ledger.jsonl artifacts/ln_ops_ledger.v1.jsonl

Beide Hashes müssen identisch sein. Diese Datei wird **nie** gelöscht.

## 3. Migration ausführen (attestiert)

    python scripts/redact_ln_ops_ledger.py \
      artifacts/ln_ops_ledger.jsonl artifacts/ln_ops_ledger_v2.jsonl

Der Report wird als `lightning_ops_migration` in die Truth-Kette geschrieben und
enthält `source_sha256`, `destination_sha256`, `verification` und
`truth_attestation.{seq,record_hash}`. Report vollständig sichern (Session-Log +
Attestation-seq notieren) — er ist der Nachweis, dass v2 aus genau diesem v1 entstand.

Gegenprobe unabhängig vom Report:

    python -c "from pathlib import Path; from app.lightning.ops_ledger import verify_ln_ops_ledger; \
      print(verify_ln_ops_ledger(Path('artifacts/ln_ops_ledger_v2.jsonl')))"

## 4. Writer wieder starten

    sudo systemctl start kai-server.service
    sudo systemctl start kai-truth-anchor.timer
    systemctl list-timers 'kai-*' | head

Danach einen Anchor-Lauf von Hand anstoßen und die Ausgabe lesen:

    sudo systemctl start kai-truth-anchor.service
    journalctl -u kai-truth-anchor.service -n 30 --no-pager

Erwartet: eine `ln-ops-tip attested=…` Zahl **ohne** `error=…`. Erscheint dort ein
`WARNING ln-ops-tip attestation skipped`, ist die v2-Datei nicht verifizierbar — die
OTS-Verankerung der übrigen Truth-Kette läuft trotzdem weiter (BL-1), aber der Befund
muss geklärt werden, bevor PR-C irgendetwas auf v2 umstellt.

## Rollback

**Vor PR-C** war der Rollback trivial: v2-Datei beiseiteschieben, fertig.

**Seit PR-C** hängt der Sendepfad an v2 — die Datei beiseitezuschieben macht ihn
sende-unfähig (Invariante 5), nicht rückgängig. Der Betrieb merkt trotzdem nur das:

* **Empfangen läuft weiter.** `/oracle`, L402 und die Einnahmen-Buchung berühren v2
  nicht. Ein v2-Problem kostet nie einen Sat Einnahmen.
* **Spends werden abgelehnt**, mit der reparierbaren Ursache im Verdikt (Cockpit
  zeigt `denied: money journal …`). Kein halb gebuchter Spend, keine stille Lücke.

Echter Code-Rollback (nur wenn v2 grundsätzlich unbrauchbar ist): den PR-C-Commit
zurücknehmen und deployen — `append_ln_op`/`spent_today_sat` sind genau dafür
unverändert stehengeblieben und arbeiten sofort wieder auf dem nie angefassten v1.
Die zwischenzeitlich in v2 geschriebenen Geld-Events müssen dann von Hand nach v1
nachgetragen werden (Attestation-seq + Report als Beleg). Im Zweifel: Writer
gestoppt lassen und keine unverifizierte Datei einsetzen.

## Tail-Recovery — abgerissene letzte Zeile (M-5)

Ein Stromausfall mitten im Append hinterlässt eine halbe JSON-Zeile. Der v2-Writer
verweigert danach **jedes** weitere Geld-Event (auch `create_invoice`) mit

    LN ops ledger tail unreadable; refusing to fork the money journal
    — repair first: docs/runbooks/ln_ops_ledger_v2_migration.md (section 'Tail-Recovery')

Das ist Absicht: auf die letzte *lesbare* Zeile weiterzuketten würde das Geld-Journal
still forken. Reparatur:

1. **Writer stoppen** — sonst schreibt jemand in die Datei, die gerade repariert wird:

       sudo systemctl stop kai-truth-anchor.timer kai-server.service

2. **Diagnose** — welche Zeile ist kaputt, und ist es wirklich nur die letzte?

       python - <<'PY'
       import json, pathlib
       p = pathlib.Path("artifacts/ln_ops_ledger_v2.jsonl")
       lines = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
       for i, ln in enumerate(lines, 1):
           try:
               json.loads(ln)
           except ValueError as exc:
               print("BROKEN", i, "of", len(lines), "->", exc, "| bytes:", len(ln))
       PY

   * Nur die **letzte** Zeile kaputt → Schritt 3 (Truncate).
   * Eine Zeile in der **Mitte** kaputt → **NICHT** truncaten. Das ist Manipulation
     oder Datenträgerschaden, nicht ein abgerissener Schreibvorgang. Datei einfrieren,
     Kopie ziehen, Befund dokumentieren; Wiederanlauf nur über eine neue Migration aus
     dem on-box `ln_ops_ledger.v1.jsonl` plus dokumentierter Lücke.

3. **Backup + Truncate der halben Zeile** (nur die letzte, Writer gestoppt):

       cp -a artifacts/ln_ops_ledger_v2.jsonl artifacts/ln_ops_ledger_v2.torn.$(date -u +%Y%m%dT%H%M%SZ).jsonl
       python - <<'PY'
       import json, pathlib
       p = pathlib.Path("artifacts/ln_ops_ledger_v2.jsonl")
       lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
       while lines:
           try:
               json.loads(lines[-1]); break
           except ValueError:
               print("dropping torn tail:", lines[-1][:120]); lines.pop()
       p.write_text("".join(f"{ln}\n" for ln in lines if ln.strip()), encoding="utf-8")
       PY

   Die abgerissene Zeile ist **immer** entweder ein `intent` (dann wurde LND nie
   gerufen) oder ein `outcome` (dann bleibt der `intent` offen und die Reservierung
   im Tages-Cap bestehen — die sichere Richtung). Die Backup-Kopie mit dem Torso
   aufbewahren.

4. **Verify** — muss `ok: True` liefern:

       python -c "from pathlib import Path; from app.lightning.ops_ledger import verify_ln_ops_ledger; \
         print(verify_ln_ops_ledger(Path('artifacts/ln_ops_ledger_v2.jsonl')))"

   `open_intents` prüfen: jeder dort gelistete Intent ist ein Vorgang, dessen Ausgang
   unbekannt ist → gegen den Node abgleichen (Zahlungsstatus/Channel-Balancen),
   bevor eine neue Zahlung derselben Invoice erlaubt wird.

5. **Wiederanlauf**

       sudo systemctl start kai-server.service kai-truth-anchor.timer
       sudo systemctl start kai-truth-anchor.service   # Tip erneut attestieren

Merksatz: der Writer vertraut beim Anhängen der aktuellen Spitze — Manipulations-
*Evidenz* entsteht erst durch `verify_ln_ops_ledger()` und die attestierte, OTS-
verankerte Spitze. Ein Repair-Schritt ohne anschließendes Verify ist wertlos.
