# ADR 0014 — KAI Protocol: Zielbild & Schichtenkarte (Verifiable AI Finance)

- **Status:** ACCEPTED — Operator-Entscheid 2026-07-06 (Plan-Approval mit drei expliziten Wahlen: Analyst-Probe jetzt parallel · EVM strikt gated · Arbeitstitel „KAI Protocol")
- **Datum:** 2026-07-06
- **Betroffen:** Positionierung, Ausbau-Reihenfolge, Onchain-Strategie, Grenzen für Schichten 4–6; ergänzt ADR 0012 (Wegpunkt Truth-Plattform) und ADR 0013 (Zugangs-/Realisierungs-Achse) — ersetzt keines von beiden
- **Referenz-Plan:** Operator-Vorschlag „KAI Protocol / KAI CleverContracts" (7 Schichten, 6 Phasen), geprüft via Explore + Architektur-Red-Team am 2026-07-06

## Kontext

Der Operator hat ein 7-Schichten-Zielbild vorgelegt: KAI als onchain-verifizierbares Analyse-, Oracle- und Settlement-Protokoll (Verdict-Layer → Oracle-Registry → L402-Zugang → P2P-Intents → Escrow-„CleverContracts" → Compliance → Treasury), mit expliziten Nicht-Zielen (keine Börse, keine unlizenzierte Tokenisierung, kein Fremdkapital, keine Anlageberatung).

**Prüf-Ergebnis:** Die Richtung deckt sich mit dem vollzogenen Kurs — die Schichten 1–3 und 6–7 existieren bereits und laufen live, teils in besserer Form als vorgeschlagen (Bitcoin-OTS statt EVM). Der Wert des Vorschlags ist die Positionierungs-Klammer und die Ziel-Landkarte, nicht ein Bauplan. Der Vorschlag *as written* wurde vom Red-Team verworfen (Supply-vor-Demand, EVM-Regression gegenüber lebendem OTS-Anker, Lizenz-Kollisionen in Phasen 4–6, `revokeVerdict`+Admin-Key würde Non-Repudiation zerstören).

**Bindende Randbedingungen:**
- Demand-Verdikt 2026-07-04: Niemand zahlt für das kryptographische Primitiv (OTS/Hash-Chains/Onchain-Fakten = Gratis-Commodities). Gezahlt wird nur für regulatorische Registrierung, reputations-haftende Signatur oder vertikalen Workflow mit konkretem Käufer. Einziger Analog-Markt: „verifizierter prä-registrierter Track-Record" (Analysten).
- C1-Demand-Probe läuft live (Listing 2026-07-04, 30-Tage-Fenster, prä-reg. `9cab81fae4823482`) — ein versiegeltes Experiment, das nicht durch neue Features/Listings kontaminiert wird.
- Solo-Operator, DE/EU, Reserve-Floor 1.840.000 sat; kein EVM-/Solidity-Code im Repo.

## Entscheidung

### 1. Positionierung & Sprachregel

**„KAI Protocol — Verifiable AI Finance on Crypto Rails"** ist das interne Zielbild-Dach; „CleverContracts" bezeichnet ausschließlich die (gegatete) Settlement-Schicht 5. Definition: KAI ist ein AI-gestütztes, kryptographisch verifizierbares Finanz-Intelligence-System — Analyse → versiegeltes Verdict → Zahlung/Zugang → Audit-Trail; optionale Ausführungs-/Settlement-Anbindung nur innerhalb der Gates dieser ADR. KAI ist NICHT: Börse, Broker, Verwahrer, Vermögensverwalter, Anlageberater für Dritte.

**Sprachregel (löst Red-Team B-002):** Extern/öffentlich bleibt die etablierte ehrliche Sprache („Bitcoin-anchored, independently verifiable AI research node / auditierbare Truth-Plattform"), bis ein Substanz-Kriterium erfüllt ist: **erster zahlender Fremd-Konsument ODER zweite unabhängige externe Verifikation.** „Protocol" ohne Multi-Party-Substanz lädt die Trustless-Prüfung ein, die ein Solo-Server nicht besteht, und streift regulierte-Tätigkeit-Suggestion.

### 2. Schichtenkarte (kanonisch)

| # | Schicht | Ist-Zustand | Regel |
|---|---|---|---|
| 1 | Verdict Layer | ✅ LIVE: `app/truth/attestation.py` (SHA-256-kanonisch), `app/truth/ledger.py` (hash-verkettet, tamper-evident), Prä-Reg-Ledger + Family-Stop-Rules, attestierte Verdikt-Reports, Input-Pinning | Nichts bauen; Schema-Erweiterung nur demand-pulled |
| 2 | Oracle Registry | ✅ LIVE als **Bitcoin-OTS**: `kai-truth-anchor.timer` (täglich, selbstheilend), `verdict-anchor`, Drittverifikation bewiesen (B5/B5c) | EVM strikt gated (Gate G-EVM, s. u.) |
| 3 | Access Layer | ✅ LIVE: L402 auf `/oracle/*` (10 sat), LN-Node, Lightning-Address `kai@pay.kai-trader.org`, Earnings-/Demand-Timer | Nichts bauen; C1-Fenster misst die Nachfrage |
| 4 | Intent Layer | ❌ | Nur als **Self-Use** offen (KAI gated eigene Forschung/Execution; Muster `edge_validation_gate`). Dritt-Variante = Tier 2 |
| 5 | CleverContracts / Marketplace | ❌ | Escrow/Fremdgelder = Tier 2. Marketplace-Kern („verifizierter Track-Record") wird per Analyst-Outreach-Probe demand-getestet, nicht gebaut (Gate G-MKT) |
| 6 | Compliance / Identity | ✅ teilweise: `app/compliance/provenance.py` (SoF/TFR-Export), `app/governance/third_party_gate.py` (fail-closed) | KYC/Sanctions/Travel-Rule = STOP-Schild, kein baubares Modul ohne Dritt-Dienst-Gate |
| 7 | Treasury / Governance | ✅ teilweise: `app/capital/` (4 Buckets, inert), LN-Policy (Caps/HOTP/Reserve-Floor) | 4-Bucket-Politik bleibt; Governance-Split erst bei realem Umsatz > Reserve-Floor |

### 3. Demand-Gates (prä-registriert, dieselbe Falsifikations-Disziplin wie Signal-Hypothesen)

- **G-EVM:** EVM-Verdict-Registry erst, wenn ein **benannter** Integrator KAI-Verdicts *im Smart Contract* konsumieren will (der einzige legitime EVM-Steelman: OTS-Proofs sind nicht contract-lesbar). Falls je gebaut: append-only, **kein `revokeVerdict`, kein Admin-Key** (Korrektur nur via neuen referenzierenden Record), kein Fund-Holding, SATOSHI-Review Pflicht.
- **G-MKT:** Marketplace-/Badge-Software erst nach PASS der Analyst-Outreach-Probe (prä-registriert vor erstem Kontakt; PASS = ≥2 von ~10 kontaktierten Analysten nehmen das kostenlose Badge an UND zeigen es ungefragt ihrem Publikum, 30-Tage-Fenster). FAIL wird aktenkundig → Fork B (Eigen-Instrument).
- **G-3P:** Jede Dritt-Dienst-Aktivierung ausschließlich via `require_third_party_authorization()` (Lizenz-Ref Pflicht) + fachanwaltliche Prüfung vorab — unverändert ADR 0013.

### 4. Tier-2-Grenzkarte (STOP-Schild — kartiert, bewusst NICHT gebaut)

| Vorhaben (aus dem Vorschlag) | Regulatorischer Trigger |
|---|---|
| Risk-Gated Execution für Dritte / Intent-Vermittlung | Anlagevermittlung/Portfolioverwaltung (MiFID II/WpIG/KWG); MiCAR-CASP |
| P2P-Marktplatz mit Zahlung/Reputation | MiCA-CASP-Betrieb; widerspricht Nicht-Ziel „keine Börse" |
| Escrow-Contracts mit Nutzergeldern | ZAG-Zahlungsdienst / E-Geld / Kryptoverwahrgeschäft (§ 1 KWG) |
| RWA/tokenisierte Wertpapiere (ERC-3643) | Wertpapier-/Prospekt-Regime, MiCAR ART/EMT; nur mit lizenziertem Partner |
| NFT-/Onchain-Receipts | überflüssig (L402-Preimage IST der Kaufnachweis) + Token-Klassifizierungsrisiko |

Kein „schon mal bauen" auf Testnets für Tier-2-Vorhaben — das erzeugt Sunk-Cost-Druck Richtung Grenzüberschreitung.

### 5. Design-Invarianten für jede künftig gebaute Erweiterung

1. Append-only; Korrekturen als neue referenzierende Records, nie Revoke/Delete/Admin-Edit.
2. Kein Fund-Holding außerhalb Self-Custody + bestehender LN-Policy (Caps, HOTP, Reserve-Floor).
3. Souveränität zuerst: Bitcoin/LN/OTS vor fremden Chains/RPCs/Sequencern.
4. Fail-closed bei Unsicherheit; Demand-Gate vor Supply-Bau; Prä-Registrierung vor Messung.
5. Selbst gehostete attestierte Artefakte vor externem Storage (IPFS/Arweave nur auf konkrete Konsumenten-Anforderung).

## Konsequenzen

- Nächste reale Schritte sind **Demand-seitig**, nicht Supply-seitig: (a) C1-Fenster ungestört auswerten (~2026-08-03, mechanisch via `prereg-check`), (b) Analyst-Outreach-Probe (prä-registriert, Versand = Operator-Handlung).
- Kein Solidity/EVM, kein Testnet, kein IPFS, kein NFT-Receipt, kein Escrow, kein Marktplatz-Code, kein KYC-Modul, kein Treasury-Umbau, solange die Gates nicht grün sind.
- Der 50/20/15/10/5-Treasury-Split des Vorschlags wird verworfen (Scheinpräzision unterhalb des Reserve-Floors); die bestehende 4-Bucket-Politik gilt.
- Übernommen aus dem Vorschlag: Verdict-als-atomares-Produkt, Analyse↔Execution-Trennung, Intent-Framing als Self-Use, Begriffsklärung, verschärfte Nicht-Ziele, Schichtenmodell als Landkarte.

## Alternativen erwogen

- **Vorschlag as written bauen (EVM-Registry als Phase 1, Phasen 4–6 als Roadmap) — verworfen:** dupliziert lebende OTS-Infrastruktur mit schlechteren Eigenschaften (Gas pro Eintrag; Receipt-Kosten 100–1000× des 10-sat-Zahlwerts; Deployer-Key als SPOF; Chain-Lock-in bricht Souveränitäts-Doktrin); Phasen 4–6 sind Lizenz-Wände, keine Fleißaufgaben; präjudiziert die laufende C1-Probe.
- **Externes Rebranding auf „Protocol" sofort — verworfen:** überzeichnet Substanz (Solo-Server), lädt Trustless- und Regulatorik-Prüfung ein; stattdessen Sprachregel mit Substanz-Kriterium.
- **Vorschlag komplett ablehnen — verworfen:** Positionierungs-Klammer, Nicht-Ziele und Landkarte sind real wertvoll; ohne Verankerung droht dieselbe Idee wiederholt als Neubau-Plan aufzutauchen.

## Rest-Unsicherheit (markiert)

- Ob je ein DeFi-Integrator Verdicts contract-lesbar braucht (G-EVM) — offen; nur der Operator kann einen benannten Kandidaten beurteilen.
- Narrativ-/Fundraising-Wert von „Protocol" gegenüber Substanz-Mismatch — Marketing-Abwägung, hier bewusst über die Sprachregel konservativ gelöst.
- Diese ADR ist **kein Rechts-/Steuerrat**; jede Tier-2-Annäherung braucht Fachanwalt/BaFin-Voranfrage (unverändert ADR 0013).
