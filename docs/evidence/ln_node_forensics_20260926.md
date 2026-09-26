# Lightning-Node-Forensik 2026-09-26

Die Prüfung lief nur lesend: `admin@192.168.178.51`, lnd 0.19.3, bitcoind ohne txindex und nicht gepruned.
Operator-Auftrag vom 26.09.: „Force-Close-Altlast muss analysiert und festgehalten werden“.
Referenz ist DECISION_LOG D-287, der Vorbefund steht in D-286 (c).

## 1. Force-Close-Altlast `cf5fe058…:0` (25 815 sat „Limbo“)

**Verdikt: Die Mittel sind geborgen, bewiesen am Bitcoin-Blockchain-Zustand. Der Limbo-Betrag ist ein reiner Buchungsrest in lnd.**

| Schritt | Beleg | Befund |
|---|---|---|
| Kanal | `lncli pendingchannels` | `cf5fe0585efd98789aa8e8c5d9298729666aade45bb02f2946cb0b2cda0f9011:0`, 1 000 000 sat Kapazität, Initiator REMOTE, `STATIC_REMOTE_KEY`, lokal 25 815 sat, `limbo_balance` 25 815, `recovered_balance` 0, `maturity_height` 862 481, `anchor` LIMBO |
| Closing-Tx | Blockscan per `bitcoin-cli getblock`, danach `getrawtransaction … <blockhash>` | `58ae2f35d12170e4a5b62cc8b6f35eff52f8d7319464e2853dc0c1bc30747a8d`, bestätigt auf **Höhe 862 481**. Einziger Input ist `cf5fe058…:0`. `vout 0` = **0,00025815 BTC, `witness_v0_keyhash`** (to_remote, statischer Schlüssel). `vout 1` = 0,00973203 BTC P2WSH (Gegenseite) |
| Sweep | `lncli listchaintxns` + `bitcoin-cli decoderawtransaction` | `2fd51c3d8ab8fc8b0ec2a3757083f85513d925071b7fd937e4d1282011ee6f02`, **Höhe 953 849**. Inputs `94f9d604…:1` und `58ae2f35…:0`, beide mit Witness [Signatur, 33-Byte-Pubkey], also P2WKH-Ausgaben mit einer einzelnen Signatur, `nSequence` 0xffffffff. Einziger Output 845 499 sat an eine **eigene** Taproot-Adresse (`is_our_address=true`) |
| Bilanz | Rechnung | 820 634 (`94f9…:1`, REMOTE_FORCE_CLOSE `7d32c005…:1`) + 25 815 (`58ae…:0`) = 846 449 sat Eingang. Davon 845 499 sat Ausgang, **950 sat Gebühr** |

**Warum lnd den Fall offen hält:** Den Sweep hat nicht der lnd-Resolver selbst ausgeführt. Beide Inputs zeigt lnd mit `is_our_output=false`, einen Wallet-Label gibt es nicht, und das Muster „ein Sweep über mehrere `to_remote`-Ausgänge verschiedener Kanäle“ passt zu einem externen Recovery-Werkzeug (chantools/SCB). Deshalb steht der ChannelArbitrator für `cf5fe058…` dauerhaft in `StateWaitingFullResolution` (lnd-Log, alle paar Minuten `skipping reading close events`), und `pending_sweeps` ist leer.

**Wirkung in KAI:** Keine. `app/lightning/treasury.py:28` weist `total_limbo_sat` getrennt aus und zählt es nie als verfügbares Kapital. Das Dashboard zeigt es nur als `limbo_state`/`limbo_reason` an.

**Aufräumen (Operator-Entscheid, irreversibel):** `lncli abandonchannel --chan_point cf5fe058…:0 --i_know_what_i_am_doing` würde den Eintrag aus der lnd-Datenbank entfernen. Weil der Output nachweislich ausgegeben ist, geht dabei kein Geld verloren. Trotzdem gilt:
- vorher eine frische SCB-Sicherung außerhalb des Nodes;
- der Eingriff in die Channel-DB lässt sich nicht zurücknehmen;
- er ist rein kosmetisch.

**Empfehlung: nicht aufräumen**, solange der Eintrag nicht stört.

## 2. Drei LOCAL_FORCE_CLOSEs auf Höhe 953 902 (neu gefunden)

**Verdikt: geborgen und bewiesen.** `lncli closedchannels` zeigt `settled_balance 0` und bei den Resolutions `outcome UNCLAIMED`. On-chain ist aber alles eingesammelt:

| Kanal (channel_point) | Kapazität | Commit-Output | Betrag |
|---|---|---|---|
| `bd0e5ecf…:0` | 100 000 | `9480a3bb…:0` | 97 768 |
| `b6eaae7b…:0` | 100 000 | `63fbe72c…:1` | 96 852 |
| `6e7933ee…:1` | 113 926 | `38262057…:0` | 112 733 |

Sweep `e7618e85707428f2cdb701e0f539075a580ce9f93235b2271a70c66c64514460` auf **Höhe 954 067**:
- Alle drei Inputs laufen über den CSV-Zweig des `to_local`-Skripts: Witness [Signatur, leer, 77-Byte-Skript], `nSequence` 144.
- Eingang 307 353 sat, Ausgang **305 886 sat an eine eigene Taproot-Adresse**, Gebühr 1 467 sat.
- Der Buchungsrest in lnd hat dieselbe Ursache wie in Abschnitt 1: Der Sweep lief außerhalb des Resolvers.

Der REMOTE_FORCE_CLOSE `7d32c005…:1` (820 634 sat) ist über `2fd51c3d…` geborgen, siehe Abschnitt 1.

**Gesamtbild der Force-Closes:** 820 634 + 25 815 + 307 353 = 1 153 802 sat standen in Commit-Outputs. Davon sind 1 151 385 sat in der eigenen Wallet angekommen, 2 417 sat gingen an Sweep-Gebühren. **Es fehlt kein Satoshi.** Die On-chain-Wallet steht bei 1 548 197 sat confirmed.

## 3. Der 15-Minuten-Minter läuft weiter

Auf dem Node entsteht alle 15 min ein offenes 1-sat-Invoice mit dem Memo `kai-pay: KAI receive` (belegt 26.09.: 07:26:44Z, 07:41:46Z, 07:56:46Z, 08:11:48Z; bezahlt: keines). Das ist derselbe Befund wie im Master-Audit vom 27.08. (`LIGHTNING_GAP_ANALYSIS.md`, A12-063–065: 1 483 Invoices in 15,42 Tagen).
- Weder KAI-Code (`app/`, `scripts/`) noch die Logs von kai-server oder das kai-pay-Repo (Strang B) erzeugen dieses Memo. Das Payment-Journal bleibt unverändert.
- Auf dem Node ist **LNbits aktiv**. Das Präfix `kai-pay: ` ist dort das Präfix des Pay-Links, der Minter ist also sehr wahrscheinlich ein LNbits-Pay-Link oder eine LNbits-Anzeige, die ihr Invoice alle 15 min erneuert.
- Wirkung: keine Zahlung, keine Buchung in KAI, nur Einträge in der Invoice-DB. KAIs Receive-Seite bucht eigene Invoices nur über ihr Journal.
- **Offen (Operator):** in LNbits den Pay-Link oder das Widget „KAI receive“ finden und abschalten, falls nicht gewollt.

## 4. `LN_SECRET_BACKUP_KEY` fehlt auf dem Laptop

- **Zweck:** ein eigener Schlüssel für Authentisierungsmaterial (Pi `~/kai-secrets`: HOTP-Seed und lnd-Macaroons, dazu Laptop-SSH und Anmeldungen). Er ist getrennt von `KAI_BACKUP_PASSPHRASE`, damit nicht eine einzige Passphrase Daten, Macaroons und Zweitfaktor zugleich öffnet (Operator-Direktive 27.08.).
- **Stand 26.09.:** Der Schlüssel **existiert**. Laut `KAI-mirror/node_pinning.json` → `ln_secret_backup_key_policy` hat der Operator am 2026-08-27 festgelegt: 42 Zeichen aus dem KeePass-Generator, abgelegt in KeePass, zur Laufzeit nur als Umgebungsvariable.
- Auf dem Laptop ist er weder als Umgebungsvariable (Process/User/Machine) noch als DPAPI-Arbeitskopie `%USERPROFILE%\.kai\vault\ln_secret_backup_key.dpapi` vorhanden. Deshalb meldet der Vault-Lauf vom 25.09. `~/kai-secrets` als **PARTIAL**, und das Auth-Material hat derzeit keine Kopie außerhalb der Pi.
- **Risiko:** Ein Verlust der Pi kostet kein Geld. Macaroons lassen sich am Node neu backen, HOTP lässt sich per `scripts/hotp_bootstrap.py` neu einrichten. Es wäre aber ein Wiederanlauf ohne Sicherung.
- **Korrektur (Operator, etwa 1 min):** `pwsh -File C:\Users\sasch\KAI-mirror\scripts\kai_vault.ps1 -StoreLnKey` ausführen und den Schlüssel aus KeePass einfügen. Er wird DPAPI-geschützt abgelegt, das Original bleibt in KeePass. Beim nächsten Vault-Lauf wird `~/kai-secrets` dann VERIFIED statt PARTIAL.
- Nebenbefund: Die aktuelle KeePass-Datenbank liegt nur unter `Downloads\` (Stand 14.09.). Die OneDrive-Kopie stammt von 2022. Punkt W0-1 aus MindBlow 2.0 (Zweitkopie und Notfallblatt) bleibt offen.

## Befehle (alle nur lesend)

```
lncli pendingchannels | closedchannels | listchaintxns | listpayments --include_incomplete | listinvoices
bitcoin-cli getblockhash <h> ; bitcoin-cli getblock <hash> 1 ; bitcoin-cli getrawtransaction <txid> 1 <blockhash>
bitcoin-cli decoderawtransaction <raw_tx_hex aus listchaintxns>
```
