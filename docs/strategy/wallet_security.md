# Wallet- & Sicherheitsstrategie (self-custody-first)

**Stand:** 2026-07-01 · **Bezug:** ADR 0013 · **Grundsatz:** Souveränität als Default — Keys beim Operator.

## Custody-Modell

| Ebene | Instrument | Zweck | Betrag |
|---|---|---|---|
| **Cold (Reserve/Langfrist)** | Multisig 2-von-3, verschiedene Hersteller (BitBox02 + Coldcard + Trezor); Air-Gap für BTC (Coldcard) | Rücklagen, Langfrist-Hold | Hauptbestand |
| **Self-hosted Node** | RaspiBlitz/lnd | Infra-Souveränität, LN-Micro-/Settlement | begrenzt |
| **Hot/Interaktion** | Phantom/MetaMask | DeFi/DEX-Interaktion | minimal, transient |
| **CEX-Ramp** | Kraken/Coinbase/Bitpanda | Fiat-Ein/Ausstieg, aktives Trading | nur benötigtes Volumen |

**Regel:** CEX-Bestände minimieren — nur aktives Trading-/Ramp-Volumen auf der Börse; Rest zeitnah in Self-Custody. LN ist Hot-Wallet → **nie** Cold-Storage; Channel-Backups (SCB) diszipliniert.

## Schlüssel- & Aktions-Sicherheit (bestehende Primitive)

- Jede irreversible Kapital-Aktion (Withdraw, Transfer, Live-Order) über `app/lightning/control_gate.py` `verify_capital_confirm` — Plan-Hash + Idempotency-Key + frischer HOTP.
- Live-Engine (`app/execution/live_engine.py`): Boot-State **LOCKED**, 5-Gate-Chain (HOTP→Caps→Risk→Perms→Server-SL), 60-min Idle-Auto-Lock, Audit `artifacts/security/live_execution_audit.jsonl`.
- `app/security/exchange_perms.py`: **Withdraw-OFF sofern nicht bewusst freigegeben** + IP-Allowlist-Verifikation; `live_caps.py`: Hard-Caps.

## Withdrawal-Whitelisting & Ownership-Proof (TFR)

- Nur **eigene, verifizierte** Adressen im CEX-Whitelist; Withdraw-2FA aktiv.
- Transfers Cold ↔ Börse > 1.000 €: **Ownership-Proof** (Satoshi-Test / signierte Nachricht) durchführen, Tx-Hash + Beleg im **Provenienz-Ledger** ablegen.
- Adress-Register: alle eigenen Empfangs-/Sende-Adressen (xpub/Ableitungspfad-Referenz, HW-Gerät-Zuordnung) auditierbar.

Dies ist **Nachweis-Hygiene** (belegen, nicht verschleiern) — bedient zugleich SoF/AML und Steuer.

## Frontier-Bezug (permissionless)

Der eigentliche Access-Layer (ADR 0013 Tier 1) ist self-custody/On-Chain: DEX-Interaktion signiert lokal, Node self-hosted. Automatisierung ist bewusst begrenzt — finale Signatur bei Hardware-Wallets bleibt physisch (Sicherheits-Feature). `satoshi`-Agent prüft Wallet-/Custody-/Ownership-Proof-Pfade und DEX-Contract-Risiken vor Merge.

## Verifikation

`security-review` + `satoshi` vor Merge kapitalnaher Änderungen; Contract-Test: Withdraw ohne Whitelist/HOTP nicht möglich; Ownership-Proof-Ledger vollständig exportierbar.
