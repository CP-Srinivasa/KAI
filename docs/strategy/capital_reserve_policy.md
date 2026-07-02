# Kapital- & Reserve-Policy

**Stand:** 2026-07-01 · **Bezug:** ADR 0013 · **Status:** Policy + inertes Scaffolding (shadow-only)
**Grundsatz:** Nicht jeder Gewinn zurück in Trades. Ein Teil systematisch aus dem Risikokreislauf herausnehmen und real sichern. Buckets **strikt getrennt, nie vermischt.**

## Buckets

| Bucket | Zweck | Ort | Regel (Operator kalibriert Zahlen) |
|---|---|---|---|
| **Operating/Trading** | aktive Trades | CEX-Ramp / DEX | Max-on-Venue-Cap; Überschuss zeitnah in Self-Custody |
| **Reserve** | Risiko-Rücklage | Cold-Multisig | fixer Gewinn-%-Anteil raus aus Risikokreislauf, **nicht** re-investiert |
| **Langfrist-Hold** | strateg./inflations­geschütztes Halten | Cold-Multisig | Reserve-Überschuss periodisch hierher; > 1-J.-Haltefrist (Steuer §23 EStG) |
| **Experiment/Lern** | Live-Lern-Tranche + Hochrisiko-Sandbox | separates Wallet/Konto | hart gedeckelt, isoliert, Verlust eingeplant |

## Gewinnverwendungs-Logik

1. Bei **Realisierung** eines Gewinns: fixer %-Split → Reserve (aus Risikokreislauf raus).
2. Reserve-Überschuss über Zielhöhe → periodisch → Langfrist-Hold.
3. Sweep-Intervalle regelbasiert (z. B. wöchentlich Exchange → Cold über Withdrawal-Whitelist).
4. Re-Investition nur aus Operating/Trading, nie aus Reserve/Langfrist.

## ⚠ Live-Readiness vs. Edge-Realität

Der gemessene Edge ist belastbar widerlegt (canonical-edge, NO_GO). Auflösung:
- Voller Live-Apparat wird gebaut (Bereitschaft).
- **Erstes reales Kapital = hart gedeckelte Lern-Tranche** (Ops-/Parität-/Gebühren-Wahrheit), isoliert im Experiment-Bucket — *nicht* Edge-Wette.
- **Edge-abhängige Skalierung bleibt gegated** auf `edge_validation_gate=grün` (DSR/MinTRL). Bewusste Operator-Entscheidung mit Pre-Mortem, nicht still erzwungen.

## Segmentierungs-Scaffolding (inert, shadow-only)

Neues Modul `app/capital/{segmentation,reserve_policy}.py` — Blaupause `app/lightning/treasury.py` (`compute_treasury_snapshot`, 3-Konten earnings/operating/tradable), verallgemeinert auf die 4 Buckets, Fiat/USD-fähig.

- **Append-only Ledger**, auditiert über `app/audit/kai_audit_service.py` (Hash-Chain).
- `ReserveSettings` in `app/core/settings.py` (`env_prefix=CAPITAL_`): default **alles inert** — `CAPITAL_SEGMENTATION_ENABLED=false`, `CAPITAL_APPLY_ENABLED=false`.
- Guardrail (`validate_mode_guardrails`-Erweiterung): **Apply nur mit HOTP + Edge-Gate grün**.
- Bucket-Promotion/-Transfer als Transition-Whitelist (Muster `app/learning/source_lifecycle.py`); jede reale Bewegung müsste durch `app/lightning/control_gate.py` `verify_capital_confirm` (im Scaffolding nur simuliert).
- Ausgabe rein rechnerisch/empfehlend („welcher Split wäre fällig") — **null Ausführung, keine reale Kapitalbewegung.**

## Nachweis/Steuer

Ein revisionssicheres Ledger bedient zugleich SoF/AML (GwG), TFR-Ownership-Proof (`wallet_security.md`) und Steuer (§23 EStG > 1 J. steuerfrei / Freigrenze 1.000 €; §20 Derivate; FIFO wallet-bezogen, BMF 06.03.2025). **Kein Steuerrat** — Detail über Steuerberater.

## Verifikation

`pytest` `app/capital/`: Split-Korrektheit, Guardrails (kein Apply ohne HOTP+Edge-Gate), Audit-Integrität, **Assertion: keine reale Kapital-/Order-Bewegung**.
