# KAI_IDENTITY.md — Single Source of Truth für die Projektidentität

**Stand:** 2026-06-02 · **Status:** Kanonisch · **Gilt für:** alle Menschen, Agenten und KI-Systeme, die an KAI arbeiten.

Dieses Dokument ist die **verbindliche Identitäts- und Zielbild-Definition** von KAI. Bei Widerspruch zwischen diesem Dokument und älteren/archivierten Beschreibungen gilt dieses Dokument. `README.md` und `ARCHITECTURE.md` müssen mit dieser Definition konsistent bleiben.

---

## Leitdefinition

> **KAI ist ein modulares, sicheres und agentisches KI-System für globale Informations-, Markt-, Risiko- und Finanzanalyse.** Das System sammelt und bewertet Signale aus Social Media, RSS, Internet, öffentlich erreichbaren Tor-Quellen, frei verfügbaren Dokumenten, Marktplattformen, Krypto-Datenquellen und späteren Finanzinfrastrukturen wie Lightning, DeFi und KYT. KAI trennt Datenaufnahme, Analyse, Risiko, Entscheidung, Audit, Sicherheit, Benutzerinteraktion und optionale Ausführung klar voneinander. Ziel ist **kein Blackbox-Trading-Bot**, sondern ein nachvollziehbares, kontrollierbares und erweiterbares Analyse- und Entscheidungsfundament mit Watchdog-Kontrolle, SENTR-Sicherheit, Dashboard, App und späterer Multichannel-Fähigkeit.

---

## Namens- und Identitätskonvention

| Begriff | Rolle |
|---|---|
| **KAI** | Primäre Systemidentität und Name des Gesamtprojekts. Zentrale agentische KI-Systemfigur. |
| **Robotron** | Interner Codename / Arbeitsname. **Nicht** die fachliche Produktidentität, niemals wichtiger als KAI dargestellt. |
| `ai_analyst_trading_bot` | Legacy-/Repository-/Paket-/Pfadname. Technisch in Verwendung, aber **nicht** die finale fachliche Produktidentität. Wird bewusst nicht umbenannt (Rename-Risiko). |

**KAI darf nicht dargestellt werden als:** einfacher Trading-Bot · garantierter Gewinnbringer · Blackbox · reine optische Persona · reines Dashboard · reines Telegram-/Crypto-Tool · vollständig autonomes Finanzsystem ohne Kontrolle.

---

## Persona ≠ Architektur (wichtige Trennung)

Die Unterlagen in `KAI-Persona/` und `docs/kai_persona/` (Prompt-Bibel, „Persona Non Grada", Asset-/Creative-/UI-Packs) definieren ausschließlich **bildliche Darstellung, Persönlichkeit, visuelle Identität, Tonalität und Avatar-/Medien-Persona**.

Sie sind **NICHT** die fachliche Quelle für die technische System-, Analyse-, Risiko- oder Trading-Architektur. Look & Feel und Sprachstil dürfen nicht mit der Systemarchitektur verwechselt werden. Technische Wahrheit steht in diesem Dokument, in `ARCHITECTURE.md`, `CLAUDE.md`, `docs/contracts.md` und den ADRs.

---

## Reifegrad-Konvention

Jede Architekturschicht wird mit ihrem Reifegrad markiert:

- **LIVE** — heute implementiert und im Betrieb (Paper-First, Live-Execution disabled).
- **VORBEREITET** — Datenmodell/Schnittstelle existiert, bewusst deaktiviert/gegated.
- **ZIELBILD** — geplante Zukunftsschicht aus dem World-of-KAI-Zielbild, noch nicht gebaut; jede finanzwirksame Schicht nur mit Governance, Compliance, Audit und Human-in-the-loop.

---

## Architektur-Schichtenmodell

### A. Input- / Signalquellen
- **LIVE:** RSS-Feeds, TradingView, Telegram (MTProto), NewsData, X/Twitter, CoinGecko, YouTube-Transcripts.
- **VORBEREITET:** weitere Börsen-/Marktdaten-Provider (Provider-Symmetrie via `app/market_data/`).
- **ZIELBILD:** Social-Media-Breite (Reddit, Discord, Foren), globaler Web-Crawl, öffentlich erreichbare Tor-Quellen im **rechtlich sauberen Beobachtungsrahmen** (keine Interaktion mit illegalen Märkten/Angeboten), frei verfügbare Dokumente, CoinMarketCap, On-Chain-/DEX-/DeFi-Signale, Whale-/Wallet-/KYT-relevante Bewegungen (soweit rechtlich und technisch sauber).

### B. Ingestion- und Normalisierungsschicht
- **LIVE:** Quell-Adapter je Datenquelle, Rate-Limits, Quellen-Metadaten, Zeitstempel, Deduplikation, Quellen-Taxonomie + Lifecycle-Status (`active/planned/disabled/requires_api/unresolved`), Provenance/Herkunftsnachweis, strukturierte Events. Trust-Boundary über `monitor/*` (File-System-ACL).

### C. Analyse- und Intelligence-Schicht
- **LIVE:** Keyword-/Rule-Engine, LLM-Analyse-Pipeline, Priority-Scoring, Sentiment-Klassifikation, Signalberechnung (`app/signals/`), Multi-Horizont-Scores, Confidence-Scores, Unsicherheits-/Datenqualitätsbewertung, Markt-Regime-Klassifikator (`app/regime/`, Observer), Narrative-/Hype-Analyse-Bausteine.
- **VORBEREITET/lernend:** adaptive Lernschicht (`app/learning/`), Bayes-Confidence (SHADOW_ONLY).

### D. Entscheidungs- und Audit-Schicht
- **LIVE:** Entscheidungsjournal (`app/decisions/`), AuditStream (append-only JSONL, `correlation_id`-Kette), tamper-evidente Audit-Primitiven (`app/audit/`), Begründungsketten, Human-in-the-loop-Approval, Paper-Trading-Protokolle. **Recording ≠ Executing.** Keine Blackbox-Entscheidungen.

### E. Risiko-, Sicherheits- und Compliance-Schicht
- **LIVE:** RiskEngine + Gate-Chain (`app/risk/`, non-bypassable), Kill-Switch (manueller Reset), Fail-closed-Prinzip, Security-Layer (`app/security/`: Idempotency, Rate-Limit, Brute-Force-Guard, Auth-Guards), **SENTR** (Security & Inspection, Agent) und **Watchdog** (Health & Drift, **Read-Only-Prüfer**), Secret-Management außerhalb Repo.
- **VORBEREITET:** server-seitige SL (OCO), HOTP-Verifier, Exchange-Permission-Verifier (Phase-0-Gates).
- **ZIELBILD:** KYT / Know Your Transaction, Wallet-/Node-Sicherheit, YubiKey-/Passkey-/FIDO2-orientierte Unternehmenssicherheit, Rollen-/Rechtekonzepte, Compliance- und Datenschutz-Prüfungen, Notfallmechanismen.

### F. Finanz- und Zahlungsinfrastruktur
- **ZIELBILD (vollständig, gegatet):** Lightning Network (Payment Channels, LNURL/Invoices/QR, Liquiditäts-/Routing-/Gebühren-/Node-/Backup-/On-chain-Fallback-Themen), Spendenempfang, Investment-/Ein-/Auszahlungsflüsse bei unterstützten Börsen, On-chain-Fallback, Buchhaltung, Gebühren-/Liquiditätsmanagement, DeFi-Integration (Staking/Lending/DEX/Smart Contracts) **nur** mit Sicherheits-, Audit-, Bridge-, Oracle-, Gas-, Wallet- und Risikoprüfung. Heute nicht implementiert.

### G. Interface- und Multichannel-Schicht
- **LIVE:** Desktop-Dashboard (React-SPA, mobile-friendly), Cloudflare-Tunnel-Remote-Zugang, Telegram-Operator-Bot (Read/Audit + Approval-Flow), CLI (Typer), Operator-API (FastAPI, read-only/guarded/audit), Operator-Observability + verständliche Statusmeldungen.
- **ZIELBILD:** dedizierte Smartphone-App, Sprachinteraktion, persönlicher KAI-Assistent als durchgängige Kommunikations- und Steuerungsoberfläche, breitere Multichannel-Präsenz.

### H. Kontrollierte Ausführungs-/Trading-Schicht
- **LIVE:** Marktanalyse, Signalbewertung, Watchlist, Simulation, **kontrolliertes Paper-Trading** (`PaperExecutionEngine`, 16-State-Lifecycle, Slippage+Fees), Entscheidungsprotokollierung, Risikoauswertung, Operator-Signal-Bridge im **Approval-Mode**.
- **VORBEREITET:** `LiveExecutionEngine` + `ExecutableOrderIntent` (einheitlicher Paper/Live-Vertrag), Live-Mode **disabled** — Gates ungeöffnet.
- **ZIELBILD:** Execution-Flows nur mit Governance, Berechtigungen, Compliance, Logging und Human-in-the-loop. KAI ist **kein** autonomer Gewinnversprechen-Trading-Bot.

---

## Nicht-Ziele (konsequent vermeiden)

- KAI als einfacher Trading-Bot, garantierter Gewinnbringer oder Blackbox.
- KAI als reine Persona, reines Dashboard, reines Telegram-/Crypto-Tool.
- KAI als vollständig autonomes Finanzsystem ohne Kontrolle.
- Live-Trading als bereits produktiv/sicher darstellen, solange es das nicht ist.
- Tor-Analyse als Interaktion mit illegalen Märkten/Angeboten darstellen.
- DeFi ohne Smart-Contract-/Bridge-/Oracle-/Gas-/Wallet-/Compliance-Risiken darstellen.
- Lightning ohne Liquiditäts-/Channel-/Routing-/Gebühren-/Node-/Backup-/On-chain-Fallback-Themen darstellen.

---

## Verweise

- **Architektur-Einstieg:** `ARCHITECTURE.md` (tragende Strukturen, Lifecycle, AuditStream)
- **Execution-Directive für Agenten:** `CLAUDE.md`
- **Verträge/Invarianten:** `docs/contracts.md`, `docs/adr/`
- **Entscheidungshistorie:** `DECISION_LOG.md`
- **Persona / Look & Feel (nicht-technisch):** `KAI-Persona/`, `docs/kai_persona/`
- **Historische Identitäts-/Prompt-Artefakte:** `docs/archive/` (klar als Archiv markiert)
