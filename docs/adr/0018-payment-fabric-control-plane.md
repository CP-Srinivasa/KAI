# ADR 0018 — Payment Fabric: Payment Control Plane mit Lightning als erstem Rail

**Status:** Angenommen (v0.1-Bauvorgabe) · **Datum:** 2026-09-03 · **Bezug:** ADR 0014 (Schicht 4 Intent Layer), ADR 0016 (Sovereign Value OS, Self-Use, Invarianten 1–5), `docs/KAI_CORE_V1.md` (Kern bleibt schlank, `app/lightning/` war QUARANTINE).
**Forensische Grundlage:** SATOSHI (`satoshi_ln_forensics.md`), SENTR (`sentr_payment_p0.md`), Architect (`architect_payment_domain.md`), Red-Team (`redteam_payment_fabric.md`) — Sprint-Artefakte 2026-09-03.

## Entscheidung in einem Satz

KAI bewegt Wert ausschließlich über **eine** Kette — `PaymentIntent → Policy → Authorization → Rail-Execution → Settlement → Reconciliation → Proof` — verwaltet von einem Payment Control Plane in `app/payments/`, das das Domänenmodell besitzt; Lightning ist der erste Rail-Adapter und importiert `app/payments` nie (nur umgekehrt).

## 1. Scope v0.1 (bindend)

| | v0.1 | Begründung |
|---|---|---|
| **Aktionen im Control Plane** | `pay_invoice` (BOLT11, Dedup über `payment_hash`) und `create_invoice` + Invoice-Tracking (Self-Use-Receivable) | Nur dafür existiert eine Rail-seitige Dedup-Garantie (Red-Team ART-S-001/002) |
| **DEFERRED, per Policy DENY `unsupported_action`** | keysend, `send_coins`, `open_channel`, `close_channel` | keysend ohne deterministisches Preimage = doppelt zahlbar; on-chain/Channel irreversibel ohne Rail-Dedup — brauchen eigenes Dedup-Design |
| **Modi** | `SIMULATION` (Default; kein Node-Call, deterministischer SimulationRail) · `SHADOW` (Policy + Decode + Quote/Route-Preview per read-only Node-Calls, kein Send) · `LIVE` (Send nur bei `APP_ENV=production`, `APP_LN_PAY_ENABLED=true`, HOTP-Seed vorhanden, Fee-Limit > 0) | Mission §6; Environment und Modus nie implizit |
| **Fremd-Rails** | Nur `RailCapabilities`-Felder im Domänenmodell; **kein** `SepaRail`/`PspRail`-Modul, kein Stub | ADR 0016: Merchant/PSP/Dritte = Tier-2-STOP; jeder Fremd-Rail braucht ein eigenes ADR. Der vollständige Zielentwurf der Fremd-Rails (Flüsse, Verantwortungstrennung, Rail-Matrix, drei Modell-Lücken, Governance-Wellen) liegt als DESIGNED in `docs/PAYMENT_FIAT_BRIDGE_ARCHITECTURE.md` und begründet keinen Bau |
| **Merchant-Flow** | **Self-Use-Receivable**: KAI stellt eine Invoice für eine eigene Leistung aus, erkennt Settlement, bucht die eigene „Order" — kein Endpunkt für Dritte, kein Onboarding, kein Preis-Artefakt | Erfüllt den Self-Use-Test (ADR 0016 §Test 1–3); Dritt-Merchant bleibt gesperrt |
| **Agent-Flow** | Agent erzeugt Intent mit `actor=agent:<id>`; Policy prüft Agent-Limits (max amount, daily, purpose, counterparties, rail, approval threshold); Agent erhält nur Status/Resultat | Mission §13; keine Macaroons für Agenten |
| **Realer Settlement-Test** | Nur mit ausdrücklichem Operator-Go, Kleinstbetrag, Self-Payment/eigene Invoice, nach Reconciliation-Beweis | Kapital-Stop |

## 2. Paket und Abhängigkeitsrichtung

`app/payments/` — `models.py` (Domäne) · `status.py` (State Machine, einzige Vergabestelle) · `journal.py` (hash-verkettetes Write-ahead-Journal) · `policy.py` (deterministische Regelkette) · `idempotency.py` (Ledger-basiert) · `rail.py` (Protocol + `RailCapabilities`) · `rails/simulation.py` · `rails/lightning.py` (wrappt `app/lightning/client.py`, `adapter.py`) · `service.py` (Control Plane, Serialisierungspunkt) · `reconcile.py` · `health.py` · `input_rejections.py` (Umzug aus `app/lightning/input_contract_rejections.py`, bricht den Import-Zyklus lightning→truth→audit→lightning). Settings in `app/core/payment_settings.py` (keine Zeile in `settings.py`). Kein Modul ≥ 350 LOC. Richtung `payments → lightning` wird per AST-Test erzwungen (`tests/unit/test_payment_dependency_direction.py`).

## 3. Domänenmodell (Pydantic v2, frozen)

`Money(minor_units:int, currency:str, scale:int)` · `Asset` · `Counterparty(kind, ref_hash, display)` · `Fee(limit, actual)` · `ExchangeRateReference(source, rate, ts)` · `Quote(rail, amount, fee_estimate, route_hint_hash, ttl)` · `Invoice(rail, ref_hash, amount, payee_hash, expires_at, memo_hash)` · `PaymentIntent(intent_id, idempotency_key, correlation_id, actor, purpose, rail, destination, amount_requested, fee_limit, expiry, policy_refs, mode)` · `PaymentAttempt(attempt_no, rail_dedup_key, submitted_at, amount_sent, fee_actual, proof)` · `Settlement(amount_settled, fee_actual, proof{PREIMAGE|TXID|PROVIDER_REF}, finality, settled_at)` · `PaymentPolicyDecision(verdict ALLOW|DENY|REQUIRES_APPROVAL|RETRY_ALLOWED|RETRY_DENIED, reasons, rule_ids, evaluated_at)` · `PaymentAuditEvent(seq, ts, intent_id, event_type, payload, prev_hash, record_hash)` · `PaymentStatus` (Enum unten).
Vier Beträge bleiben getrennt: `amount_requested`, `amount_sent`, `amount_settled`, `fee_actual`. Keine Lightning-Felder in `PaymentIntent`; Rail-spezifisches lebt in `Counterparty.ref_hash`/`Attempt.rail_dedup_key` und im Adapter.

## 4. State Machine (genau eine Vergabestelle: `status.transition`)

| Von | Nach | Auslöser |
|---|---|---|
| `REQUESTED` | `DENIED` · `AWAITING_APPROVAL` · `AUTHORIZED` | Policy-Verdikt (unter Journal-Lock) |
| `AWAITING_APPROVAL` | `AUTHORIZED` · `CANCELLED` · `EXPIRED` | HOTP-Freigabe · Operator · Ablauf |
| `AUTHORIZED` | `SUBMITTED` · `EXPIRED` · `CANCELLED` | Write-ahead vor Rail-Call · Ablauf · Operator |
| `SUBMITTED` | `IN_FLIGHT` · `SETTLED` · `SETTLED_REVERSIBLE` · `FAILED_FINAL` · `RECONCILIATION_REQUIRED` | Rail-Antwort mit Evidenz; **Timeout/Transport/Unbekannt → `RECONCILIATION_REQUIRED`, nie `FAILED`** |
| `IN_FLIGHT` | `SETTLED` · `SETTLED_REVERSIBLE` · `FAILED_RETRYABLE` · `FAILED_FINAL` · `RECONCILIATION_REQUIRED` | Rail-Lookup/Reconciler mit Node-Evidenz |
| `FAILED_RETRYABLE` | `AUTHORIZED` · `FAILED_FINAL` | nur wenn Rail beweist „nichts bewegt" (Payment `FAILED` am Node) |
| `RECONCILIATION_REQUIRED` | `SETTLED` · `FAILED_FINAL` · `RECONCILIATION_REQUIRED` | ausschließlich Reconciler mit Node-Evidenz; nie automatisch terminal ohne Evidenz |
| `SETTLED_REVERSIBLE` | `SETTLED` · `REVERSED` | nur Rails mit `reversal_supported` (Lightning: nie) |
| terminal | — | `DENIED`, `SETTLED`, `REVERSED`, `FAILED_FINAL`, `EXPIRED`, `CANCELLED` |

Cap-Zählung: `SUBMITTED`, `IN_FLIGHT`, `RECONCILIATION_REQUIRED`, `SETTLED*` zählen gegen Limits; `FAILED_FINAL` nur mit Node-Evidenz nicht.

## 5. Journal, Lock, Idempotenz (Invariante 1)

- **Ein Artefakt, ein Format, ein Lock:** `artifacts/payments/payment_journal.jsonl`, append-only, jeder Record `prev_hash`/`record_hash` (SHA-256 über kanonisches JSON), fsync, Torn-Tail = Deny. Nie rotiert. In Backup-`REQUIRED_SOURCES` (zusammen mit `ln_ops_ledger_v2.jsonl`, `ln_hotp_journal.jsonl` — Architect P1).
- **Serialisierungspunkt:** Policy-Verdikt, Idempotenz-Konsum, Cap-Prüfung und Intent-Append passieren unter **einem** exklusiven Interprozess-Lock (portalocker, Muster `ops_ledger.py:634`). Alles davor Gelesene ist Vorschau.
- **Idempotenz:** `idempotency_key` ist journal-eindeutig (kein Store mit Eviction); gleicher Key → Wiedergabe des bestehenden Intents/Status (HTTP 200, `replayed=true`), nie zweiter Send. Rail-Dedup zusätzlich über `rail_dedup_key` (= `payment_hash`).
- **Ein sendender Prozess** (`kai-server`); der Reconcile-Timer hängt nur Outcomes an. Prozessstart: vollständige Kettenverifikation, danach In-Prozess-Index (Idempotency-Keys, offene Intents, Tageszähler) und inkrementelles Tail-Lesen vor jedem Append.
- **Uhr:** Ablauf aus `max_inflight_window` der Rail-Capabilities; Uhr-Sprung-Guard (monotone Zeit + Wall-Clock-Plausibilität), kein vorzeitiges Verfallen offener Intents.

## 6. Policy (fail-closed, deterministisch)

Regelkette in fester Reihenfolge, erste DENY gewinnt, Ergebnis mit `rule_ids`: `mode_and_environment` → `rail_capability` (`unsupported_action`) → `reserve_floor` (souveräner Kapital-Boden, `APP_PAYMENT_RESERVE_FLOOR_SAT`, Default 0 = aus; bewaffnet UND Liquidität unbekannt = DENY — nachgezogen aus `lightning/policy.py` beim Rückbau, §12) → `amount_limits` (per Zahlung, harter Tages-Cap = DENY, nicht `needs_confirm`) → `fee_limit_required` (≤ 0 = DENY) → `destination_allowlist` (Payee aus Decode gebunden, nie `None`) → `actor_limits` (Agent-Tabelle) → `purpose_allowed` → `node_health` (unsynced/locked/offline = DENY) → `liquidity` → `retry_policy` (Retry nur mit Node-Evidenz) → `approval_threshold` (REQUIRES_APPROVAL → HOTP). Jede Regel liefert ALLOW/DENY/REQUIRES_APPROVAL; Fehler in einer Regel = DENY.

## 7. Rail-Interface

```python
class PaymentRail(Protocol):
    name: str
    def capabilities(self) -> RailCapabilities: ...
    async def health(self) -> RailHealth: ...
    async def decode(self, destination: str) -> DecodedDestination: ...
    async def quote(self, intent: PaymentIntent) -> Quote: ...
    async def pay(self, intent: PaymentIntent, attempt: PaymentAttempt) -> RailResult: ...
    async def lookup(self, rail_dedup_key: str) -> RailLookup: ...
    async def create_invoice(self, req: InvoiceRequest) -> Invoice: ...
    async def invoice_status(self, ref_hash: str) -> InvoiceStatus: ...
```
`RailCapabilities`: `settlement_finality {INSTANT, PROBABILISTIC, DEFERRED, BUSINESS_DAYS}`, `confirmation_depth_required`, `reversal_supported`, `reversal_window`, `dedup_guarantee {NONE, BY_RAIL_KEY, BY_PAYMENT_HASH}`, `max_inflight_window`, `capture_model {IMMEDIATE, AUTH_CAPTURE}`, `batch_semantics`, `fee_model`, `supported_actions`. Lightning: INSTANT · 0 · False · — · BY_PAYMENT_HASH · aus CLTV-Obergrenze · IMMEDIATE · none · routing_fee. (SEPA würde: BUSINESS_DAYS · 0 · True · 8 Wochen · BY_RAIL_KEY · … — nur als Nachweis, dass das Modell trägt.)

## 8. Reconciliation (beide Richtungen)

Vorwärts: jeder nicht-terminale Intent wird per `rail.lookup(rail_dedup_key)` gegen den Node geprüft; Abbildung `SUCCEEDED→SETTLED`, `FAILED→FAILED_FINAL` (mit `failure_reason`), `IN_FLIGHT/UNKNOWN→RECONCILIATION_REQUIRED` (bleibt, bis Evidenz). Rückwärts: Node-Zahlungen im Fenster ohne Intent → `RECONCILIATION_REQUIRED`-Record `orphan_settlement` + Alarm. `attention`-Status löst Telegram-Alarm über den bestehenden Health-Check-Pfad aus (nicht nur `OnFailure=`). Läuft im bestehenden `kai-ln-reconcile.timer` (`scripts/ln_reconcile.py` behält Name/Pfad; Rumpf ruft `payments.reconcile`).

## 9. Audit-Ereignisse (Mission §11)

`intent_created`, `policy_decided`, `approval_granted`, `approval_denied`, `submitted` (write-ahead), `rail_requested`, `rail_responded`, `settled`, `settlement_reversible`, `reversed`, `failed`, `retry_scheduled`, `reconciled`, `orphan_settlement`, `expired`, `cancelled`, `final`. Payloads redigiert (Allowlist wie `ops_ledger._redact_plan`): Hashes statt BOLT11/Pubkeys/Preimage-Klartext; Preimage als `proof_hash`. Keine Secrets, keine Macaroons.

## 10. API und Health

`POST /payments/intents` (Bearer + Idempotency-Key-Header) · `GET /payments/intents/{id}` · `POST /payments/intents/{id}/simulate` · `POST /payments/intents/{id}/execute` (Bearer + HOTP-Code, nur wenn `AUTHORIZED`/`AWAITING_APPROVAL`) · `POST /payments/invoices` · `GET /payments/invoices/{ref}` · `GET /payments/audit?intent_id=` · `GET /health/payment` (Rail-/Node-/Wallet-State ohne Geheimnisse, Modus, letztes Settlement, letzter Failure, Reconciliation-State, In-Flight, Policy-Reject-Count, Settlement-Latenz, Fees, Journal-Kettenstatus) · `GET /health/config` zeigt `payments`-Sektion. Alle Endpunkte auth-gated (S-001-Regel: auch lokal).

## 11. Security P0 (Vorbedingungen für SHADOW → LIVE)

Read-Scope auf `readonly.macaroon` (heute Invoice-Macaroon für Lesepfade) · Fee-Limit Pflicht · Sync-/Wallet-Gate im Kapital-Pfad · harter Tages-Cap · Boot-Guards (`validate_payment_boot`: LIVE nur mit `APP_ENV=production`, HOTP-Seed, Payment-Macaroon, Scope-Kollision `macaroon_path == invoice_macaroon_path` = Abbruch) · Journal-/HOTP-Dateien `0600` · Destination-Bindung im Journal · Rate-Limit auf `/payments/*` (bestehender Mechanismus) · Replay-Schutz via Idempotency-Key + HOTP-Counter.

## 12. Übergang und Rückbau

`ln_control` `pay_invoice` delegiert an `PaymentService` (kein zweiter Weg); Dual-Read 7 Tage (altes `ops_ledger` v2 bleibt lesbar, Reconciler prüft beide), danach DELETE-Kandidaten laut Architect §10 (~2,7k LOC: `value_layer`, `ops_ledger`, `reconciliation`, `ln_control`-Reste, `policy`, `ops_annotations`, `idempotency_store`, `control_gate`). Netto-Ziel ≈ 0 zusätzliche LOC nach Rückbau.

### Nachtrag 2026-09-04 — Rückbau vorgezogen, PR 1 „Der Altpfad schreibt nicht mehr“

**Warum vorgezogen.** Die 7-Tage-Frist sollte nicht Zeit vergehen lassen, sondern eine Frage beantworten: *gibt es einen offenen Alt-Vorgang, den nach dem Rückbau niemand mehr sieht?* Das ist eine Eigenschaft der Datei, nicht der Zeit — ein Lauf beantwortet sie abschließend. Vier Gates wurden am Gerät geprüft, bevor eine Zeile gelöscht wurde:

| Gate | Frage | Befund am Gerät (2026-09-04) |
|---|---|---|
| G-1 | Ist das Alt-Journal geschlossen? | `verify_ln_ops_ledger()` → `ok=true`, `open_intents=[]`, `errors=[]` |
| G-2 | Gibt es einen Doppelbefund? | `dual_conflicts=()` |
| G-3 | Steht die versiegelte Prä-Reg dem entgegen? | `0879a65c5fd01f65` verdict=**PASS**, Fenster abgelaufen 2026-08-15 |
| G-4 | Schreibt noch ein Alt-Writer? | Letzter Record 2026-08-05, älter als der Cutover |

Ohne G-1 und G-2 wäre der Rückbau kein Rückbau, sondern ein Wegsehen.

**Was PR 1 tut.** Der Gate-Stack (`policy`, `control_gate`, `idempotency_store`, `ops_annotations`, `ops_resolution`, `plan_guards`, `input_contract_rejections`, `ln_control_gates`) und die Sende-Hälfte (`value_layer`) sind gelöscht. `ops_ledger.py` ist auf ein **READ-ONLY-Archiv** geschrumpft: es kann einen Vorgang nur noch SCHLIESSEN (`append_ln_outcome` für den Crash-Gap-Reconciler), nicht mehr eröffnen — `_append_chained_record` verlangt strukturell einen vorhandenen `intent`-Record.

**Drei Zusagen sind VOR dem Löschen nachgebaut worden**, weil sie sonst ersatzlos verschwunden wären:

1. **Reserve-Boden** — Regel `reserve_floor` in `payments/policy.py` (§6) + `APP_PAYMENT_RESERVE_FLOOR_SAT`. ⚠ Der Boden ist erst wirksam, wenn der Rail eine Liquiditätszahl liefert; `PolicyContext.available_liquidity_sat` wird heute von keiner Produktionsstelle gesetzt, ein bewaffneter Boden lehnt deshalb JEDE Zahlung ab (fail-closed, wie der Bestand mit seinem 0-Fallback). Siehe Runbook.
2. **Truth-Anker des Geldes** — `payments/journal_chain.attest_payment_journal_tip` ersetzt `ops_ledger.attest_ln_ops_tip` in `scripts/truth_anchor_run.py`. Sonst wäre nach dem Rückbau keine Geldbewegung mehr on-chain verankert.
3. **Empfangspfad** — `create_invoice` ist nach `lightning/receive_gate.py` EXTRAHIERT, nicht migriert. `PaymentService.create_invoice` journalliert die Forderung und schöbe den anonymen `/oracle`-Mint hinter den exklusiven Geldjournal-Lock (BL-2); die Memo-Semantik `kai-oracle:{scope}` bleibt unverändert, weil `earnings_ledger` daran die Quelle zuordnet.

**Was PR 1 NICHT tut.** `lightning/reconciliation.py`, `payments/reconcile_dual.py` und `scripts/ln_reconciliation_eval.py` bleiben — sie sind PR 2 („Das Alt-Journal wird Archiv“). `artifacts/ln_ops_ledger_v2.jsonl` wird nicht angefasst und bleibt in `DEFAULT_SOURCES` und `MONEY_SOURCES` des Backups.

**Bewusst aufgegeben, nicht übersehen:** der Operator-Envelope aus `artifacts/ln_policy.json` (`allowed_actions`) fällt mit `lightning/policy.py`. Für `pay_invoice` war er schon vorher wirkungslos (die Delegation stand vor dem 403); für den kapitalfreien Cockpit-Mint bleiben `receive_enabled`, die Operator-Auth und die Plan-Hash-Bindung. `GET /dashboard/api/ln/ops` und sein Panel entfallen ersatzlos — sie zeigten ein Journal, das keinen Schreiber mehr hat.

## 13. Konsequenzen

Positiv: eine Wahrheit pro Geldbewegung, Doppelzahlung strukturell ausgeschlossen (Dedup + Unbekannt = Reconciliation), Agenten ohne Wallet-Zugriff, Fremd-Rails ohne Umbau anschließbar. Negativ: eine Übergangsphase mit zwei Journalen; SHADOW-Preview kostet read-only Node-Calls; LIVE bleibt in diesem Sprint aus.
