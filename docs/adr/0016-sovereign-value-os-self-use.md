# ADR 0016 — Sovereign Value OS (Self-Use)

- **Status:** DRAFT — Entwurf 2026-08-02. Entscheid erst nach **beiden** attestierten Verdikten: C1 `9cab81fae4823482` (04.08.) und Analyst-Probe `f0e1a3a8073fd4c0` (~10.08.). Bis dahin bindet dieses Dokument nichts.
- **Datum:** 2026-08-02
- **Betroffen:** Positionierung, Ausbau-Reihenfolge der Lightning-Schicht, Rolle des Tradings, Abgrenzung Eigennutzung ↔ Produkt
- **Verhältnis zu bestehenden ADRs:** konkretisiert **ADR 0014 Schicht 4** („Intent Layer — nur als Self-Use offen; Dritt-Variante = Tier 2"), erbt **ADR 0012** (Research-/Truth-Plattform als Wegpunkt) und respektiert den prä-registrierten Fork-B-Entscheidungsbaum. **Ersetzt keines von beiden und hebt Fork-B nicht auf.**
- **Referenz:** Operator-Vorschlag „Souveränes Value-OS" (Daily-Wallet + deterministischer Policy-Kern + KAI Truth), geprüft am 2026-08-02 durch vier parallele Verifikations-Läufe gegen Live-Pi, LND-Node, Repo und Ledger

## Kontext

Der Operator hat eine Neupositionierung vorgelegt: Der wertvollste Teil von KAI sei nicht mehr die Signalerzeugung, sondern die Verbindung aus überprüfbarer Wahrheit, deterministischen Sicherheitsregeln, Lightning-Zahlungen, Provenance und einer KI, die planen und erklären kann. Vorgeschlagen wurde ein Daily-Wallet in drei Modi (Personal, Merchant, Agent), ein Liquidity-Autopilot, moderne Routing-Technik und ein Markt für messbar nützliche Information.

**Die Architekturgrenze des Vorschlags wird übernommen und ist der Kern dieses ADRs:**

> Die KI erstellt Absichten, bewertet Alternativen und erklärt. Ein **deterministischer Policy-Kern entscheidet**. Nur LND besitzt und verwendet Schlüssel. Jede Geldbewegung wird anschließend reconciled. Die KI sieht standardmäßig keine Preimages, Macaroons oder vollständigen Routen.

**Das Prüf-Ergebnis widerspricht dem Vorschlag in Umfang und Zeitpunkt, nicht in der Richtung.** Die genannte Grenze existiert heute nur auf dem Papier: Der Reserve-Floor wird gegen einen Cache geprüft, dessen Zeitstempel bei degradierten Polls unbegrenzt einfriert. Das Tages-Cap speist sich aus einem Ledger, dessen Schreiben still fehlschlagen darf. Ein Macaroon trägt sämtliche Rechte, obwohl die Doku Trennung behauptet. Der Zahlungspfad hat einen dokumentierten Präzedenzfall, bei dem eine Zahlung als Fehler protokolliert wurde und real settelte. Der Vorschlag beschreibt damit zu **bauen**, was zuerst zu **reparieren** ist.

Hinzu kommt ein Befund, der die Ausgangslage verändert (siehe „Nachfragelage").

## Entscheidung (vorgeschlagen)

**KAI wird als souveränes Value-OS ausgebaut — ausschließlich in der Self-Use-Form.**

Das bedeutet: Wallet, Liquiditätsverwaltung und agentische Beschaffung werden für **den Operator selbst** gebaut. Es entsteht keine Schnittstelle für Dritte, keine Kundenbeziehung, kein Vertriebsartefakt. Damit fällt das Vorhaben unter die in ADR 0014 bereits versiegelte Öffnung von Schicht 4 und **erfordert keine Aufhebung des Fork-B-Baums**.

### Der Self-Use-Test (bindend, pro Paket nachzuweisen)

Ein Paket ist nur dann Self-Use, wenn **alle drei** Punkte zutreffen:

1. Es existiert ein **benannter, eigener Nutzungsfall** des Operators — kein hypothetischer fremder.
2. Es entsteht **keine Schnittstelle für Dritte** (kein öffentlicher Endpunkt, kein Onboarding, keine Mandantenfähigkeit).
3. Es entsteht **kein Vertriebsartefakt** (keine Landing-Page, kein Preis, keine Ansprache).

Jeder Pull Request der Wellen 0 und 1 referenziert diesen Test. Wer ihn nicht erfüllt, ist Tier 2 und braucht ein eigenes ADR.

### Was damit erlaubt ist

- Härtung des bestehenden Geldpfads (Freshness-Gate, transaktionaler Intent-Ledger, Reconciliation, Idempotenz, Macaroon-Trennung, Redaktion sensibler Felder)
- Sichtbarkeit über eigenes Kapital, einschließlich pending Force-Closes
- Betriebssicherheit des eigenen Nodes (SCB-Monitor, Watchtower, Version)
- Eine Wallet-Oberfläche für den Operator gegen die **bestehende** API — kein zweiter Enforcement-Punkt
- Liquidität für den **eigenen** Node
- Agentische Beschaffung, bei der **KAI selbst** kauft

### Was gesperrt bleibt

Merchant-/POS-Betrieb, ein „Liquidity Intelligence Service", Intent-Ausführung für Dritte, Marktplatz, Escrow mit Fremdgeldern, RWA. Diese bleiben **Tier-2-STOP** gemäß ADR 0014 Abschnitt 4 — kartiert, bewusst nicht gebaut, auch nicht „schon mal auf Testnet".

## Nachfragelage (korrigierte Tatsachengrundlage)

Der Vorschlag bezeichnete einen 25.000-sat-Eingang vom 04.07. als „das stärkere reale Nachfragesignal". Die Forensik vom 02.08. widerlegt das:

- Zwei Tage vor dem Eingang verließ **exakt derselbe Betrag** den Node — am 02.07. um 05:46:20 UTC, an eine Rechnung mit der Beschreibung **„Test"**, adressiert an ein Wallet hinter dem **eigenen** Kanal-Peer, das in keinem anderen Artefakt vorkommt. Genau dieser Spend ist der bekannte „als Fehler protokolliert, real settled"-Präzedenzfall.
- Die einzigen beiden Oracle-Zahlungen (je 10 sat, 02.07.) settelten, **während `/oracle/*` noch CF-Access-privat war**. Die Öffnung war ausdrücklich Voraussetzung für den Listing-Post am 04.07. Extern konnte sie niemand erreichen — es waren Selbsttests.
- Derselbe Requester-Fingerprint erzeugt am 04.07. auch die ersten beiden Anfragen nach dem Listing.

> **Feststellung: KAIs verifizierbare externe Einnahmen betragen über die gesamte Laufzeit 0 sat.**

LNURL-pay überträgt keine Absenderinformation; die Rückführung ist deshalb nicht hart beweisbar. Die Gegenhypothese müsste allerdings annehmen, dass eine fremde Person spontan auf den Satoshi genau die Summe zahlt, die gerade hinausgegangen war.

**Konsequenz:** Es existiert kein externes Nachfragesignal — auch kein schwaches. Damit entfällt das einzige Argument, das für einen früheren Produktausbau hätte sprechen können, und die Self-Use-Beschränkung dieses ADRs ist nicht Vorsicht, sondern die einzige durch Evidenz gedeckte Option.

## Bindende Regel: Dogfood ist keine Nachfrage

**Metriken aus der Eigennutzung zählen NIE als Demand-Evidenz.** Sie messen ausschließlich Betriebsqualität.

Dass der Operator seine eigene Wallet täglich benutzt, beweist, dass die Wallet funktioniert — nicht, dass jemand dafür zahlen würde. Diese Verwechslung ist der wahrscheinlichste Weg, auf dem dieses ADR später missbraucht würde: erst Dogfood-Erfolg, dann „also gibt es Bedarf", dann Merchant-Ausbau. Die Trennung wird deshalb hier vorab festgeschrieben, nicht im Nachhinein diskutiert.

Die Re-Open-Klausel des Fork-B-Baums gilt unverändert und wörtlich:

> Re-Open-Kriterium (einziges): ein EXTERN initiiertes, unaufgefordertes Signal (Zahlungs-/Pilotanfrage Dritter) → löst eine NEUE Prä-Reg aus, nie direkten Bau.

## Rolle des Tradings

Trading bleibt technisch **unverändert** in Betrieb: Die Paper-Pipeline läuft weiter, weil zwei versiegelte Prä-Registrierungen auf `artifacts/paper_execution_audit.jsonl` festgelegt sind — H1 `fd6f5f7842f49244` (n≥200, Reife ~September) und H2 `0c7ead764621dd17` (n≥50). Ein Abschalten würde beide beschädigen.

Was sich ändert, ist die **Positionierung**: Trading ist ein Falsifikations-Labor, kein Produktkern. Das ist keine Neuerfindung, sondern die Anerkennung eines bereits attestierten Befunds — `canonical-edge` steht auf NO_GO (n=68), und die Paper-Ergebnisse der laufenden Epoche sind negativ.

### Kanonische Zahlen (die bisher kursierenden sind falsch)

Der Vorschlag nennt −4.252 USD, EV −8,13 und 37,5 % Trefferquote. Keine dieser Zahlen ist aus den Rohdaten reproduzierbar. Verbindlich gilt, epochenrein seit `paper_v2_attested` (Reset 2026-07-12):

| Kennzahl | Wert |
|---|---|
| Realisierter PnL | **−675,36 USD** (n=134, Summe `trade_pnl_usd` über `position_closed` + `position_partial_closed`) |
| Erwartungswert je Trade | **−5,04 USD** |
| Trefferquote | **32,8 %** (44/134) |
| Exit-Mix | 91 stop · 35 take · 8 tp_tier |

Kreuzprobe: Der letzte kumulative `realized_pnl_usd` der Epoche beträgt −675,3611 und bestätigt die Summe. All-Time-Zahlen enthalten die verworfene Legacy-Epoche und sind **nicht entscheidungstauglich**.

Ebenfalls zu korrigieren: „74 % active precision" ist die ungefilterte `alert_hit_rate` (111/150); die tatsächliche `active_precision` beträgt 73,29 % (107/146), und der zugehörige Report markiert sich selbst als `insufficient_data` bei einem Gate-Minimum von 200. Die episoden-deduplizierte Precision über 2.417 aufgelöste Zeilen beträgt 56,7 %. Beide Zahlen sind kein Widerspruch, sondern verschiedene Populationen aus verschiedenen Pipelines.

Leere Risikometriken (VaR, CVaR, Sharpe, Sortino, Drawdown) sind **beabsichtigt** und dokumentiert: `degraded` mit `value=None` ist der ehrliche Zustand, solange die Berechnungsbindung fehlt — kein Defekt.

## Ausbau-Reihenfolge

**Welle 0 — Geldpfad härten.** Q4-kompatibel, weil Betriebs-Härtung ausdrücklich erlaubt bleibt. Beginnt nach dem attestierten C1-Verdikt, da der Rechnungspfad Teil des laufenden Messobjekts ist. Reihenfolge: Freshness-Gate → transaktionaler Intent-Ledger → Reconciliation mit Terminal-State → Tages-Cap; parallel Schließung des `auto_execute`-Pfads, Redaktion im Writer, Macaroon-Trennung, Wiederbelebung des SCB-Monitors samt Sichtbarkeit pending Kapitals.

**Welle 1 — Wallet für den Operator.** Startet **nicht** vor Annahme dieses ADRs. Node-Version, Bergung des Limbo-Kapitals, Watchtower, zweiter Kanal, Wallet-Oberfläche. Jedes Paket mit eigenem Betriebs-Gate; kein Paket beginnt vor dem Gate des vorigen.

**Welle 2 — Agentische Beschaffung.** Nur Design und Prä-Registrierung. Es existiert heute kein externer L402-Anbieter-Markt, bei dem KAI einkaufen könnte; ein Kauf beim eigenen Oracle wäre ein Zirkelschluss. Vor jedem Bau: Nachweis von mindestens drei unabhängigen, real bezahlbaren externen Quellen und ein **vor** der ersten Zahlung versiegeltes ROI-Maß.

**Nicht eingeplant:** Loop, LSPS2, AMP und PTLC haben kein Substrat — der Node hat einen Kanal und über seine gesamte Lebensdauer null Weiterleitungen; PTLCs sind in keiner Implementierung ausgeliefert. BOLT12 erst, wenn LND es nativ trägt.

## Invarianten (aus ADR 0014 übernommen, hier unverändert gültig)

1. Append-only; Korrekturen als neue referenzierende Records, nie Revoke/Delete/Admin-Edit.
2. Kein Fund-Holding außerhalb Self-Custody und LN-Policy.
3. Souveränität zuerst: Bitcoin, Lightning und OpenTimestamps vor fremden Chains, RPCs oder Sequencern.
4. Fail-closed bei Unsicherheit; Demand-Gate vor Supply-Bau; Prä-Registrierung vor Messung.
5. Selbst gehostete attestierte Artefakte vor externem Storage.

## Konsequenzen

**Positiv.** Der Geldpfad wird belastbar, bevor ihm mehr anvertraut wird. Die Architekturgrenze des Vorschlags wird real statt behauptet. Eigenes Kapital wird sichtbar, einschließlich der bisher unsichtbaren 25.815 sat in einem überfälligen Force-Close. Fork-B bleibt intakt, es entsteht kein Doktrin-Präzedenzfall für nachträgliches Umdeuten versiegelter Konsequenzen.

**Negativ.** Es entsteht in absehbarer Zeit kein Umsatz — was angesichts der korrigierten Nachfragelage ohnehin nicht anders zu erwarten wäre. Der Aufwand fließt in Reparatur statt in neue Fähigkeiten. Die Wallet bleibt in ihrer Empfangsfähigkeit begrenzt, solange der Node Tor-only mit einem Kanal betrieben wird.

**Risiko und Gegenmaßnahme.** Das größte Risiko ist Doktrin-Erosion: Härtung und Eigennutzung als Deckmantel für schrittweisen Produktbau. Dagegen stehen der Self-Use-Test pro Paket, die Regel „Dogfood ist keine Nachfrage" und die unveränderte Re-Open-Klausel.

## Offene Frage, die dieses ADR bei Annahme beantworten muss

Der **SSG-Pilot** (Signal-Abo-Audit-Report, 79 EUR, Operator-Entscheid vom 30.07.) ist selbst ein Monetarisierungsvorhaben und wurde **vor** dem Q4-Verdikt beschlossen. Genießt er Bestandsschutz, oder fällt er unter das Q4-Verbot? Beide Auslegungen sind vertretbar; die Entscheidung gehört dem Operator und wird bei Annahme dieses ADRs hier eingetragen.

## Status-Übergang

Dieses Dokument bleibt **DRAFT**, bis beide Verdikte attestiert sind. Erst dann entscheidet der Operator über ACCEPTED oder REJECTED. Ein Übergang nach ACCEPTED ohne vorliegende Verdikte wäre genau die Vorwegnahme, die der Fork-B-Baum ausschließen sollte.
