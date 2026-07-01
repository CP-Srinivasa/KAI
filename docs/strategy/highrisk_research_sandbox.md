# Hochrisiko-Forschungsrahmen (Microcaps / Memecoins / NFTs)

**Stand:** 2026-07-01 · **Bezug:** ADR 0013 (Tier 1) + ADR 0012 (Research/Truth) · **Status:** Spec, default inert.

## Zweck

Isolierter Forschungsbereich, um zu testen, ob KAI in **extrem illiquiden, sentimentgetriebenen, hochvolatilen** Märkten sinnvolle **Warnungen, Scores, Frühindikatoren** liefert — Rug-/Scam-Erkennung, Liquiditäts-/Konzentrations-Warnung, Sentiment-Frühsignal.

**Ausdrücklich NICHT:** blindes Spekulieren, kein naiver Alpha-Feed. Konsistent mit ADR 0012 (keine neuen naiven Generatoren) — hier zählt der **Research-/Falsifikations-Wert**, nicht Rendite.

## Isolation (nicht verhandelbar)

- Eigener **Experiment/Lern-Bucket**, eigenes Wallet, **hart gedeckeltes Fixbudget** — kein Commingling mit Trading/Reserve/Langfrist.
- Eigener Ledger + eigene Audit-Kennzeichnung (`kai_audit_service`).
- Strikt getrennt von normaler Trading-/Reserve-/Portfolio-Strategie.

## Fail-closed

- Default inert: `RESEARCH_HIGHRISK_ENABLED=false`.
- Signal-/Scoring-Erzeugung **shadow** (nur Beobachtung/Bewertung, keine Ausführung).
- Falls je eine reale Micro-Position: dieselben Kapital-Gates (`verify_capital_confirm` + HOTP) + separater, minimaler Cap; nie über das Fixbudget hinaus.

## Provenienz & Risiko

- Contract-/Rug-Risiko via `satoshi`-Agent (Tokenomics-vs-On-Chain-Konsistenz, Honeypot/Mint-Authority-Checks).
- Provenienz/Herkunft über Etherscan/Blockchair/Arkham — **Best-Effort-Selbstprüfung**, nicht regulatorische KYT-Erfüllung.
- Für NFTs/frühe On-Chain-Projekte: Wash-Trading-/Konzentrations-Heuristiken als Warn-Score.

## Metriken (Research-Output)

Trefferquote der Warnungen (z. B. „Rug binnen X Tagen"), Frühsignal-Vorlauf, Falsch-Positiv-Rate — geführt im Hypothesen-Ledger (`app/research/ledger.py`), BH-FDR-kontrolliert (`multiple_testing.py`), damit Sandbox-Signale nicht die Truth-Baseline verwässern.

## Verifikation

Scoring shadow reproduzierbar; Budget-Cap-Invariante (nie überschreitbar); Isolation-Test (kein Commingling zu anderen Buckets); default-inert-Assertion.
