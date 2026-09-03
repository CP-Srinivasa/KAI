# Fiat-Bridge-Architektur — SEPA, Karten, PSP und Banken am Payment Control Plane

**Status:** **DESIGNED** · **Datum:** 2026-09-03 · **Baum:** `core/value-os-fiat-bridge-doc` @ `f18ded6c`
**Bezug:** ADR 0018 (Domänenmodell §3, State Machine §4, Rail-Interface §7), ADR 0016 (Self-Use-Test, Tier-2-STOP, Invarianten 1–5), ADR 0014 (Schicht 4 Intent Layer), Red-Team `redteam_payment_fabric.md` §3.
**Auftrag:** Mission §14/§15 — „Die Brücke zu SEPA, Karten, PSP und Banken wird architektonisch vollständig vorbereitet, aber noch nicht zu fünf halb fertigen Integrationen aufgeblasen."

> **Geltungsgrenze (bindend, vor jedem Lesen der Tabellen):**
> Dieses Dokument ist ein **Entwurf**, kein Zustandsbericht. Es existiert **kein** Fiat-Rail,
> **kein** PSP-Adapter, **kein** Bankkonto-Anschluss und **keine** Zeile Code dafür im Baum.
> Es beschreibt, **wie** eine Anbindung aussähe, damit sie später ohne Umbau des ADR-0018-Kerns
> möglich ist — und **was dafür zuerst entschieden** werden muss. Es ist **keine Freigabe**,
> **keine Rechtsberatung** und **kein Nachweis**, dass ein regulatorisches Problem gelöst wäre.

## 0. Klassifikation und Ist-Zustand

| Marke | Bedeutung |
|---|---|
| `IMPLEMENTED` | Code liegt im Baum |
| `TESTED` / `VERIFIED` | Code + Tests im Baum / zusätzlich gegen ein Live-System belegt |
| `DESIGNED` | in diesem oder einem ADR entworfen, **kein Code** |
| `DEFERRED` | bewusst verschoben, Bedingung benannt |
| `BLOCKED` | durch ADR/Governance/Recht gesperrt |

**Ehrlichkeitsanker — Ist-Zustand @ `f18ded6c`:**

| Gegenstand | Pfad | Klassifikation | Beleg |
|---|---|---|---|
| Payment Control Plane | `app/payments/` | `DESIGNED` | ADR 0018 §2 — Verzeichnis existiert im Baum **nicht** |
| Lightning-Rail-Adapter | `app/payments/rails/lightning.py` | `DESIGNED` | ADR 0018 §2 |
| LN-Client/Adapter (vor Control Plane) | `app/lightning/client.py`, `adapter.py` | `IMPLEMENTED` | im Baum vorhanden |
| LN-Reconciliation (outcome-only) | `app/lightning/reconciliation.py`, `scripts/ln_reconcile.py`, `kai-ln-reconcile.timer` | `IMPLEMENTED` | `docs/CODEMAP.md:85` |
| Provenance-/SoF-Export | `app/compliance/provenance.py` | `IMPLEMENTED` | ADR 0014 §2 Schicht 6 |
| Dritt-Gate (fail-closed) | `app/governance/third_party_gate.py` | `IMPLEMENTED` | ADR 0014 §2 Schicht 6 |
| Kapital-Buckets / Reserve-Floor | `app/capital/reserve_policy.py`, `segmentation.py` | `IMPLEMENTED` (inert) | ADR 0014 §2 Schicht 7 |
| Fiat-Rails (SEPA/Karte/PSP/Bank) | — | `BLOCKED` | ADR 0018 §1 „Fremd-Rails": kein Modul, kein Stub |
| Verwahrung fremder Gelder | — | `BLOCKED` (dauerhaft) | ADR 0016 Invariante 2, §Was gesperrt bleibt |
| KYT/AML/Sanktions-Prüfung | — | `BLOCKED` | ADR 0014 §2 Schicht 6: „STOP-Schild, kein baubares Modul" |

---

## 1. Die zwei Flüsse

Grundregel beider Flüsse: **ein Intent je Rail-Hop**, verbunden über `correlation_id`.
Es gibt **keinen** rail-übergreifenden Intent. Der Grund ist nicht Ästhetik: `SETTLED`,
`SETTLED_REVERSIBLE` und `REVERSED` sind **rail-spezifisch**; ein Intent mit zwei
Finalitätsbegriffen wäre in der Hälfte der Fälle gelogen (Red-Team §3, Zeile „Keine Reversals").

### 1.1 Fluss A — FIAT IN → CONFIRMED FUNDS → FX/QUOTE → LIGHTNING SETTLEMENT

```
FLUSS A (DESIGNED)                                   correlation_id = C

  [Zahler = Operator selbst / eigene Rechnung]
        |
        | (1) Fiat-Gutschrift                Rail: SEPA | SEPA_INSTANT | CARD_PSP
        v
  +-----------------------------------+
  | HOP 1  RECEIVABLE (Einlauf)       |  Invoice(rail=SEPA, ref_hash=<E2E/Verwendungszweck>)
  |  Invoice -> Settlement            |  Settlement(finality=BUSINESS_DAYS,
  |  Status: SETTLED_REVERSIBLE       |             proof=PROVIDER_REF, settled_at=Valuta)
  +-----------------------------------+  reversal_window: SEPA-DD 8 Wochen
        |
        |  GATE  cross_rail_finality_gate  (DESIGNED Policy-Regel, fail-closed)
        |  DENY, solange reversal_window offen -- ausser der Auszahlungshop wird
        |  ausdruecklich aus EIGENMITTELN vorfinanziert (Self-Use, Risiko beim Operator).
        |  Fremdgeld wird NIE vorfinanziert (ADR 0016 Inv. 2).
        v
  +-----------------------------------+
  | HOP 2  QUOTE (kein Intent)        |  Quote(rail=LIGHTNING,
  |  FX + Fee, TTL-gebunden           |        amount_in=Money(EUR), amount_out=Money(BTC),
  |                                   |        rate_ref=ExchangeRateReference(source,rate,ts),
  |                                   |        fee_estimate, ttl)
  +-----------------------------------+  TTL abgelaufen -> NEU quoten, niemals senden
        |
        v
  +-----------------------------------+
  | HOP 3  PaymentIntent (Auslauf)    |  PaymentIntent(rail=LIGHTNING, correlation_id=C,
  |  REQUESTED -> AUTHORIZED ->       |                idempotency_key, fee_limit, expiry)
  |  SUBMITTED -> SETTLED             |  Settlement(finality=INSTANT, proof=PREIMAGE)
  +-----------------------------------+  Unbekannt/Timeout -> RECONCILIATION_REQUIRED, nie FAILED
        |
        v
  [Journal-Kette: intent_created ... settled ... final]  ->  Accounting-Projektion
```

| # | Schritt | Objekt (ADR 0018 §3) | Zustand (§4) | Trägt das Modell? | Klassifikation |
|---|---|---|---|---|---|
| A1 | Fiat-Einlauf erkannt | `Invoice` + `Settlement` | — → `SETTLED_REVERSIBLE` | ja, unverändert | `DESIGNED` |
| A2 | Reversal-Fenster läuft | `RailCapabilities.reversal_window` | bleibt `SETTLED_REVERSIBLE` | ja | `DESIGNED` |
| A3 | Fenster abgelaufen | Reconciler mit Bank-Evidenz | → `SETTLED` | ja | `DESIGNED` |
| A4 | Rücklastschrift im Fenster | Reconciler mit Bank-Evidenz | → `REVERSED` | ja | `DESIGNED` |
| A5 | Rücklastschrift **nach** Fenster | — | nicht abbildbar | **nein** → Lücke **G-2** | `DESIGNED` (Zusatz) |
| A6 | FX/Quote | `Quote` + `ExchangeRateReference` | kein Zustand | nur mit `amount_in`/`amount_out` | `DESIGNED` (Feld) |
| A7 | LN-Auszahlung | `PaymentIntent` + `PaymentAttempt` | `REQUESTED`…`SETTLED` | ja, v0.1-Pfad | `DESIGNED` |
| A8 | Kettenverknüpfung | `correlation_id` | — | ja für Intents; `Invoice` braucht das Feld | `DESIGNED` (Feld) |

### 1.2 Fluss B — LIGHTNING IN → SETTLEMENT → FX/QUOTE → FIAT PAYOUT

```
FLUSS B (DESIGNED)                                   correlation_id = C2

  [LN-Zahler]
        |
        | (1) BOLT11-Invoice bezahlt
        v
  +-----------------------------------+
  | HOP 1  RECEIVABLE (Einlauf)       |  Invoice(rail=LIGHTNING, ref_hash=payment_hash)
  |  Status: SETTLED (sofort final)   |  Settlement(finality=INSTANT, proof=PREIMAGE)
  +-----------------------------------+  reversal_supported=False -> nie REVERSED
        |
        |  GATE  cross_rail_finality_gate  -> ALLOW (Einlauf ist final)
        |  GATE  compliance_gate           -> Schnittstelle DESIGNED, Inhalt NICHT geloest
        v
  +-----------------------------------+
  | HOP 2  QUOTE (kein Intent)        |  Quote(amount_in=Money(BTC), amount_out=Money(EUR),
  |  FX + Slippage + TTL              |        rate_ref, ttl, max_slippage_bps)
  +-----------------------------------+
        |
        v
  +-----------------------------------+
  | HOP 3  PaymentIntent (Auslauf)    |  PaymentIntent(rail=SEPA|BANK_ACCOUNT,
  |  REQUESTED -> AUTHORIZED ->       |                correlation_id=C2, mandate_ref?  -> G-3)
  |  SUBMITTED -> SETTLED_REVERSIBLE  |  Settlement(finality=BUSINESS_DAYS, proof=PROVIDER_REF)
  |    -> SETTLED   ODER   -> REVERSED (R-Transaction / Rueckleitung)
  +-----------------------------------+
        |
        v
  [T+1 Reconciliation gegen Kontoauszug: CAMT.053 / Bank-Read-only]
```

| # | Schritt | Objekt | Zustand | Trägt das Modell? | Klassifikation |
|---|---|---|---|---|---|
| B1 | LN-Einlauf | `Invoice` + `Settlement` | → `SETTLED` | ja | `DESIGNED` (Rail v0.1) |
| B2 | Compliance-Gate | Policy-Regel `compliance_gate` | `DENY`/`REQUIRES_APPROVAL` | Schnittstelle ja, Inhalt nein | `BLOCKED` |
| B3 | FX/Quote mit Slippage | `Quote` | kein Zustand | Feld `max_slippage_bps` fehlt | `DESIGNED` (Feld) |
| B4 | Fiat-Auszahlung eingereicht | `PaymentIntent`, `PaymentAttempt` | → `SUBMITTED` | ja | `DESIGNED` |
| B5 | Valuta gebucht | Reconciler (Kontoauszug) | → `SETTLED_REVERSIBLE` | ja | `DESIGNED` |
| B6 | Rückleitung/Return | Reconciler | → `REVERSED` | ja | `DESIGNED` |
| B7 | Sammelbuchung (n Payouts, 1 Buchung) | — | nicht abbildbar | **nein** → Lücke **G-1** | `DESIGNED` (Zusatz) |
| B8 | Lastschrift-Mandat als Autorisierung | — | nicht abbildbar | **nein** → Lücke **G-3** | `DESIGNED` (Zusatz) |

### 1.3 Onchain-Variante (Einlauf mit `confirmation_depth`)

```
  Onchain-Einlauf (DESIGNED)
  tx gesehen (0 conf)                       ->  IN_FLIGHT
  conf < confirmation_depth_required (6)    ->  SETTLED_REVERSIBLE
       (hier KEIN Rail-Reversal, sondern offene Finalitaetsbedingung / Reorg-Risiko;
        reversal_window := erwartete Zeit bis zur geforderten Tiefe)
  conf >= 6                                 ->  SETTLED (proof=TXID)
  Reorg NACH SETTLED                        ->  weder REVERSED noch FAILED_FINAL -> Luecke G-2
```

`SETTLED_REVERSIBLE` trägt damit **zwei** Semantiken: Rail-Reversal (SEPA/Karte) und offene
Finalitätsbedingung (Onchain). Das ist zulässig, weil beide dieselbe operative Regel erzeugen —
*„gebucht, aber nicht terminal"* —, muss aber über ein Feld `finality_reason`
(`REVERSAL_WINDOW | CONFIRMATION_DEPTH`) unterschieden werden, sonst ist der Reconciler nicht
entscheidbar. `DESIGNED` (Feld-Ergänzung, kein Zustandszusatz).

---

## 2. Trennung der Verantwortungen

Neun Domänen, je genau **eine** Owner-Komponente. Wo keine existiert, steht `DESIGNED` oder
`BLOCKED` — nicht „geplant" als Beschönigung.

| Domäne | Verantwortung (genau) | Owner-Komponente | Schnittstelle | Klassifikation |
|---|---|---|---|---|
| **Payment Orchestration** | Intent-Lebenszyklus, Serialisierung, Policy-Aufruf, Zustandsvergabe; kennt **keinen** Rail im Detail | `app/payments/service.py` + `status.py` (einzige Vergabestelle, ADR 0018 §4) | `POST /payments/intents`, `execute`, `simulate` | `DESIGNED` |
| **Custody (LN/Onchain)** | Schlüsselbesitz und Signatur — ausschließlich LND, nie KAI, nie ein Agent | LND-Node + `app/lightning/client.py` | gRPC/REST mit getrennten Macaroons (ADR 0018 §11) | `IMPLEMENTED` |
| **Custody (Fiat)** | Halten von Geld auf einem Konto | **regulierter Dritter oder eigene Bank — niemals KAI** | keine | `BLOCKED` (dauerhaft, ADR 0016 Inv. 2) |
| **Custody (fremde Mittel)** | Geld Dritter halten, weiterleiten, treuhänderisch verwalten | — | keine | `BLOCKED` (Tier-2-STOP) |
| **Liquidity (LN)** | Kanal-Kapazität, Reserve-Floor, Reservierung offener Intents | `app/lightning/treasury.py`, `app/capital/reserve_policy.py` | Policy-Regel `liquidity` (ADR 0018 §6) | `IMPLEMENTED` (inert) |
| **Liquidity (Fiat)** | Kontodeckung, reservierte Beträge für offene Payouts | keine — Bankkonto des Operators, manuell | Reservierung als Journal-Zähler | `DEFERRED` (Welle 2) |
| **FX** | Kurs-Quelle, Kursbindung, TTL, Slippage-Grenze, Kurs-Beweis im Journal | `app/payments/fx.py` | `Quote` + `ExchangeRateReference(source, rate, ts)`; abgelaufene Quote = `DENY` | `DESIGNED` |
| **Settlement** | Finalitätsentscheid je Rail; **nur** mit Rail-Evidenz | `app/payments/rails/*` + `status.transition` | `RailResult`, `RailLookup`, `Settlement.proof` | `DESIGNED` (LN-Rail v0.1) |
| **Banking** | Konto, Mandate, Referenzen, Kontoauszug | keine — Welle 2 `rails/bank_read.py` (read-only) | CAMT.053-Import oder lizenzierter AIS-Anbieter | `DESIGNED` / `DEFERRED` |
| **Compliance** | KYT, AML, Sanktionslisten, Travel Rule | **kein Modul** — nur Gate-Schnittstelle | Policy-Regel `compliance_gate` → `ALLOW/DENY/REQUIRES_APPROVAL`, Ergebnis mit `rule_ids` ins Journal | Schnittstelle `DESIGNED`, Inhalt `BLOCKED` |
| **Accounting** | Ableitung der Buchung aus dem Journal, keine zweite Wahrheit | `app/payments/journal.py` (Quelle) → Projektion; heute `app/lightning/earnings_ledger.py`, `earnings_booking.py` | Projektion ist **idempotent über `intent_id`**; Journal ist append-only, Buchung wird nie zurückgeschrieben | Journal `DESIGNED`, LN-Booking `IMPLEMENTED` |
| **Reconciliation** | Abgleich Rail-/Bank-Evidenz gegen Journal, beide Richtungen, T+1-Fenster für Fiat | `app/payments/reconcile.py`; heute `app/lightning/reconciliation.py` + `scripts/ln_reconcile.py` + `kai-ln-reconcile.timer` | `rail.lookup()` vorwärts; Statement→Journal rückwärts, Waise = `orphan_settlement` + Alarm | LN `IMPLEMENTED`, Fiat `DESIGNED` |

**Drei Regeln, die aus dieser Trennung folgen und nicht verhandelbar sind:**

1. **Keine Doppelbuchung.** Das Payment-Journal ist die einzige Wahrheit je Geldbewegung; jede
   Buchhaltungszeile ist eine *Projektion* daraus, idempotent über `intent_id` und
   wiederholbar berechenbar. Eine Korrektur ist ein **neuer** referenzierender Record, nie eine
   Änderung (ADR 0016 Invariante 1). `DESIGNED`
2. **Keine improvisierte Verwahrung.** Es gibt keinen Zustand, in dem KAI Geld hält, das ihm
   nicht gehört — auch nicht „kurz zwischen zwei Hops". Wo ein Fluss das erzwingen würde, wird
   der Fluss nicht gebaut, nicht die Verwahrung. `BLOCKED`
3. **Compliance ist ein Gate, kein Feature.** `compliance_gate` liefert im Entwurf ohne
   angeschlossenen Dienst konstant `DENY` (fail-closed). Ein `ALLOW` ohne Dienst wäre die
   gefährlichste Zeile dieses Dokuments. `DESIGNED` / `BLOCKED`

---

## 3. RailCapabilities-Matrix

Felder wie in ADR 0018 §7 (Herkunft: Red-Team §3 „Konkrete Feldvorschläge"). Die Werte sind
**Entwurfsannahmen** für einen späteren Adapter, keine gemessenen Eigenschaften eines
angeschlossenen Dienstes.

### 3.1 Finalität, Umkehrbarkeit, Dedup

| Rail | `settlement_finality` | `confirmation_depth_required` | `reversal_supported` | `reversal_window` | `dedup_guarantee` | `max_inflight_window` | Klassifikation |
|---|---|---|---|---|---|---|---|
| LIGHTNING | `INSTANT` | 0 | nein | — | `BY_PAYMENT_HASH` | aus CLTV-Obergrenze | `DESIGNED` (v0.1-Ziel `IMPLEMENTED`) |
| BITCOIN ONCHAIN | `PROBABILISTIC` | 6 | nein (Reorg ≠ Reversal) | — | `BY_RAIL_KEY` (txid) | ~24 h (Fee-Bump/RBF) | `DEFERRED` (ADR 0018 §1 DENY `unsupported_action`) |
| SEPA (SCT/SDD) | `BUSINESS_DAYS` | 0 | ja | 8 Wochen (SDD); 13 Monate unautorisiert | `BY_RAIL_KEY` (EndToEndId) | T+2 | `BLOCKED` (eigenes ADR nötig) |
| SEPA INSTANT | `INSTANT` (bankseitig final) | 0 | nein (nur Recall-Bitte) | Recall ohne Anspruch | `BY_RAIL_KEY` | 20 s Timeout, dann unbekannt | `BLOCKED` |
| BANK ACCOUNT (Payout) | `BUSINESS_DAYS` | 0 | ja (Return) | Rückleitungsfrist des Instituts | `BY_RAIL_KEY` (Auftragsref.) | T+2 | `BLOCKED` |
| CARD/PSP (Auth/Capture) | `DEFERRED` | 0 | ja | Chargeback 120+ Tage | `BY_RAIL_KEY` (Idempotency-Key des PSP) | Auth-Hold 7–30 Tage | `BLOCKED` |
| MERCHANT PSP (Acquiring) | `DEFERRED` | 0 | ja | Chargeback + Rolling Reserve | `BY_RAIL_KEY` | Auszahlungszyklus | `BLOCKED` (Tier-2-STOP, ADR 0016) |
| STABLECOIN/BLOCKCHAIN | `PROBABILISTIC` | kettenabhängig | nein (aber Freeze durch Emittent) | — | `BY_RAIL_KEY` (txhash) | kettenabhängig | `BLOCKED` (ADR 0016 Inv. 3: Souveränität zuerst) |

### 3.2 Capture, Batch, Gebühren, Aktionen, Beweis, Reconciliation

| Rail | `capture_model` | `batch_semantics` | `fee_model` | `supported_actions` | Proof-Typ | Reconciliation-Mechanismus | Klassifikation |
|---|---|---|---|---|---|---|---|
| LIGHTNING | `IMMEDIATE` | `PER_ITEM` | `PREPAID_LIMIT` (routing_fee) | `pay_invoice`, `create_invoice`, `invoice_status`, `lookup` | `PREIMAGE` | `lookup(payment_hash)` gegen Node, beide Richtungen | `DESIGNED` |
| BITCOIN ONCHAIN | `IMMEDIATE` | `BATCH_ATOMIC` (mehrere Outputs, 1 tx) | `POST_SETTLEMENT` (RBF ändert Fee nachträglich) | `send_coins`, `watch_address` | `TXID` + Tiefe | Block-Explorer/eigener Node, Tiefe je Poll | `DEFERRED` |
| SEPA (SCT/SDD) | `IMMEDIATE` | `BATCH_PARTIAL` (Sammler, Einzelrückgabe) | `NEGOTIATED` (Entgelt je Posten) | `credit_transfer`, `direct_debit`, `return` | `PROVIDER_REF` (Buchungsref.) | Kontoauszug CAMT.053, T+1 | `BLOCKED` |
| SEPA INSTANT | `IMMEDIATE` | `PER_ITEM` | `NEGOTIATED` | `credit_transfer` | `PROVIDER_REF` | Kontoauszug + Sofortstatus | `BLOCKED` |
| BANK ACCOUNT (Payout) | `IMMEDIATE` | `BATCH_PARTIAL` | `NEGOTIATED` | `payout`, `statement_read` | `PROVIDER_REF` | Kontoauszug T+1, Betrag+Referenz | `BLOCKED` |
| CARD/PSP | `AUTH_CAPTURE` | `BATCH_PARTIAL` (Settlement-Batch) | `POST_SETTLEMENT` (Disagio erst im Payout) | `authorize`, `capture`, `void`, `refund` | `PROVIDER_REF` (+ Network-Tx-ID) | PSP-Settlement-Report gegen Bank-Gutschrift | `BLOCKED` |
| MERCHANT PSP | `AUTH_CAPTURE` | `BATCH_PARTIAL` | `POST_SETTLEMENT` | wie oben + `payout`, `chargeback` | `PROVIDER_REF` | Payout-Report n:1 gegen Journal | `BLOCKED` |
| STABLECOIN | `IMMEDIATE` | `PER_ITEM` | `POST_SETTLEMENT` (Gas) | `transfer` | `TXID` | Chain-Indexer | `BLOCKED` |

### 3.3 Was das ADR-0018-Modell trägt — und die drei Stellen, an denen es nicht reicht

**Trägt ohne Umbau:** Alle acht Rails oben lassen sich über `PaymentIntent → Policy →
Authorization → Attempt → Settlement → Reconciliation` abbilden. Die vier getrennten Beträge
(`amount_requested`/`amount_sent`/`amount_settled`/`fee_actual`) decken `POST_SETTLEMENT`-Gebühren
ab. `SETTLED_REVERSIBLE`/`REVERSED` decken SEPA-Rücklastschrift und Karten-Refund **im Fenster**.
`dedup_guarantee` + `rail_dedup_key` decken PSP-Idempotenz. `capture_model=AUTH_CAPTURE` wird durch
zwei `PaymentAttempt`-Einträge unter demselben Intent abgebildet. `confirmation_depth_required`
steuert den Übergang `IN_FLIGHT → SETTLED_REVERSIBLE → SETTLED`. — Klassifikation aller Aussagen
dieses Absatzes: `DESIGNED` (analytisch geprüft, nicht implementiert, nicht getestet).

**Es reicht an drei Stellen nicht:**

| ID | Lücke | Warum das Modell bricht | Minimaler Zusatz | Zustandsmaschine ändert sich? | Klassifikation |
|---|---|---|---|---|---|
| **G-1** | **Batch-Settlement mit n:1-Zuordnung** | `Settlement` hängt 1:1 am Intent. Ein PSP-Payout oder eine SEPA-Sammelbuchung deckt *n* Intents mit **einem** Bankbeleg und **einem** Nettobetrag (Brutto − Gebühren − Rückbelastungen). Der Reconciler kann Beleg und Intent nicht zuordnen. | `SettlementGroup(group_id, rail, external_ref_hash, amount_gross, amount_net, fee_total, member_intent_ids[], allocation_method)` + optionales `Settlement.group_ref`. Zuordnung wird **berechnet und im Journal festgeschrieben**, nicht geraten. | **nein** — jeder Intent durchläuft weiterhin einzeln `SETTLED`; die Gruppe ist nur der Beweisträger | `DESIGNED` |
| **G-2** | **Reversal nach Terminalzustand** (Chargeback nach Monaten, SEPA unautorisiert 13 Monate, Onchain-Reorg nach Tiefe) | `SETTLED` ist terminal (ADR 0018 §4). `SETTLED_REVERSIBLE → REVERSED` gilt nur im *bekannten* Fenster. Danach gibt es keine Kante — und es darf auch keine geben, sonst ist „terminal" wertlos und Invariante 1 (append-only, keine Rückschreibung) verletzt. | **Kein neuer Zustand, kein Rückwärts-Übergang.** Stattdessen ein **Kompensations-Intent**: `PaymentIntent(kind=COMPENSATION, compensates=<intent_id>, correlation_id=<original>)` mit eigener Policy und eigenem Lebenszyklus. Zusätzlich `RailCapabilities.reversal_window_max` (harte Obergrenze) getrennt vom Normalfenster. | **nein** — der Originalintent bleibt terminal; die Umkehr ist eine **eigene** Geldbewegung mit eigenem Beweis | `DESIGNED` |
| **G-3** | **Langlebiges Autorisierungsartefakt** (SEPA-Mandat mit UMR/Gläubiger-ID, Karten-Token/Network-Transaction-ID, PSP-Kundenreferenz) | ADR 0018 kennt nur `Counterparty.ref_hash` **pro Intent**. Ein Mandat überdauert viele Intents, hat eigene Zustände (aktiv/ausgesetzt/widerrufen/verfallen) und ist bei Missbrauch die haftungsrelevante Tatsache. In `Counterparty` versteckt, ist es weder prüfbar noch widerrufbar. | `PaymentMandate(mandate_id, rail, scheme_ref_hash, debtor_hash, creditor_id_hash, valid_from, valid_until, revoked_at, status)` als eigenes Journal-Objekt + `PaymentIntent.mandate_ref`. Policy-Regel `mandate_valid` vor `destination_allowlist`. | **nein** für den Intent; das Mandat bekommt eine **eigene**, sehr kleine Zustandsmaschine | `DESIGNED` |

**Kein Modellfehler, aber ein Benennungsrisiko:** `AUTHORIZED` bedeutet in ADR 0018 §4
*„KAI-Policy hat freigegeben"*. Bei Karte bedeutet *Authorization* *„der Emittent hat den Betrag
reserviert"* — ein Rail-Ereignis nach `SUBMITTED`. Wird das nicht getrennt, liest sich ein
Karten-Intent falsch. Auflösung ohne neuen Zustand: `PaymentAttempt.hold_ref` +
`hold_expires_at`; der Rail-Hold ist ein Attempt-Attribut, kein Intent-Zustand. `DESIGNED`

---

## 4. Nicht-custodiale Pfade (ohne Fremdgeld, ohne eigene Lizenzpflicht)

Auswahlkriterium: der Pfad ist **nur dann** hier gelistet, wenn KAI zu **keinem** Zeitpunkt
Geld hält, das ihm nicht gehört, und **keine** Zahlung für einen Dritten auslöst. Jeder Pfad
erfüllt zusätzlich den Self-Use-Test (ADR 0016 §Test 1–3) — sonst ist er Tier 2.

| ID | Pfad | Was KAI tut | Was KAI **nicht** tut | Klassifikation |
|---|---|---|---|---|
| **P-1** | Eigene Fiat-Rechnung über einen regulierten Anbieter mit Lightning-Settlement | zahlt eine **LN-Invoice** des Anbieters; der Anbieter begleicht die auf den Operator lautende Rechnung in Fiat | kein Fiat-Konto, kein Fiat-Leg, keine Verwahrung, kein Dritt-Nutzer | `DESIGNED` (frühestens Welle 3) |
| **P-2** | Submarine Swap (LN ↔ Onchain, HTLC-basiert, non-custodial) | tauscht **eigene** Mittel zwischen eigenen Wallets; Refund-Pfad bleibt bei KAI | kein Custodial-Swap, kein Fiat, kein Tausch für Dritte | `DESIGNED` (Welle-1-Kandidat) |
| **P-3** | Bank-Schnittstelle **nur lesend** für Reconciliation | importiert Kontoauszug (CAMT.053-Datei oder lizenzierter AIS-Anbieter) des **eigenen** Kontos | keine Zahlungsauslösung, keine Schreib-Scopes, keine Fremdkonten | `DESIGNED` (Welle 2) |
| **P-4** | Manuelle Fiat-Leg mit Beleg-Erfassung | Operator zahlt selbst in seiner Banking-App; KAI erfasst `PROVIDER_REF` + Beleg-Hash und schließt den Intent per Reconciliation | keine API zur Bank, keine Automatik im Geldpfad | `DESIGNED` (kleinste Variante, Welle 2) |
| **P-5** | Karten-Acquiring, Merchant-PSP, Stablecoin-Ramp mit Kundenwallet | — | **wird nicht gebaut** | `BLOCKED` (Tier-2-STOP) |

**P-1 — Voraussetzungen und Risiko.** Voraussetzung: Anbieter nachweislich reguliert; Rechnung
lautet auf den Operator; Kleinbetrag; kein Onboarding Dritter; `compliance_gate`-Ergebnis im
Journal. Risiko: zwischen LN-Settlement (`INSTANT`, unumkehrbar) und Fiat-Ausführung
(`BUSINESS_DAYS`) besteht eine **ungesicherte Forderung** gegen den Anbieter — Ausfall bedeutet
Totalverlust ohne Rückholpfad. Im Modell ist das kein `SETTLED`-Problem, sondern ein
**Gegenparteirisiko ohne eigenes Objekt**; es wird über `Counterparty` + Betragsgrenze in der
Policy begrenzt, nicht wegdefiniert. Nicht gebaut wird: jede Form von Guthabenkonto beim
Anbieter, jede Wiederverwendung des Pfads für andere Personen. `DESIGNED`

**P-2 — Voraussetzungen und Risiko.** Voraussetzung: Anbieter mit echtem HTLC-Refundpfad; Timeout-
und Refund-Behandlung als **eigener** Intent (`kind=COMPENSATION`, G-2), nicht als
`FAILED_FINAL`; Onchain-Gebührenbudget. Risiko: der Refund-Pfad ist der am seltensten getestete
Pfad und genau der, der im Fehlerfall zählt; `confirmation_depth_required` verlängert das
Zeitfenster, in dem Kapital gebunden ist. Nicht gebaut wird: Swap-Vermittlung für Dritte.
`DESIGNED` / Bau `DEFERRED` bis Onchain-Rail entschieden ist.

**P-3 — Voraussetzungen und Risiko.** Voraussetzung: ausschließlich Lese-Scopes; getrennte
Credentials mit `0600`; Kontodaten im Journal **nur als Hash** plus Betrag/Valuta/Referenz;
Aufbewahrung nach demselben Append-only-Prinzip. Risiko: (a) Scope-Creep in Richtung
Zahlungsauslösung — deshalb Boot-Guard analog ADR 0018 §11 (Prozessabbruch, wenn ein
Schreib-Scope gesetzt ist); (b) personenbezogene Daten Dritter aus Gegenbuchungen — Redaktion
im Writer ist Pflicht, nicht Option. Nicht gebaut wird: jede Form von Zahlungsauslösung über die
Bank-API. `DESIGNED`

**P-4 — Voraussetzungen und Risiko.** Voraussetzung: keine. Risiko: Mensch-im-Pfad, damit
Latenz und Tippfehler; dafür null Angriffsfläche und null Lizenzberührung. Dieser Pfad ist der
ehrlichste erste Schritt: er beweist die **Zuordnungslogik** (G-1) an echten Belegen, bevor
irgendeine Integration existiert. `DESIGNED`

---

## 5. Governance

### 5.1 Was gesperrt bleibt (ADR 0016, unverändert)

| Gegenstand | Status | Quelle |
|---|---|---|
| Merchant-/POS-Betrieb für Dritte | `BLOCKED` — Tier-2-STOP | ADR 0016 §Was gesperrt bleibt |
| PSP-/Acquiring-Betrieb | `BLOCKED` — Tier-2-STOP | ADR 0016; ADR 0014 §2 Schicht 5 |
| Escrow mit Fremdgeldern | `BLOCKED` — Tier-2-STOP | ADR 0016; ADR 0014 §2 Schicht 5 |
| Intent-Ausführung für Dritte | `BLOCKED` — Tier-2-STOP | ADR 0016 |
| Verwahrung fremder Mittel (Fiat oder Krypto) | `BLOCKED` (dauerhaft) | ADR 0016 Invariante 2 |
| Marktplatz / RWA / Tokenisierung | `BLOCKED` — Tier-2-STOP | ADR 0014 §2 Schicht 5 |
| „schon mal auf Testnet" als Umgehung | `BLOCKED` | ADR 0016 §Was gesperrt bleibt, wörtlich |

Ergänzend gilt ADR 0016 §„Dogfood ist keine Nachfrage": eine funktionierende Fiat-Bridge im
Eigengebrauch ist **kein** Nachfragesignal und **keine** Begründung für den nächsten Rail.

### 5.2 Welches ADR jeder Fremd-Rail bräuchte

| Rail | Eigenes ADR nötig? | Was dieses ADR beantworten müsste | Klassifikation |
|---|---|---|---|
| BITCOIN ONCHAIN (Self-Use) | ja | Dedup ohne `payment_hash`, RBF/Fee-Nachträglichkeit, Reorg-Behandlung (G-2), Tiefe als Policy-Parameter | `DEFERRED` |
| BANK READ-ONLY (P-3/P-4) | ja, klein | Scope-Beweis „nur lesen", PII-Redaktion, Aufbewahrung, Boot-Guard | `DESIGNED` |
| SEPA (Auslauf, Self-Use) | ja | Rechtsträger, Kontoinhaberschaft, Mandat (G-3), Batch-Zuordnung (G-1), Compliance-Dienst, Kapitalgrenze | `BLOCKED` |
| SEPA INSTANT | ja | zusätzlich: 20-s-Timeout-Semantik, Recall ohne Anspruch | `BLOCKED` |
| CARD/PSP | ja | Auth/Capture-Trennung, Chargeback-Kompensation (G-2), PCI-Berührung, Vertragsverhältnis | `BLOCKED` |
| MERCHANT PSP | **nein — Tier-2-STOP**, ADR würde die Grenze verschieben, nicht klären | — | `BLOCKED` |
| STABLECOIN/CHAIN | ja | Kollision mit ADR 0016 Invariante 3 (Souveränität zuerst) begründen | `BLOCKED` |

### 5.3 Ausbau-Reihenfolge

| Welle | Inhalt | Vorbedingung (fail-closed) | Klassifikation |
|---|---|---|---|
| **Welle 0** | ADR-0018-v0.1: Control Plane + Lightning-Rail, `SIMULATION`/`SHADOW`, Journal, Policy, Reconciliation | keine — bindende Bauvorgabe | `DESIGNED` (Bau offen) |
| **Welle 1** | Onchain-Rail **nur Self-Use** (eigene Wallet zu eigener Wallet), P-2 Submarine Swap | Welle 0 mit Reconciliation-Beweis; eigenes ADR; Dedup-Design ohne `payment_hash` | `DEFERRED` |
| **Welle 2** | Bank-**Read-only**-Reconciliation (P-3/P-4), G-1-Zuordnungslogik an echten Belegen | Welle 1 abgeschlossen; kleines ADR; Beweis „keine Schreib-Scopes" | `DEFERRED` |
| **Welle 3** | **erster** Fiat-Rail (Auslauf, Self-Use) | eigenes ADR **und** angeschlossener Compliance-Dienst **und** Rechtsprüfung **und** Operator-Go mit Kapitalgrenze | `BLOCKED` |

Die Reihenfolge ist nicht parallelisierbar: jede Welle liefert die Evidenz, ohne die die
nächste nur Vermutung wäre — Mission §15 ist genau diese Regel.

---

## 6. Regulatorische Ehrlichkeit

**Keine Rechtsberatung.** Der folgende Abschnitt benennt, welche Tätigkeiten in DE/EU
**typischerweise** erlaubnis- oder registrierungspflichtig sind, damit die Architektur nicht
versehentlich hineinläuft. Die Einordnung im Einzelfall macht eine Fachperson, nicht dieses
Dokument und nicht KAI.

| Tätigkeit (typisierend) | Typische Einordnung | Verhält sich KAI-Self-Use dazu wie? | Klassifikation |
|---|---|---|---|
| Geld für Dritte entgegennehmen und weiterleiten | Zahlungsdienst / Finanztransfergeschäft | wird **nicht** getan — nur eigene Mittel | `BLOCKED` |
| Zahlungen im Auftrag Dritter auslösen | Zahlungsauslösedienst | wird **nicht** getan | `BLOCKED` |
| Kontoinformationen Dritter abrufen | Kontoinformationsdienst | nur **eigenes** Konto, nur lesend (P-3) — die Abgrenzung ist zu prüfen, siehe offene Frage 3 | `DESIGNED` |
| Kryptowerte für andere verwahren | Kryptoverwahrgeschäft / CASP-Verwahrung | wird **nicht** getan — Self-Custody, eigener Node | `BLOCKED` |
| Kryptowerte für andere tauschen | CASP-Tausch/Handel | wird **nicht** getan | `BLOCKED` |
| E-Geld ausgeben / Guthabenkonten führen | E-Geld-Geschäft | wird **nicht** getan, auch nicht „intern als Bilanzposten" | `BLOCKED` |
| Eigene Schlüssel für eigene Mittel halten | in der Regel nicht erlaubnispflichtig | genau das ist der Ist-Zustand | `IMPLEMENTED` |
| Eigene Rechnung über einen regulierten Anbieter zahlen | Anbieter ist der Regulierte, nicht der Zahler | P-1 | `DESIGNED` |

**Die architektonische Konsequenz in einem Satz:** Jede Stelle im Entwurf, an der KAI zum
Halter oder Weiterleiter fremden Geldes würde, ist nicht durch eine Kontrolle abgesichert,
sondern **nicht vorgesehen** — es gibt keinen Zustand, keinen Endpunkt und kein Objekt dafür.

### 6.1 Offene Fragen (unbeantwortet, nicht wegdefiniert)

1. Ab welchem Punkt wird agentisches Bezahlen (Agent erzeugt Intent, KAI zahlt) rechtlich als
   Zahlung „für einen Dritten" gewertet, wenn der Agent formal Software des Operators ist?
2. Ist der Operator bei P-1 Kunde des Anbieters oder entsteht durch die Automatisierung ein
   eigenes Dienstleistungsverhältnis?
3. Wo genau verläuft die Grenze zwischen dem erlaubnisfreien Abruf des **eigenen** Kontos
   (Datei-Export, Bank-eigene Schnittstelle) und einem registrierungspflichtigen
   Kontoinformationsdienst?
4. Wie werden die FX-/BTC-Legs eines Cross-Rail-Flusses steuerlich behandelt — ist jeder Hop
   ein eigenes Veräußerungsgeschäft?
5. Welche Aufbewahrungs- und Unveränderbarkeitspflichten (GoBD) gelten für das
   Payment-Journal, und kollidieren sie mit der Redaktions-/Hash-Praxis aus ADR 0018 §9?
6. Wie ist mit personenbezogenen Daten Dritter umzugehen, die ungefragt im eigenen
   Kontoauszug erscheinen (Gegenbuchungen, P-3)?
7. Löst ein Self-Use-Receivable (ADR 0018 §1) Umsatzsteuer- oder Buchführungspflichten aus,
   die heute nicht abgebildet sind?
8. Berührt ein Submarine-Swap-Anbieter (P-2) die Travel Rule, und wenn ja: wen trifft die
   Pflicht?

Keine dieser Fragen ist beantwortet. Solange auch nur Frage 1 oder 3 offen ist, bleibt Welle 3
`BLOCKED` — unabhängig davon, wie fertig der Code wäre.

---

## 7. Was dieses Dokument nicht ist

- **Kein Nachweis** eines funktionierenden Fiat-Pfads — es existiert keiner.
- **Keine Freigabe** für Welle 1, 2 oder 3; jede Welle braucht ihr eigenes ADR und ein Operator-Go.
- **Keine Compliance-Lösung** — `compliance_gate` liefert ohne angeschlossenen Dienst `DENY`.
- **Kein Ersatz** für ADR 0018 (bei Widerspruch gilt das ADR) und **keine Rechtsberatung**.

**Änderungsregel:** Dieses Dokument wird nur zusammen mit dem ADR geändert, das den jeweiligen
Rail freigibt. Wird ein Rail gebaut, wandert seine Zeile von `DESIGNED`/`BLOCKED` nach
`IMPLEMENTED` — mit Pfad und Test als Beleg, nie ohne.
