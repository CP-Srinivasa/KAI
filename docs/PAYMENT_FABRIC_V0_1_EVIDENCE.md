# KAI SOVEREIGN VALUE-OS v0.1 — Abschlussbericht und Evidenz (Payment Fabric)

**Stand:** 2026-09-04 · **Release:** PR [#861](https://github.com/CP-Srinivasa/KAI/pull/861) → Mainline `be092fab` (Squash-Merge 2026-09-03 21:52 UTC, CI 9/9) · **Spezifikation:** ADR 0018 · **Entscheidung:** D-CORE-002 · **Runbook:** `docs/runbooks/payment_fabric.md` · **Zielsystem:** Pi 5 (`kai-pi5`), Modus SHADOW (LIVE-Fenster 2026-09-04 geschlossen).

Jede Aussage trägt eine Klassifikation: **IMPLEMENTED** (Code im Baum) · **TESTED** (reproduzierbarer Test) · **VERIFIED** (am Gerät belegt) · **DESIGNED** (nur Architektur) · **DEFERRED** (bewusst vertagt) · **BLOCKED** (extern/Operator).

## 1. Architecture — IMPLEMENTED / TESTED / VERIFIED (SIMULATION)

`PaymentIntent → Policy → Authorization (HOTP) → Rail-Execution → Settlement → Reconciliation → Proof` in `app/payments/` (35 Module, keins ≥ 350 LOC, netto +14.145 / −136 Zeilen inkl. Tests und Doku). Ein Serialisierungspunkt (`PaymentService`, Journal-Lock), ein Geldjournal (`artifacts/payments/payment_journal.jsonl`, hash-verkettet, 0600, Stream-Vertrag G4), ein sendender Prozess (`kai-server`), Timer nur für Outcomes. `app/lightning/` ist Rail-Adapter darunter und importiert `app/payments` nie (AST-Gate `tests/unit/test_payment_dependency_direction.py`). Fremd-Rails nur als `RailCapabilities` (DESIGNED, § 13).

## 2. Existing Adapter Forensics — VERIFIED (Berichte im Sprint-Ordner)

33 Einheiten in `app/lightning/`: **KEEP 11 · FIX 8 · REWRITE 2 · DELETE 1 · DEFER 9 · QUARANTINE 2** (SATOSHI). Kernbefunde: vier offene Idempotenz-Lücken (Cap-Race ohne gemeinsamen Lock, keysend mit neuem Preimage je Versuch, Reconciler blind für keysend/on-chain/Channel, Idempotency-Store auf 1.000 Keys mit Eviction), kein Fee-Limit (Default 0, kein Aufrufer), Lesepfad mit Invoice-Macaroon, `error`-Intents nie re-evaluiert, drei Zustandsvokabulare ohne Transitionsvalidierung, Destination aus Decode nie gebunden/journalliert. Red-Team: Persistenz JSONL-only GRÜN, SQLite als Wahrheit ROT; Unbekannt muss `IN_FLIGHT/RECONCILIATION_REQUIRED` sein. SENTR: Node mainnet, synced, lnd 0.19.3, 1 Kanal, TLS-Pinning trägt, kein `admin.macaroon`, `APP_ENV` nicht gesetzt.

## 3. KEEP / FIX / REWRITE / DELETE / DEFER (Bestand nach Sprint)

| Kategorie | Inhalt | Status |
|---|---|---|
| KEEP (Rail-Adapter) | `client`, `adapter`, `cache`, `plan_guards`, `golive_preflight`, `backup_monitor`, `lightning_settings` (TLS-Boot, Capability-Scopes) | IMPLEMENTED |
| FIX (im Control Plane gelöst) | Fee-Limit Pflicht · Sync-/Wallet-Gate · harter Tages-Cap · Idempotenz ohne Evict · Destination-Bindung · Unbekannt ≠ FAILED · Scope-Kollisions-Guard | IMPLEMENTED/TESTED |
| REWRITE (ersetzt) | `value_layer.pay_invoice`-Sendeweg → `PaymentService`; `ln_control.pay_invoice` delegiert | IMPLEMENTED/TESTED |
| DELETE (nach 7 Tagen Dual-Read) | `value_layer`, `ops_ledger` (v1+v2), `reconciliation`, `policy`, `control_gate`, `idempotency_store`, `ops_annotations`, Reste `ln_control` (~2,7k LOC) | DEFERRED |
| DEFER | keysend, `send_coins`, `open/close_channel` (Policy DENY `unsupported_action`) · L402/Oracle-Revenue, `demand_*`, `earnings_*`, `treasury`, `reputation`, `selfpay` (QUARANTINE, nicht Payment-Kern) | DEFERRED |

## 4. Implemented Changeset — IMPLEMENTED

21 Commits auf `core/value-os-v0.1` (Forensik-Berichte → ADR 0018 → S1 Domain/State/Journal/Idempotenz/Policy/Settings → S2 Rail-Interface/SimulationRail/LightningRail/Service → S3 Reconciliation/Health/Backup → S4 API/Lifespan/Delegation/E2E/Failure-Injection → Doku, Fiat-Bridge, God-File-Extraktion `health_check_payments.py`, Starlette-1.x-feste Tests, Testisolation `runtime_identity`). Neue Oberflächen: `GET /health/payment`, `payments`-Sektion in `/health/config`, `POST/GET /payments/intents[/{id}[/simulate|/execute]]`, `POST/GET /payments/invoices[/{ref}]`, `GET /payments/audit?intent_id=`.

## 5. Payment State Machine — IMPLEMENTED / TESTED

`REQUESTED → {DENIED, AWAITING_APPROVAL, AUTHORIZED} → SUBMITTED → {IN_FLIGHT, SETTLED, SETTLED_REVERSIBLE, FAILED_FINAL, RECONCILIATION_REQUIRED}`; `IN_FLIGHT → {SETTLED, SETTLED_REVERSIBLE, FAILED_RETRYABLE, FAILED_FINAL, RECONCILIATION_REQUIRED}`; `FAILED_RETRYABLE → {AUTHORIZED, FAILED_FINAL}` nur mit Node-Evidenz; `RECONCILIATION_REQUIRED → {SETTLED, FAILED_FINAL}` nur durch den Reconciler; `SETTLED_REVERSIBLE → {SETTLED, REVERSED}` nur bei `reversal_supported`; terminal: `DENIED, SETTLED, REVERSED, FAILED_FINAL, EXPIRED, CANCELLED`. Genau eine Vergabestelle `app/payments/status.py::transition`; Timeout/Transport/Unbekannt → `RECONCILIATION_REQUIRED`, nie `FAILED` (Tests `tests/unit/payments/test_status.py`, parametrisiert über alle erlaubten und verbotenen Übergänge).

## 6. Security Evidence — TESTED / VERIFIED

- Macaroons least privilege: Read-Scope auf dem Pi seit 2026-09-03 auf `readonly.macaroon` (**VERIFIED**; Boot-Guard `validate_payment_boot` verweigert Scope-Kollision — am Gerät bewiesen, siehe § 12). Agenten erhalten keine Macaroons (Intent-only, **TESTED**).
- TLS: `validate_lightning_boot` (Existenz/PEM/Ablauf), Negativkontrolle ohne `--cacert` scheitert (**VERIFIED**, SENTR).
- Secrets: Journal-Redaktions-Allowlist (BOLT11/Pubkey/Preimage überleben den Append nicht, **TESTED**), keine Secret-Strings in 15 Failure-Injection-Fällen (**TESTED**), `/health/config` Fingerprints (**VERIFIED**).
- API-Auth: `/payments/*` in keiner Local-Bypass-Liste, 401 auch von 127.0.0.1, falscher Bearer 403 (**TESTED**); Idempotency-Key Pflicht (**TESTED**); HOTP für `execute` (**TESTED**, Fake-Verifier; realer HOTP im LIVE-Fenster).
- Amount/Destination/Fee: `amount_requested > 0`, Destination aus Decode gebunden und gegen Allowlist geprüft, `fee_limit ≤ 0` = DENY (**TESTED**).
- Fail-closed: erste DENY gewinnt, Regel-Exception = DENY, unsynced/locked/offline = DENY (**TESTED**); Production-Konfiguration: LIVE nur mit `APP_ENV=production`, `APP_LN_PAY_ENABLED`, Payment-Macaroon, HOTP-Seed, Fee-Limit (**TESTED**; am Gerät zeigt `live_gate` seit 2026-09-04 `app_env_production=true, pay_enabled=false, hotp_seed_present=true, fee_limit_ok=true` — **VERIFIED**).
- Offen: Journal-/HOTP-Dateien des Altpfads `664` (Operator-chmod), `KYT_ADDR_SALT`-Default, unkeyed Hash-Kette ohne Truth-Anker (**DEFERRED**).

## 7. Test Evidence — TESTED

Payment-Suite: **627 passed, 1 skipped** (Unit `tests/unit/payments/*` + 3 Integrations-E2E + Dependency-/Backup-Gate). Vollsuite lokal unter Lock-Versionen (fastapi 0.141.1 / starlette 1.6.0): **10011 passed, 44 skipped, 2 xfailed** (Exit 0). CI-Lauf für #861: Tests pass, 9/9 Jobs grün. Gates: ruff/format grün, God-File-Ratchet grün (health_check.py 1795/1800 nach Extraktion), Stream-Consumer-Ratchet grün (neuer Strom mit Vertrag + `alternative_watcher`), mypy strict 682 Dateien.
Failure-Injection (Mission §20, `tests/integration/test_payment_failure_injection.py`, 15 Fälle + 2 SHADOW-Querschnitte): LND offline · Node unsynced · Wallet locked · TLS-Fehler · ungültiger Macaroon · Invoice expired · unbekannte Invoice · Payment-Timeout → `RECONCILIATION_REQUIRED` · insufficient liquidity → `FAILED_FINAL` mit Evidenz · route failure → `FAILED_RETRYABLE` · Prozess-Crash (`submitted` ohne Antwort → `recover()` → Reconcile mit Node-`SUCCEEDED` → `SETTLED`, kein zweiter Send) · Journal nicht schreibbar → kein Send · doppelter API-Request → Replay, ein Send · Settlement-Event verloren → Reconcile erkennt · Antwort verloren, Zahlung erfolgreich → Lookup → `SETTLED`, kein Re-Send. Jeder Fall asserted `rail.pay ≤ 1` und ein Journal ohne Secret-Strings.

## 8. Lightning Integration Evidence

- Node-Erreichbarkeit read-only vom Pi: `/v1/getinfo` 200, mainnet, synced chain+graph, lnd 0.19.3-beta, 1 aktiver Kanal (**VERIFIED**, SENTR 2026-09-03).
- `LightningRail` gegen gefakten Client: Timeout → UNKNOWN, Fee-Limit erzwungen, unsynced → unhealthy, Contract-Suite (**TESTED**).
- Reconcile-Timer auf dem Pi liest den Node über den readonly-Scope: erster Lauf 2026-09-03 22:01:55 UTC `Result=success`, `reconcile_state.json {status ok, orphans 0, clock_anomaly false}` (**VERIFIED**).
- SHADOW am Gerät (2026-09-04): `APP_ENV=production` + `APP_PAYMENT_MODE=shadow`, Rail `reachable=true`, synced, Wallet entsperrt; SHADOW-Preview mit der Operator-Invoice: Intent `pi_4686fe9107a04350` → `AWAITING_APPROVAL` (`approval_threshold`), Quote 1.000 sat + 3 sat Schätzung (`estimate_source=settings_ppm`) (**VERIFIED**).
- **LIVE-Send am Gerät (2026-09-04 07:58:57–07:59:00 UTC) — VERIFIED:** Fenster `APP_PAYMENT_MODE=live`, `APP_LN_PAY_ENABLED=true`, Allowlist = `sha256(payee)`, Limits 1.000/1.000 sat, Fee-Max 5 sat, HOTP ab 1 sat. Intent `pi_f5648a5eb9854ae1` (Idempotency-Key `live-window-20260904-04`, Ziel: Operator-Invoice aus Wallet of Satoshi, 1.000 sat): `intent_created` → `policy_decided` (`REQUIRES_APPROVAL`) → `approval_granted` (HOTP vom YubiKey, Zähler 0) → `submitted` (1.000 sat, Versuch 1, `rail_dedup_key`) → `rail_responded` (`observed_status=SUCCEEDED`, `evidence_source=rail_response`) → `settled` (`amount_settled=1000`, `fee_actual=4`, `proof_hash=938d743e…`). Zweiter `execute` mit demselben Code → `replayed=true, already executed`, **kein zweiter Send**. Node-Lookup über `readonly.macaroon`: `SUCCEEDED`, `value_sat=1000`, `fee_sat=4`. Reconciler-Lauf 08:00:13 UTC: `ok`, `orphans=0` (Node-Zahlung dem Intent zugeordnet). `/health/payment`: `last_settlement 07:59:00Z / 1000`, `fees=4`, `settlement_latency_p50=2,9 s`, `policy_reject_count=1` (die initiale Allowlist-DENY). Rückbau unmittelbar danach: `APP_PAYMENT_MODE=shadow`, `APP_LN_PAY_ENABLED=false`, Allowlist leer, `live_gate.pay_enabled=false` bestätigt.
- Befunde aus dem Fenster: (1) Drei HOTP-Fehlversuche vor dem Erfolg — der YubiKey-Eintrag trug ein anderes Secret als der Pi-Seed (erst nach Neu-Provisionierung + Neuanlage des Eintrags `match at counter [0]`); Lehre: HOTP-Provisionierung gehört ins Runbook als Vorbedingung mit lokalem Zähler-Check. (2) **Intents überleben keinen Server-Neustart** (Journal hält nur Hashes, Ziel-BOLT11 liegt nur im Prozessspeicher) → nach jedem Restart neuer Intent; als DEFERRED-Verbesserung: verschlüsselte Ablage der Destination oder Re-Submit-Pfad. (3) Der Betriebs-API-Key erschien im Terminal-Paste des Operators und wurde nach dem Fenster rotiert (alter Key → 403 bestätigt).

## 9. Merchant Use Case (Self-Use-Receivable) — TESTED

`tests/integration/test_payment_merchant_selfuse_e2e.py`: Invoice mit `order_ref` → simuliertes Settlement → Reconcile → `receivable_settled` mit `order_ref` → Invoice-Status settled → Service-Neustart auf demselben Journal → identischer Zustand, erneuter Reconcile ohne neue Records. Dritt-Merchant bleibt Tier-2-STOP (ADR 0016).

## 10. Agent Payment — TESTED

`tests/integration/test_payment_agent_e2e.py` mit `config/payment_agent_limits.json`: unter Limit → ALLOW → SIMULATION → `SETTLED` → Agent erhält Status/`proof_hash`/`amount_settled`; über `max_amount` → `DENIED` mit `rule_id`; über Approval-Schwelle → `AWAITING_APPROVAL` → ohne HOTP 4xx → mit HOTP → `SETTLED`; Tages-Cap → `DENIED`. Agenten erzeugen ausschließlich Intents.

## 11. Reconciliation — TESTED / VERIFIED

Vorwärts (offene Intents ↔ `rail.lookup`), rückwärts (Node-Zahlungen ohne Intent → `orphan_settlement` + Alarm über `_check_payment_reconciliation`, P0-Klasse), Receivables, Uhr-Sprung-Guard; Timer-Prozess sendet nie (Spy-Test); zweimaliger Lauf idempotent. Am Gerät: erster Lauf `ok` (§ 8). Bekannte Grenze: Alt-Journal (`ln_ops_ledger_v2`) und Payment-Journal werden noch nicht gegeneinander abgeglichen (**DEFERRED**).

## 12. Deployment Evidence — VERIFIED (mit Vorfall)

BUILD: Lock-Install unverändert (keine neue Dependency). CONFIG VALIDATION: `validate_payment_boot` im Lifespan — **hat auf dem Pi den Start verweigert** (Scope-Kollision `APP_LN_MACAROON_PATH == APP_LN_INVOICE_MACAROON_PATH`), Deploy-Urteil `DEPLOY_FAILED (HEALTH_NOT_200:000, SYSTEMD_CHANGE_REQUIRES_OPERATOR:41)`, Ausfall ≈ 4 min; Remediation: `.env` gesichert (`.env.bak-20260903-macaroon-scope`, 0600), Read-Scope auf `readonly.macaroon`, systemd-Restart → START: `/health` 200, `runtime_commit == checkout_commit == be092fab`. HEALTH: `/health/payment` 200 `status ok`, `mode simulation`, Journal-Kette ok (seq 0), `/health/ai` 200, `/health/config` mit `payments`-Sektion; alle sechs Dauerläufer `active`. SIMULATION TEST: Payment-Suite + E2E grün (§ 7). INTEGRATION TEST: Reconcile-Timer erster Lauf ok (§ 8). RESULT: Code live, Unit-Drift 41 (Operator-Apply offen).

## 13. Fiat-Bridge Architecture — DESIGNED

`docs/PAYMENT_FIAT_BRIDGE_ARCHITECTURE.md`: beide Flüsse als Intent je Rail-Hop mit `correlation_id`, Verantwortungstrennung (Orchestration · Custody · Liquidity · FX · Settlement · Banking · Compliance · Accounting · Reconciliation), `RailCapabilities`-Matrix für Lightning, Onchain, SEPA, SEPA Instant, Bankkonto, Karte/PSP, Merchant-PSP, Stablecoin; drei Modell-Ergänzungen (G-1 `SettlementGroup`, G-2 Kompensations-Intent statt Rückwärts-Übergang, G-3 `PaymentMandate` + `finality_reason`); nicht-custodiale PoC-Pfade; acht offene Rechtsfragen; Wellen 0–3. Kein Fiat-Modul im Repo (ADR 0016 Tier-2-STOP).

## 14. Remaining Blockers

- **Unit-Drift 41 (BLOCKED, Operator):** `sudo bash scripts/pi_apply_systemd_units.sh` (EnvironmentFile-Härtung aus CORE v1 + Value-OS).
- **`APP_ENV=production`** seit 2026-09-04 gesetzt (**VERIFIED**, Boot mit hartem `validate_secrets`).
- **Altpfad-Rückbau (DEFERRED, 7 Tage Dual-Read):** ~2,7k LOC; Reconciler-Abgleich beider Journale; `PaymentService.get()` journal-first; `/health/payment` über Index statt Voll-Read.

## 15. Merge / Commit / Release State

Mainline `claude/p7/reentry-ia-codex-cycle` @ `be092fab` (#861, 88 Dateien). Pi läuft auf `be092fab` (`/health` runtime == checkout, Drift 0). Alle Sprint-Artefakte (Forensik, Reports, Logs) unter `C:\tmp\kai-vos-reports\`.

## 16. Next Three Actions

1. Runbook um HOTP-Provisionierung (YubiKey/Authenticator, lokaler Zähler-Check) und um den Hinweis »Intent überlebt keinen Restart« ergänzen; Rückweg testen: Wallet-of-Satoshi-Guthaben auf eine KAI-Invoice zurücksenden (Self-Use-Receivable real).
2. Operator: `pi_apply_systemd_units.sh`, `APP_ENV=production`, `chmod 0600` der Altpfad-Journale.
3. Nach 7 Tagen Dual-Read: Altpfad-Rückbau (ADR 0018 § 12) und Reconciler-Abgleich beider Journale.

## Acceptance Gates (Mission § 24)

1 Control Plane existiert — IMPLEMENTED/VERIFIED · 2 PaymentIntent einziger Einstieg (Sendeweg) — IMPLEMENTED/TESTED (Altpfad-Journal bleibt bis Rückbau lesbar) · 3 Lightning als Rail-Adapter — IMPLEMENTED/TESTED · 4 State Machine — TESTED · 5 Policies — TESTED · 6 Idempotenz — TESTED (Threads + Prozesse) · 7 Settlement-Erkennung — VERIFIED (realer Send 1.000 sat, `settled` + Node-Lookup + Reconciler) · 8 Neustart ändert nichts — TESTED · 9 Reconciliation — TESTED/VERIFIED (erster Lauf) · 10 Audit/Truth Chain — TESTED (Hash-Kette, Tamper, Torn-Tail) · 11 Simulation — TESTED/VERIFIED · 12 Merchant E2E — TESTED · 13 Agent E2E — TESTED · 14 Node-/LND-Ausfälle fail-closed — TESTED · 15 Security-P0 geprüft — VERIFIED (SENTR) mit offenen Operator-Punkten · 16 Deployment reproduzierbar — VERIFIED (inkl. dokumentiertem Vorfall) · 17 Fremd-Rails über dasselbe Interface — DESIGNED · 18 Keine unnötige Komplexität im Core — VERIFIED (Ratchets grün, keine Zeile in `settings.py`, keine neue Dependency).
