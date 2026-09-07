# Lightning-Reconciliation — ARCHIV (ADR 0018 §12, PR 2)

> **Dieser Runbook beschreibt einen Reconciler, den es nicht mehr gibt.**
> `app/lightning/reconciliation.py` und `scripts/ln_reconciliation_eval.py`
> sind am 2026-09-07 mit dem Rückbau des alten Lightning-Wertpfads gelöscht
> worden. Er bleibt liegen, weil er beschreibt, **wie** die Zeilen in
> `artifacts/lightning/ln_reconciliation.jsonl` und das Verdikt in
> `artifacts/research/ln_reconciliation_verdict.jsonl` entstanden sind — beide
> Dateien liegen weiterhin am Gerät und sind Beweismittel.
>
> **Der lebende Reconciler steht in `docs/runbooks/payment_fabric.md`.**
> `kai-ln-reconcile.timer` gibt es weiter, unter demselben Namen; er fährt seit
> PR 2 ausschließlich `reconcile_payments()` über
> `artifacts/payments/payment_journal.jsonl`.

## Was der alte Reconciler tat

`kai-ln-reconcile.timer` schloss ausschliesslich die Crash-Luecke zwischen einem
bereits fsync-ten v2-Intent und dessen fehlendem Terminal-Outcome. Er bezahlte und
wiederholte nichts. Der Node-Zugriff war `GET /v1/payments` mit dem Read-Credential.

Sicherheitsreihenfolge (zur Beurteilung der Altzeilen):

1. Das komplette v2-Geldjournal wurde unter Shared-Lock aus exakt einem Snapshot
   gelesen und voll verifiziert. Missing, Lock-/Lesefehler oder Kettenfehler waren
   ein harter Fehler.
2. Der letzte verifizierte Truth-Eintrag vom Typ `lightning_ops_tip` musste mit Hash
   **und** Seq in diesem Snapshot vorkommen. Fehlte er, blieb jedes Intent offen;
   der Node wurde nicht gelesen und das Geldjournal nicht geschrieben.
3. Nur wenn offene `pay_invoice`-Intents existierten, wurde die gesamte paginierte,
   redigierte LND-Payment-Historie gelesen. Ein Teilscan galt als gar kein Scan.
4. Journal und Truth-Tip wurden nach dem Node-Scan erneut geprueft. Erst danach
   durfte genau ein eindeutiges `SUCCEEDED` als `executed` bzw. `FAILED` als `error`
   angehaengt werden. Hash und Betrag mussten dem versiegelten Intent entsprechen.
   Unmatched, doppelte, unbekannte oder laufende Zustaende blieben offen und laut.

Jeder Lauf schrieb eine fsync-te, streng gelockte und redigierte Zeile nach
`artifacts/lightning/ln_reconciliation.jsonl`.

## Die versiegelte Prä-Registrierung `0879a65c5fd01f65`

Prä-Reg `ln_reconciliation_shadow_integrity_v1`, Familie `money_path_integrity`,
Fenster 2026-08-08 → 2026-08-15, Stichprobenziel 96.

**Verdikt `PASS`, gezogen am 2026-08-27, Fenster geschlossen, `attested: true`.**
Es steht in `artifacts/research/ln_reconciliation_verdict.jsonl`, wird von
`app/research/prereg_reconciliation.py` gelesen und ist in
`config/prereg_supervision.json` archiviert. Der stündliche Evaluator
(`kai-ln-reconcile-verdict.timer`) hätte ab Fensterschluss nur noch dasselbe
Archiv neu gelesen und ist deshalb mit PR 2 entfallen — **das Verdikt bleibt.**

Der Evaluator-Quelltext ist über die Git-Historie erreichbar:

```bash
git show 2ebe54d8~1:scripts/ln_reconciliation_eval.py
```

## Operator-Schritt vor dem Deploy dieses Release

`pi_apply_systemd_units.sh` meldet verwaiste Units als `ORPHAN`, **entfernt sie
aber nie**. Ohne diesen Schritt läuft eine installierte Unit gegen ein
gelöschtes Skript und `kai-unit-failure-notify` schlägt stündlich.

```bash
sudo systemctl disable --now kai-ln-reconcile-verdict.timer
sudo rm -f /etc/systemd/system/kai-ln-reconcile-verdict.service \
           /etc/systemd/system/kai-ln-reconcile-verdict.timer
sudo systemctl daemon-reload
systemctl list-timers 'kai-ln-reconcile*'   # nur noch kai-ln-reconcile.timer
```

`kai-ln-reconcile.timer` bleibt **aktiv**. Er ist seit PR 2 die Lebend-Wache des
Geldpfads: bleibt er aus, meldet `check_payment_reconciliation` das Alter von
`last_run_utc` in `artifacts/payments/reconcile_state.json` nach 45 Minuten als
P0-Befund.

## Wenn eine Altzeile Fragen aufwirft

Bei `attested_tip_not_in_journal`, `truth_ledger_invalid`, `money_journal_invalid`
oder `node_scan_failed:*` in einer historischen Zeile gilt unverändert: nichts
reparieren, nichts abschneiden, keinen Outcome manuell setzen. Originaldateien
sichern und die Truth-/v2-Kette gegen `docs/runbooks/ln_ops_ledger_v2_migration.md`
untersuchen.
