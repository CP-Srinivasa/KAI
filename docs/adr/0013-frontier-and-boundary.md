# ADR 0013 — Frontier & Boundary: souveräner Zugang statt Umgehung

- **Status:** ACCEPTED — Operator-Entscheid 2026-07-01
- **Datum:** 2026-07-01
- **Betroffen:** Plattform-/Zugangs-/Wallet-/Kapital-Strategie; erweitert ADR 0012 (Research-/Truth-Plattform) um die Zugangs- & Realisierungs-Achse
- **Referenz-Plan:** Full-Spectrum-Strategieplan (Operator-Freigabe 2026-07-01)

## Kontext

KAI soll kontrolliert zu souveräner, auditierbarer Krypto-Finanz-Infrastruktur wachsen — Anbieter-Wahl, Wallet-/Reserve-Strategie, Hochrisiko-Forschung. Leitanspruch des Operators: **an die echte Kante gehen**, den vollen Möglichkeitsraum denken, dann *wahrhaftig realisieren, was tatsächlich geht* — kein compliance-eingesperrtes Spielzeug.

**Historisches Argument (Operator, faktisch korrekt):** Technologie führte, Regulierung folgte — Bitcoin 2009, frühe Börsen, das Ökosystem entstanden Jahre vor FinCEN (2013), BitLicense (2015), 5AMLD (2020), MiCA (2023–2025).

**Präzisierung, die diese ADR trägt:** Der „build-first, Regeln-kommen-später"-Vorteil gilt im *noch ungeregelten* Raum — **nicht rückwirkend gegen bereits geltende Regeln**. Eine Umgehung der KYC-/Geoblocking-Kontrollen nicht-autorisierter zentraler Börsen (Binance/Bitfinex/BitMEX/Poloniex) 2026 ist nicht „der Regulierung voraus": MiCA/GwG/TFR sind seit 2024/25 **in Kraft**. Das ist kein Frontier — und strategisch rückwärtsgewandt, weil die zentrale Börse als Mittelsmann exakt das *alte* Modell ist, das Bitcoin überflüssig machen wollte.

**Zwei Recherche-Befunde (2026-07-01 verifiziert):**
- DE-MiCA-Grandfathering ausgelaufen (**31.12.2025, § 50 KMAG**; Primärtext maßgeblich, Sekundärquellen nennen fälschlich die EU-Höchstfrist 01.07.2026). → Fiat-On/Off-Ramp nur über MiCAR-autorisierte CEX; permissionless/Self-Custody davon unberührt.
- MiCAR-konforme Stablecoins = **USDC/EURC** (USDT auf EU-Venues weitgehend delistet). TFR (VO 2023/1113) ohne Bagatellgrenze.

## Entscheidung

KAI wächst entlang einer **Tier-Karte des Möglichkeitsraums**. Der Wert entsteht an der *legalen* Frontier, nicht an der Umgehung.

### Tier 0 — Basis (sicher, privat, sofort)
Autorisierte CEX **nur als Fiat-Brücke** (Kraken/Coinbase/Bitpanda, MiCAR), Daten-Redundanz, Cold-Storage-Multisig. Compliance-first, schlank.

### Tier 1 — Frontier (neu, legal, „das Besondere")
1. **Permissionless / Self-Custody — souveräner Zugang.** DEX/On-Chain, self-hosted Node (RaspiBlitz/lnd), Lightning. Echter Zugang für Unterversorgte — nicht *an* einer Börse vorbei, sondern indem die Börse als Nadelöhr entfällt. Legal, weil permissionless kein KYC-Gate hat.
2. **Auditierbare Wahrheits-/Provenienz-Schicht** (ADR 0012 realisiert): verifizierbarer, tamper-evidenter Signal-/Falsifikations-Layer.
3. **Neue On-Chain-Primitive** (weitgehend ungeregelt, weil neu): zk-attestierte Track-Records (Performance beweisen ohne Positionen offenzulegen), On-Chain-Reputation, Proof-of-Provenance.
4. **Compliance-as-Superpower / MiCA-native RegTech:** Auditierbarkeit als Produkt & Moat — macht KAI später schneller zulassungsfähig als scheiternde Incumbents.
5. **Lizenz-Pfad (CASP/BaFin):** legale Maximalstufe, um Dritten Dienste anzubieten.

### Tier 2 — Kartierte harte Grenze (bewusst NICHT gebaut)
CEX-KYC/Geoblocking-Umgehung nicht-autorisierter Börsen + unlizenzierter Dritt-Dienst. Ehrliches Explorations-Ergebnis: (a) „Innovation vor Regulierung" greift faktisch nicht — Regel gilt bereits; (b) strategisch fragil (Binance verlässt die EU ohnehin); (c) rückwärtsgewandt. → als Grenze markiert, damit der Raum *davor* voll ausgeschöpft wird.

### Die eine Linie (durch alle Tiers)
- **Self-use / Self-Custody / Protokoll-Nutzung:** Raum weit offen — kein Intermediärs-Gate.
- **Alles *für Dritte* oder *Emission an die Öffentlichkeit*** — egal welche Schiene, auch DeFi — läuft durch das **Lizenz-/Offering-Gate** (CASP/MiCAR Titel V bzw. Titel-II-White-Paper/MiFID). Operationalisiert als `THIRD_PARTY_SERVICE_ENABLED` (default False, Guardrail „nur mit dokumentierter Zulassung").
- **Bereits geltendes Recht wird nicht umgangen** — nicht aus Vorsicht, sondern weil es das schwächere Ziel ist.
- **Fail-closed:** bei Unsicherheit inert/shadow bis Gate grün + Operator-Bestätigung.

## Konsequenzen

- **Anbieter:** permissionless/On-Chain als First-Class-Access; CEX (Kraken/Coinbase/Bitpanda) nur Fiat-Ramp; Binance/Bitfinex/BitMEX/Poloniex für Live/DE nicht bauen. Reserve-Stablecoin USDC/EURC, nicht USDT.
- **Code-Gates bleiben unverändert scharf:** `edge_validation_gate` (DSR/MinTRL) nicht im Execution-Pfad; jede irreversible Kapital-Aktion über `verify_capital_confirm` (HOTP+Plan-Hash+Idempotency).
- **Live-Vorbereitung konkret, aber ehrlich:** voller Live-Apparat wird gebaut; erstes reales Kapital = hart gedeckelte **Lern-Tranche** (Ops-/Parität-/Gebühren-Wahrheit), isoliert; edge-abhängige Skalierung bleibt auf `edge_validation_gate=grün` gegated — bewusste Operator-Entscheidung mit Pre-Mortem, nicht still erzwungen.
- **Regulatorik schlank** (Operator-Wunsch): nötige Tiefe in `docs/strategy/`, kein Deep-Dive; Auditierbarkeit als Moat statt Käfig.
- **Steuer/Nachweis:** ein revisionssicheres Ledger bedient SoF/AML (GwG), TFR-Ownership-Proof und Steuer (§23/§20 EStG, FIFO) zugleich — Nachweis-Hygiene, nicht Umgehung.

## Alternativen erwogen

- **CEX-Umgehung für Zugang (verworfen):** illegal (in-force Recht), fragil, rückwärtsgewandt; widerspricht ADR 0012 (auditierbare Truth-Plattform).
- **Rein privat/compliance-eng, keine Frontier (verworfen):** erfüllt den „etwas Neues erschaffen"-Anspruch nicht; verschenkt den permissionless-Vorsprung.
- **Sofort Dritt-Dienst ohne Lizenz (verworfen):** unerlaubtes CASP/Finanzdienstleistungs-Terrain (strafrechtlich); der Lizenz-Pfad ist der legale Weg zur Skalierung.

## Rest-Unsicherheit (markiert)

- Einzel-Lizenzstatus (Kraken/Coinbase/OKX): ESMA-CASP-Register vor jedem Go/No-Go namentlich prüfen.
- Permissionless/DeFi-Feinabgrenzung: Self-Custody/Protokoll-Nutzung außerhalb CASP; Dritt-Dienst/Emission darauf triggert Gate — Einzelfall.
- AMLR/AMLA (~2027), DAC8 (ab 2026+) können nachjustieren.
- Diese ADR ist **kein Rechts-/Steuerrat**; verbindliche Einzelfall-Klärung über Fachanwalt/BaFin-Voranfrage.
