# Runbook: KI-Zahlungsvorschlaege fuer KAI-Pay (D-297)

KAI signiert Zahlungsvorschlaege, die Wallet KAI-Pay prueft sie, und bezahlt wird nur, wenn
der Nutzer in der Wallet bestaetigt (ADR 0021 I5, Addendum E5). KAI sendet dabei kein Geld,
haelt keinen Zahlungszustand und beruehrt den Zahlungskern nicht (I3).

| Teil | Ort |
|---|---|
| Format und Signatur | `app/kai_pay_bridge/proposal.py`, byte-kompatibel zu `packages/proposal/proposal.ts` (kai-pay) |
| Schluessel | `~/kai-secrets/kai-pay/proposal-source.pem` (PEM PKCS#8, P-256, 0600, Verzeichnis 0700) |
| Telegram | `/vorschlag`, `/vorschlag quelle`, `/vorschlag <Ziel> <sat> <Zweck>` |
| CLI | `kai kaipay-proposal init` / `show` / `new <Ziel> <sat> <Zweck>` |
| Settings | `APP_KAIPAY_PROPOSAL_*` (`app/kai_pay_bridge/settings.py`, Block in `.env.example`) |

## 1. Quelle anlegen (einmalig, auf der Pi)

```bash
# als ubuntu (Dienst-User von kai-server, das den Bot faehrt) - HOME bestimmt den Schluesselpfad
cd /home/kai/current
.venv/bin/python -m app.cli.main kaipay-proposal init
```

- Der Befehl legt den Schluessel an und gibt den **Fingerabdruck** aus (8 Vierergruppen).
- Eine vorhandene Datei wird nie ueberschrieben, der Befehl endet dann mit Exit 1.
- Der private Schluessel wird nie ausgegeben, auch nicht in Logs.
- Die Datei liegt unter `~/kai-secrets` und wandert mit dem naechsten Vault-Lauf verschluesselt
  auf die Offsite-Platte.

## 2. Quelle in der Wallet einrichten

1. Telegram: `/vorschlag quelle`.
2. Den Link „In der Wallet einrichten“ auf dem Handy oeffnen. Er fuehrt in die Web-Wallet oder
   die Beta-App, beide unter `#kaisrc=`.
3. Pruefen, dass der Fingerabdruck in der Wallet dem aus Schritt 1 gleicht.
4. In der Wallet erlaubte Empfaenger und Budget (Tag/Monat) eintragen und speichern.

Ohne diese Einrichtung zeigt die Wallet bei jedem Vorschlag „Diese Quelle ist in deiner Wallet
nicht eingerichtet“ und bereitet nichts vor.

## 3. Vorschlag erzeugen

```
/vorschlag name@breez.tips 2100 Server Oktober
```

Die Antwort enthaelt den Link „In KAI-Pay pruefen“ (`#kaiprop=`) und den Code zum Einfuegen
unter „Senden“. Der Vorschlag ist 24 h gueltig (`APP_KAIPAY_PROPOSAL_TTL_HOURS`, hoechstens
168).

Die Wallet prueft:

- Signatur, Laufzeit und Wiederholung
- ob der Empfaenger erlaubt ist (massgeblich ist der vorbereitete Zahlungsweg)
- ob das Budget inklusive Gebuehr reicht

Erst danach zeigt sie „Bestaetigen“.

Abgelehnt wird schon in KAI, also bevor ueberhaupt signiert wird:

- `sat` ausserhalb von 1 bis 1.000.000
- ein Zweck ueber 140 Zeichen oder mit Steuer- oder Richtungszeichen
- ein Ziel mit Leerraum

## 4. Rotation, Verlust, Kompromittierung

1. Alte Datei auf der Pi verschieben (nicht loeschen, bis die neue Quelle laeuft):
   `mv ~/kai-secrets/kai-pay/proposal-source.pem ~/kai-secrets/kai-pay/proposal-source.pem.alt`
2. `kai kaipay-proposal init` legt eine neue Quelle mit neuer Kennung an.
3. Die neue Quelle wie in Abschnitt 2 einrichten.
4. Die alte Quelle in der Wallet unter „KI-Zahlungsvorschlaege“ entfernen.

Ab dann lehnt die Wallet alle Vorschlaege der alten Kennung ab.

Bei Verdacht auf Kompromittierung ist Schritt 4 der entscheidende. Ein gestohlener Schluessel
kann nur Vorschlaege erzeugen, keine Zahlungen. Begrenzt wird der Schaden durch:

- die erlaubten Empfaenger
- das Budget
- die Bestaetigung durch den Nutzer

## 5. Diagnose

| Meldung | Ursache |
|---|---|
| „Keine Vorschlagsquelle angelegt“ | Schluesseldatei fehlt: Abschnitt 1 |
| „Vorschlagsquelle unbrauchbar“ | Datei ist kein lesbarer P-256-Schluessel: Abschnitt 4 |
| Wallet: „… nicht eingerichtet“ / „passt nicht zur eingerichteten Quelle“ | Quelle nicht eingerichtet oder rotiert: Abschnitt 2 |
| Wallet: „Der Vorschlag ist abgelaufen.“ | Vorschlag aelter als die TTL: neu erzeugen |
| Wallet: „noch nicht gueltig“ | Uhr des Handys oder der Pi geht falsch |
| Wallet: „Empfaenger … nicht erlaubt“ | Ziel fehlt in der Liste der erlaubten Empfaenger der Quelle |
| Wallet: „uebersteigt das verbleibende Budget“ | Tages- oder Monatsbudget der Quelle erschoepft |

`kai kaipay-proposal show` zeigt Fingerabdruck und Einrichtungs-Link erneut. Beides ist
oeffentlich.
