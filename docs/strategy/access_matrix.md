# Zugangs-/Anbieter-Matrix (permissionless zuerst)

**Stand:** 2026-07-01 · **Für:** DE-Privatoperator · **Bezug:** ADR 0013 (Frontier & Boundary)
**Prinzip:** On-Chain/permissionless = First-Class-Access; zentrale Börsen nur als **regulierte Fiat-Brücke**. Quellen live verifiziert; Scores (API/Sicherheit 1–5) sind Einordnung, nicht anbieterzertifiziert. **Kein Rechtsrat.**

## Regulatorischer Anker (kompakt)

- DE-MiCA-Grandfathering **ausgelaufen 31.12.2025** (§ 50 KMAG) → Fiat-Ramp nur MiCAR-autorisierte CEX. Letztgültige Quelle: **ESMA-CASP-Register** (wöchentlich, vor Go/No-Go namentlich prüfen).
- Derivate ≠ MiCA (MiFID II; Retail-Leverage 30:1–2:1). MiCAR-konforme Stablecoins = **USDC/EURC** (USDT EU-delistet).
- Self-Custody/Protokoll-Nutzung liegt außerhalb der CASP-Pflicht; Dienst *für Dritte* triggert das Lizenz-Gate (ADR 0013).

## Matrix

| Schicht | ✅ SOFORT | 👁 BEOBACHTEN | ⛔ VERMEIDEN (Live/DE) |
|---|---|---|---|
| **Permissionless Access** 🚀 | DEX-Aggregation (On-Chain), self-hosted Node (RaspiBlitz/lnd), Lightning | L2s/Bridges (Provenienz-Risiko, `satoshi`-Review) | — |
| **Fiat-On/Off-Ramp (CEX)** | Kraken (CASP IE/CBI), Coinbase (CASP LU/CSSF), Bitpanda (BaFin+FMA, DE-Fiat) | OKX (CASP MT + MiFID + EMI), Bybit (nur Spot EU; Hack 02/25) | Binance (keine MiCA, EU-Spot-Stopp), Bitfinex (keine MiCA), BitMEX (Deriv./geoblockt), Poloniex (keine MiCA, Hack 11/23) |
| **Daten** | CoinGecko (Free 100/min, 10k/Mon), CoinMarketCap (Redundanz), Dune (On-Chain-SQL), Etherscan/Blockchair (Explorer) | Glassnode (~79 $/Mon, On-Chain), Arkham, Nansen (49–69 $/Mon) | Kaiko/Amberdata, Messari, CCData/CoinDesk (Enterprise bzw. Free-Tier entfallen 05/26) |
| **Cold-Wallet** | BitBox02 (Open-Source+SE, Multi-Asset), Coldcard (BTC, Air-Gap) | Trezor (voll Open-Source) | — (Ledger nachrangig: Closed-FW + Recover-Delle) |
| **Hot/Interaktion** | — | Phantom (Multichain, Self-Custody), MetaMask (EVM) | — |
| **KYT/Provenienz** | Etherscan/Blockchair (quasi gratis) | Arkham (Self-Serve API), Nansen | Chainalysis/TRM/Elliptic (50k–250k $/Jahr, unerreichbar) |

## Scores (Kurz)

| Anbieter | Kat. | EU/DE-Status | API | Sicherh. | Reg-Risiko | Testnet | Empf. |
|---|---|---|---|---|---|---|---|
| Kraken | CEX | CASP IE, Spot DE ✓ | 5 | 5 | niedrig | ja | SOFORT |
| Coinbase | CEX | CASP LU, Spot DE ✓ | 5 | 4 | niedrig | ja | SOFORT |
| Bitpanda | CEX/Broker | CASP BaFin+FMA, DE ✓ | 3 | 4 | niedrig | nein (unbest.) | 👁 (Fiat-Ramp) |
| OKX | CEX | CASP MT+MiFID+EMI | 4 | 4 (unbest.) | niedrig | ja (Demo) | 👁 |
| Bybit | CEX | CASP AT, nur Spot EU | 4 | 3 | niedrig–mittel | ja (Demo) | 👁 |
| Binance | CEX | keine MiCA, EU-Stopp 1.7. | 5 | 4 | hoch | ja | ⛔ (live) |
| Bitfinex/BitMEX/Poloniex | CEX | keine MiCA | 2–4 | 2–3 | hoch | tw. | ⛔ |
| CoinGecko/CMC/Dune | Daten | Free ✓ | 4 | n/a | niedrig | n/a | SOFORT |
| BitBox02/Coldcard | Cold | DE-Versand ✓ | PSBT | 5 | niedrig | tw. | SOFORT |
| Etherscan/Blockchair | KYT | Free/Lite ✓ | 4 | n/a | niedrig | n/a | SOFORT |

## Empfehlungs-Lean je Use-Case

- **Datenanalyse/Backtesting:** CoinGecko primär + CoinMarketCap-Redundanz + Dune (On-Chain-SQL); Glassnode gezielt bei On-Chain-Signalbedarf. ToS: keine Redistribution, CoinGecko-Attribution.
- **Paper/Testnet:** Kraken/Coinbase-Sandbox (regulatorisch sauber) zuerst; OKX/Bybit-Demo als Zweitoption; BTC/LN via Coldcard-Testnet + RaspiBlitz-Testnet.
- **Live-Spot (Fiat-Ramp):** Kraken primär, Coinbase redundant; Bitpanda DE-freundlich (API simpler). Derivate bewusst außen vor.
- **Souveräner Zugang (Frontier):** DEX/On-Chain self-custody als eigentlicher Access-Layer; CEX nur zum Ein-/Ausstieg Fiat.
- **Cold-Storage:** Multisig 2-von-3 über verschiedene Hersteller (BitBox02 + Coldcard + Trezor).

## Code-Bezug

Order-Adapter existieren nur für Binance/Bybit (`app/execution/exchanges/`). Neu: **Kraken/Coinbase-Ramp-Adapter** (erben `base.py`, in `factory.py` registrieren, dry_run/testnet-Default) + **DEX/On-Chain-Ausführungs-Layer** (Frontier, self-custody). `edge_validation_gate` bleibt aus dem Execution-Pfad (Invariante).

## Annahmen / unbestätigt

Scores = Einordnung. Kraken-Derivate-für-DE-Retail, OKX-Sicherheitshistorie, diverse Testnet-Existenzen (BitMEX/Bitfinex/Bitpanda) nur sekundär/unbestätigt. ESMA-Register vor jedem Go/No-Go prüfen.
