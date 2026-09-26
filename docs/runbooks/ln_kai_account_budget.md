# Node-seitiges Budget für KAIs Sendeschlüssel (litd-Account)

> **Status: ENTWURF, nichts ausgeführt (26.09.2026).** Der Operator hat die Vorbereitung beauftragt. Jeder Schritt am Node und jeder Send braucht eine eigene Freigabe. Referenzen: D-288 (Lückenregister), D-289 (fremde Ausgaben werden gemeldet).

## 1. Warum

KAIs Sendeschutz (per_payment_max und daily_hard_cap je 1000 sat, genau ein Empfänger, HOTP ab 1 sat) steckt **nur in KAIs Software** (`PaymentService`). Der Node kennt weder den HOTP noch die Caps. Für einen Send prüft lnd ausschließlich, ob ein gültiger Macaroon mit Senderecht und das TLS-Zertifikat vorliegen.

- Auf der Pi liegt `~/kai-secrets/lnd/kai-payment.macaroon` (Rechte `600`, Benutzer `ubuntu`). Der Go-live-Preflight (GO) belegt ohne Geldbewegung, dass er **sendefähig** ist.
- **Alle** release-gebundenen Dienste und `cloudflared` laufen als `ubuntu`: kai-server, agent-worker, tg-listener, litellm, entry-watch, liquidation-stream, cloudflared.
- Folge: Eine Codeausführung in irgendeinem dieser Dienste (Abhängigkeit, Webhook, Tunnel) reicht, um **am KAI-Code vorbei** direkt am Node per Lightning zu zahlen, ohne HOTP und ohne Cap. Die Obergrenze wäre dann das **ausgehende Kanalguthaben** (am 26.09. lokal ≈ 370 000 sat).
- **On-chain ist über diesen Schlüssel nicht erreichbar.** `kai-payment.macaroon` trägt laut `docs/lightning_macaroon_matrix.md` nur `offchain:read offchain:write`, kein `onchain:write`. Der tatsächliche Scope ist am Node noch nicht nachgelesen (Matrix, offener Punkt), er gehört zu Schritt 4.4.
- D-289 sorgt dafür, dass so eine Ausgabe **auffällt**. Ein litd-Account sorgt dafür, dass sie **begrenzt** ist.

Einordnung: Ein gezielter Angriff ist unwahrscheinlich, der mögliche Schaden aber unbegrenzt. Priorität P2.

## 2. Was ein litd-Account leistet (belegt)

Quelle: Lightning Labs, *LND Accounts* (docs.lightning.engineering, gelesen am 26.09.2026).

- Ein Account ist ein **virtuelles Unterkonto** mit eigenem Macaroon. Das Limit setzt der **Node** über den RPC-Middleware-Interceptor durch, nicht der Client.
- Jede Zahlung wird **einschließlich Routing-Gebühren** vom virtuellen Guthaben abgezogen. Übersteigen die laufenden Zahlungen das Guthaben, wird abgelehnt.
- Einschränkung: litd prüft nicht, ob die Summe aller Accounts durch den Kanal gedeckt ist. Für uns ist das irrelevant, weil es genau einen Account mit kleinem Betrag gibt.
- Erzeugen: `litcli accounts create <sat> --save_to <pfad>`.

## 3. Ist-Stand am Node (gelesen am 26.09.)

- RaspiBlitz `codeVersion="1.12.1"`, lnd `0.19.3-beta`.
- litd ist **nicht installiert** (`systemctl is-active litd` ergibt `inactive`), aber über `config.scripts/bonus.lit.sh` verfügbar.

## 4. Vor der Umsetzung zu klären (NICHT dokumentiert, muss am Node bewiesen werden)

1. **Fail-closed bei litd-Ausfall.** Wird eine Anfrage mit Account-Macaroon abgelehnt, wenn litd nicht läuft (keine Middleware registriert)? Die Doku sagt es nicht ausdrücklich. Wäre die Anfrage dann ohne Limit erlaubt, wäre der Account wertlos. Das ist **Abnahmetest A3** und ein K.-o.-Kriterium.
2. **Endpunkt.** Kann KAI mit dem Account-Macaroon weiter direkt `lnd`-REST (`:8080`) ansprechen, wobei lnd an die Middleware weiterreicht? Oder muss KAI über den litd-Port gehen? Davon hängt ab, ob sich an `APP_LN_*` mehr als der Macaroon-Pfad ändert.
3. **lnd-Neustart.** `rpcmiddleware.enable=true` in `lnd.conf` braucht einen lnd-Neustart. Dabei trennen sich die Kanäle kurz. Nur mit Drain-Check: kein Intent unterwegs (`scripts/payment_drain_check.py`).
4. **Alten Schlüssel wirklich entwerten.** Nur den Pfad zu tauschen genügt nicht, solange `kai-payment.macaroon` gültig bleibt. Dazu prüfen:
   - Mit welchem `root_key_id` wurde er gebacken (`lncli printmacaroon`)?
   - Ist es `0` (Standard), trifft `lncli deletemacaroonid 0` **auch** `admin.macaroon`, `readonly.macaroon` und `invoice.macaroon`. Dann ist eine gezielte Entwertung nicht möglich, und es braucht einen geplanten Neuaufbau aller Macaroons.
5. **Receive-Pfad.** KAI empfängt mit `kai-invoice.macaroon`, das bleibt getrennt. Ein Account-Macaroon verbucht eingehende Zahlungen als Guthaben des Accounts. Ob der Self-Use-Receive künftig über den Account laufen soll, ist offen.
6. **Reconcile.** Das Lesen mit `readonly.macaroon` bleibt unverändert. Prüfen, dass `ListPayments` mit dem Readonly-Macaroon weiter **alle** Node-Zahlungen sieht, damit D-289 nichts verliert.

## 5. Ablauf (Entwurf)

| # | Schritt | Wer | Rückweg |
|---|---|---|---|
| 0 | Frische SCB-Sicherung außerhalb des Nodes, Drain-Check grün, kein Pilot und kein Release parallel | Claude (prüft) | – |
| 1 | litd über `bonus.lit.sh on` installieren. Prüfen, was das Skript an `lnd.conf` ändert (`rpcmiddleware.enable`) und ob es lnd neu startet | Operator (sudo) | `bonus.lit.sh off` |
| 2 | Punkte 4.1 und 4.2 beweisen, mit einem **Test-Account über 1 sat** und ohne echten Send: Probe über eine ungültige Rechnung, wie beim Armed-Probe im Preflight | Claude + Operator | Test-Account löschen |
| 3 | KAI-Account anlegen: Budget **5 000 sat** (Vorschlag), Macaroon nach `~/kai-secrets/lnd/kai-account.macaroon` (Rechte `600`) | Operator | Account löschen |
| 4 | `.env`: `APP_LN_PAYMENT_MACAROON_PATH` auf den Account-Macaroon umstellen (`scripts/env_backup.sh` vorher). kai-server neu starten | Operator + Claude | alter Pfad, Restart |
| 5 | Abnahme (Abschnitt 6) | Claude + Operator | Schritt 4 zurück |
| 6 | Alten `kai-payment.macaroon` entwerten (nur wenn 4.4 das gezielt erlaubt), sonst eigenes Vorhaben „Macaroon-Neuaufbau“ | Operator | – (irreversibel) |

## 6. Abnahme

- **A1:** Der Go-live-Preflight mit dem Account-Macaroon meldet GO.
- **A2:** `/pay` über 100 sat mit HOTP wird SETTLED. `fee_actual_msat` ist gleich lnd `fee_msat`. Das Account-Guthaben sinkt um Betrag plus Gebühr.
- **A3 (K.-o.):** Mit gestopptem litd wird ein Send mit dem Account-Macaroon **abgelehnt**, geprüft per Probe mit einer ungültigen Rechnung, ohne Geld.
- **A4:** Ein Send über dem Account-Guthaben wird **vom Node** abgelehnt, auch mit korrektem HOTP. Das ist der eigentliche Beweis, dass das Limit nicht mehr nur in KAIs Software sitzt.
- **A5:** Reconcile `ok` und `complete`. Eine Wallet-Zahlung am KAI-Account vorbei erscheint weiter als `unattributed` (D-289).

## 7. Offene Operator-Entscheide

- Die Budgethöhe (Vorschlag 5 000 sat, das Fünffache des Tages-Caps) und wie aufgefüllt wird (manuell per `litcli accounts update`).
- Ob die Alltags-Wallet ebenfalls einen eigenen Account bekommt.
- Ob der alte Macaroon gezielt entwertet werden kann (4.4) oder ein Neuaufbau aller Macaroons geplant werden muss.

## 8. Alternative oder Ergänzung ohne litd

Die Dienste bekämen getrennte Unix-Benutzer, und nur der sendende Prozess (kai-server) dürfte `kai-payment.macaroon` lesen. Das verkleinert die Angriffsfläche (cloudflared, litellm und tg-listener könnten den Schlüssel nicht mehr lesen), **begrenzt aber keinen Betrag**, denn kai-server selbst ist über das Dashboard exponiert. Das ergänzt den Account, ersetzt ihn aber nicht.
