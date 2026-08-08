# Lightning-Reconciliation (PR-D)

`kai-ln-reconcile.timer` schliesst ausschliesslich die Crash-Luecke zwischen einem
bereits fsync-ten v2-Intent und dessen fehlendem Terminal-Outcome. Er bezahlt und
wiederholt nichts. Der Node-Zugriff ist `GET /v1/payments` mit dem Read-Credential.

## Sicherheitsreihenfolge

1. Das komplette v2-Geldjournal wird unter Shared-Lock aus exakt einem Snapshot
   gelesen und voll verifiziert. Missing, Lock-/Lesefehler oder Kettenfehler sind
   ein harter Fehler.
2. Der letzte verifizierte Truth-Eintrag vom Typ `lightning_ops_tip` muss mit Hash
   **und** Seq in diesem Snapshot vorkommen. Fehlt er, bleibt jedes Intent offen;
   der Node wird nicht gelesen und das Geldjournal nicht geschrieben.
3. Nur wenn offene `pay_invoice`-Intents existieren, wird die gesamte paginierte,
   redigierte LND-Payment-Historie gelesen. Ein Teilscan gilt als gar kein Scan.
4. Journal und Truth-Tip werden nach dem Node-Scan erneut geprueft. Erst danach
   darf genau ein eindeutiges `SUCCEEDED` als `executed` bzw. `FAILED` als `error`
   angehaengt werden. Hash und Betrag muessen dem versiegelten Intent entsprechen.
   Unmatched, doppelte, unbekannte oder laufende Zustaende bleiben offen und laut.

Jeder Lauf schreibt eine fsync-te, streng gelockte und redigierte Zeile nach
`artifacts/lightning/ln_reconciliation.jsonl`. Exit 0 bedeutet `status=ok`;
`attention` oder `error` liefern Exit 1 und werden dadurch in systemd sichtbar.

## Installation und Aktivierung

Die Units sind Teil von `UNITS`, aber absichtlich **nicht** von
`ENABLE_ON_INSTALL`. Sie haben kein `Requires=` und der Timer ist weder persistent
noch ein Boot-Hook. Aktivierung erst nach versiegelter Shadow-Prae-Registrierung:

```bash
CRITERIA='In the first 96 enabled shadow runs within 7d: all runs pass Truth-tip containment and zero unsupported, unmatched, ambiguous, amount-mismatched or nonterminal intents are terminalised; every naturally observed uniquely matched terminal BOLT11 payment with equal hash and amount is appended exactly once by the next completed run. If zero eligible open-intent incidents occur, transition-effectiveness remains INSUFFICIENT_N and only the safety/tip axis may pass. This is no readiness, capital, alpha or revenue claim.'
.venv/bin/trading-bot trading prereg-register \
  --name ln_reconciliation_shadow_integrity_v1 \
  --direction neutral --horizon 7d --sample-target 96 \
  --family money_path_integrity --success-criteria "$CRITERIA"
sudo install -m 0644 deploy/systemd/kai-ln-reconcile.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kai-ln-reconcile.timer
sudo systemctl start kai-ln-reconcile.service
systemctl status kai-ln-reconcile.service kai-ln-reconcile.timer
```

Bei `attested_tip_not_in_journal`, `truth_ledger_invalid`,
`money_journal_invalid` oder `node_scan_failed:*` nichts reparieren/abschneiden und
keinen Outcome manuell setzen. Timer deaktivieren, Originaldateien sichern und die
Truth-/v2-Kette gegen `docs/runbooks/ln_ops_ledger_v2_migration.md` untersuchen.

Rollback der Automatik (keine Datenloeschung):

```bash
sudo systemctl disable --now kai-ln-reconcile.timer
```
