# ADR 0021: KAI-Pay-Produkt, eine selbstverwahrte Wallet ausserhalb des KAI-Kerns

- **Status:** **ACCEPTED, Architekturgrenze** (Operator-Entscheid 2026-09-25, D-285).
  Freigegeben sind die Produktrichtung und die Grenze zum KAI-Kern. Oeffentlicher Betrieb
  ist **nicht** freigegeben. Dafuer braucht es vorher die Etappe E6: Anwaltsfreigabe,
  Rechtstraeger, Sicherheitspruefung und Recovery-Drills.
- **Datum:** 2026-09-25
- **Betroffen:** nur Dokumentation im KAI-Repo. Der Produktcode entsteht im eigenen Repo `kai-pay`.
- **Baut auf:** [ADR 0016](0016-sovereign-value-os-self-use.md) (Self-Use),
  [ADR 0018](0018-payment-fabric-control-plane.md) (Payment Control Plane), D-277, D-CORE-005/006.
- **Aendert:** Die Drittprodukt-Sperre aus ADR 0016 gilt **nicht** fuer das Produkt KAI-Pay
  (Strang B), und zwar nur fuer dieses.
- **Aendert nicht:** `app/payments`, `app/pay`, `app/lightning`, den Sendepfad nach D-277, die
  Policy-Kette, die Journal-Invarianten aus ADR 0018 §4/§5, D-277 (4) fuer KAI selbst
  (keine Alltags-Wallet-UX im KAI-Dashboard) und D-CORE-006 (kein Benutzerkonto im KAI-Kern).

## Kontext

Der Operator will KAI-Pay als eigenstaendiges Produkt: Webseite, responsive Web-App und spaeter
Store-Apps. Funktional orientiert es sich an Wallet of Satoshi (WoS), gestaltet wird es im KAI-Design.
Die Domains kai-pay.net, .org, .online und .world sind vorhanden. Hosting laeuft nur ueber Cloudflare,
einen Webhoster gibt es nicht.

Stand im KAI-Repo (Mainline `aacb10bd`):

- KAI stellt Rechnungen aus und empfaengt fuer genau einen Operator (`app/api/routers/pay.py`).
- Gesendet wird nur per Telegram mit HOTP an Empfaenger auf einer Allowlist.
- Es gibt keine Nutzerkonten und keine Mandantentrennung im Journal.
- Die Anmeldung beruht allein auf dem Header, den Cloudflare Access setzt.
- Ein Umbau des Kerns zur Mehrnutzer-Wallet wuerde jede Invariante aus ADR 0018 brechen: ein Journal,
  ein sendender Prozess, eine Wahrheit.

Rechtslage, ohne Rechtsrat (BaFin-Merkblatt vom 03.01.2025, Ende der deutschen MiCAR-Uebergangsfrist
am 31.12.2025):

- Wer fuer Kunden Kryptowerte oder private Schluessel verwahrt, braucht eine CASP-Erlaubnis.
- Reine Selbstverwahrungs-Software ist erlaubnisfrei. Massstab ist, ob der Anbieter den Zugang
  wiederherstellen kann.
- Tausch, Fiat-Kauf und Stablecoin-Swap sind jeweils eigene erlaubnispflichtige Dienste.

WoS selbst bietet in der EU seit 2026 nur noch den selbstverwahrten Modus auf Spark (Lightspark) an
(Offenlegung v1.23 vom 25.08.2026). Die WoS-Referenz, die in der EU nachgebaut werden kann, ist damit
genau das selbstverwahrte Modell.

## Entscheidung

### 1. Zwei Straenge, streng getrennt

| | Strang A: KAI-Eigenbetrieb | Strang B: Produkt KAI-Pay |
|---|---|---|
| Zweck | KAIs eigene Einnahmen und Ausgaben (Self-Use) | Wallet fuer beliebige Nutzer |
| Geldwahrheit | `payment_journal.jsonl`, LND am eigenen Node | das Geraet des Nutzers plus Spark (Breez SDK) |
| Code | KAI-Repo, unveraendert | eigenes Repo `kai-pay` |
| Laufzeit | Pi 5, Named Tunnel `kai-trader.org` | Cloudflare (Workers Static Assets und Worker `/api`) |
| Regeln | D-277, D-CORE-005/006, ADR 0016/0018 | dieses ADR |

### 2. Verwahrungsgrenze (Invarianten fuer Strang B)

- **I1:** Seed und private Schluessel entstehen auf dem Geraet und verlassen es nie im Klartext.
  Weder KAI noch Breez kann eine Wallet wiederherstellen.
- **I2:** KAI-Server und Worker halten kein Geld, keine Schluessel und keine Salden. Serverseitig
  wird keine Nutzerzahlung ausgefuehrt.
- **I3:** Strang B hat keinen Code-Pfad nach `app/payments`, `app/pay` oder `app/lightning` und
  keine Abhaengigkeit vom Pi. Beide Straenge verbindet nur die oeffentliche Lightning-Zahlung selbst,
  also Rechnung, Link oder QR.
- **I4:** Seeds, Schluessel und Zahlungsberechtigungen erscheinen nie in Logs, Fehlerberichten,
  Analysen oder KI-Prompts. Ein Test belegt das.
- **I5:** KI-Agenten erzeugen nur Zahlungsvorschlaege (Link oder BIP21). Ausfuehren kann nur der
  Nutzer in der App.
- **I6:** Keine eigenen Kauf-, Tausch- oder Stablecoin-Ablaeufe. Erlaubt ist hoechstens ein Link zu
  einem lizenzierten Partner, und nur nach Anwaltsfreigabe.

### 3. Technik

- **Wallet-Kern:** Breez SDK (Spark, MIT-Lizenz). Es gibt Bindings fuer WASM, Kotlin und Swift. Es kann
  Lightning-Adresse, LNURL-pay und On-chain und bietet Recovery per Mnemonic oder Passkey. Fallback nach
  einem NO-GO im Spike: Breez SDK Liquid.
- **Frontend:** der KAI-Stack wird kopiert, nicht gekoppelt: React 18, TypeScript, Vite, Tailwind mit
  KAI-Tokens, eigenes i18n (de/en), Vitest.
  - Zuerst entsteht eine installierbare PWA.
  - Danach dieselbe Codebasis als Capacitor-App (Android zuerst, iOS erst mit Rechtstraeger wegen
    Apple Guideline 3.1.5).
- **Auslieferung:** Cloudflare Workers Static Assets. Ein kleiner Worker stellt `/api` bereit
  (LNURL-Weiterleitung, Kurs-Cache, Web-Push-Relay). Es gibt keine Datenbank mit Personendaten.
- **Domains:**

  | Domain | Verwendung |
  |---|---|
  | `kai-pay.net` | Webseite und Lightning-Adressen `name@kai-pay.net` |
  | `app.kai-pay.net` | Wallet; `/api` laeuft auf demselben Origin |
  | `staging.kai-pay.net` | Staging, geschuetzt mit Access |
  | `admin.kai-pay.net` | 301 auf `kai-trader.org/dashboard/#pay` (kein Tunnel-Umbau) |
  | `.org`, `.online`, `.world` | 301 auf `kai-pay.net`, Pfad bleibt erhalten |

### 4. WoS-Paritaet

- Die Abnahmeliste ist eine Referenzmatrix, gebaut aus Screenshots der WoS-App des Operators
  (EU-Version, mit Version und Betriebssystem).
- Jede Zeile bekommt einen Status: *uebernommen*, *KAI-besser*, *abweichend (Grund)* oder
  *bewusst nicht (Recht)*.
- Nachgebaut werden die Funktionen, nicht Marke oder Aufmachung.

## Abhaengigkeiten und Ausstiegspfad

- **Breez:** Das SDK braucht einen API-Key. Faellt Breez weg, zeigt die App ehrlich „nicht erreichbar“
  an. Der Seed bleibt nutzbar.
- **Spark:** Nutzer vertrauen darauf, dass der Spark-Operator kein Geld zurueckhaelt; ein einseitiger
  Ausstieg on-chain ist theoretisch moeglich. Das kommt in die Nutzerinformation, ebenso der
  Datenschutzhinweis, dass sich die Spark-Adresse aus der Lightning-Adresse ableiten laesst.
- **Nachweis im Spike (E1, S7):** Ein Kleinbetrag wird per Import in eine zweite Spark-Wallet oder per
  Auszahlung on-chain aus der Wallet geholt.

## Offene Rechtsfragen (Anwalt, vor der oeffentlichen Beta)

1. Gilt die Mitsignatur des Spark-Operators als „Kontrolle“ im Sinne von Art. 3 Abs. 1 Nr. 17 MiCAR?
2. Passkey-Backup: Ist das erlaubnisfrei, solange KAI nichts entschluesseln kann?
3. Ein Lightning-Adress-Server unter kai-pay.net vermittelt nur Rechnungen. Ist das eine Transferdienstleistung?
4. Kasse (POS) fuer Haendler ohne Schluessel auf dem Mitarbeitergeraet: Ist das erlaubnisfrei?
5. Duerfen Links zu lizenzierten Kaufpartnern gesetzt werden, und gibt es dafuer Provisionen?
6. Impressum, AGB, Datenschutz, Haftung und geeigneter Rechtstraeger.

## Operator-Schritte (E0)

- Die vier Domains stehen heute auf Namecheap-Nameservern (`dns1/dns2.registrar-servers.com`,
  geprueft am 2026-09-25). Sie als Zonen in Cloudflare anlegen und bei Namecheap die
  Cloudflare-Nameserver eintragen.
- Breez-API-Key beantragen.
- Screenshots aller Bildschirme der WoS-App liefern (Version, Betriebssystem, Region DE).
- Rechtsfragen an einen Anwalt geben; Rechtstraeger klaeren.

## Etappen

Jede Etappe hat eigene Abnahmekriterien. Nur eine Etappe laeuft zur Zeit.

| Etappe | Ergebnis |
|---|---|
| E0 | Dieses ADR, D-285, Referenzmatrix, Operator-Schritte |
| E1 | Technik-Spike (Breez WASM, Recovery, Lightning-Adresse, Ladezeit, Capacitor) mit GO/NO-GO |
| E2 | Repo, CI, Cloudflare, Webseite |
| E3 | Wallet-Kern als geschlossene Beta: Start, Senden, Empfangen, Aktivitaeten, Einstellungen, PWA |
| E4 | Funktionsparitaet laut Matrix: Kontakte, Export, Zahlungslink, Kasse, Push, Hilfe |
| E5 | Store-Apps und KAI-Integration (Zahlungslinks, Vorschlaege von Agenten) |
| E6 | Produktionsfreigabe: Security-Review, Recht, Drills, gestufter Rollout |

**Strang A**, erst nach Release-Lauf und Abnahme laut Betriebsreihenfolge vom 23.09.:

- `pay.kai-trader.org` (LNbits `:5006`, ohne Access, mit Deny-Liste; Befund aus dem Audit vom
  16.09.) auf eine Allowlist nur fuer LNURL-Pfade umstellen.
- Die cloudflared-`config.yml` versionieren.

## Konsequenzen

- KAI bleibt Self-Use. Das Produkt entsteht daneben, ohne den versiegelten Kern oder den laufenden
  Pilot zu beruehren.
- Da KAI keine Kundengelder haelt, braucht es keinen Kundensaldo, keine doppelte Buchfuehrung und
  keinen zweiten Truth-State.
- Gebunden ist man an Breez und Spark. Der Ausstiegspfad ist deshalb Teil der Abnahme, nicht eine
  spaetere Aufgabe.
- Umsatz fuer KAI entsteht nur ueber Stufe B nach D-CORE-006: KAI-Leistungen werden per Zahlungslink
  bezahlt. Fuer Eingaenge von Dritten am eigenen Node braucht es eine eigene Operator-Freigabe.
