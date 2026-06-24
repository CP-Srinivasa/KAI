# Spec: G0-Demand-Probe für `/oracle/fee-series`

**Datum:** 2026-06-24
**Status:** Entwurf (Operator-bestätigt via /goal)
**Phase/Gate-Kontext:** Unified-Lighthouse Gate **G0** (zahlender Use-Case ODER bewiesener Edge). Diese Probe testet den *zahlenden Use-Case* für die L402-Sovereign-Truth-API.
**Worktree:** `C:\Users\sasch\dev\kai-ln-valuelayer` (Mainline-basiert)

---

## 0. Ziel & Leitsatz

Kapitalfreie Nachfrage-Probe für den L402-/Lightning-Zugriff auf `/oracle/fee-series`.
Die Infrastruktur wird **vollständig inert** vorbereitet: zuerst wird nur *Interesse* gemessen
(keine echten sats riskiert, lnd muss nicht produktiv laufen), und der Sprung auf *echte
Zahlung* ist nur noch ein **operator-getriggerter, automatisch geprüfter Flip** — kein weiterer
Code-Bau.

**Leitsatz: „Empfangen vor Senden".** Eingehende Invoices (kapitalfrei — man kann nur gewinnen)
werden strikt vom Senden (`pay_enabled`, Kapital, irreversibel) entkoppelt.

---

## 1. Realer Ist-Stand (Code-Anker, verifiziert)

- `/oracle/fee-series` ist echt, gibt echte Fee-Serien-Daten zurück, gegatet via
  `await _require_paid(request, "fee-series")` + Flag `APP_LN_L402_ENABLED` (Default false →
  sonst HTTP 503). Datei: `app/api/routers/truth_oracle.py`.
- L402-Token-Krypto (HMAC sign/verify, `sha256(preimage)==payment_hash`) ist vollständig real.
  Datei: `app/lightning/l402.py`.
- Invoice-Minting ruft einen **echten lnd** (`add_invoice` → lnd REST `POST /v1/invoices`).
  Datei: `app/lightning/value_layer.py:90–111`, `app/lightning/client.py`.
- **Der Gate-Konflikt (Kern-Befund):** `create_invoice` (receive-side, kein Spend) läuft durch
  den einzigen Chokepoint `_assert_send_allowed()` (`value_layer.py:58–87`), dessen **erster
  Check `pay_enabled` ist** (Zeile 81). `create_invoice` übergibt bereits
  `irreversible=False, confirm=True` (Zeile 101) — die einzige Schranke, die das Minten
  blockiert, ist also `pay_enabled`. `pay_enabled=true` zu setzen würde aber zugleich
  `pay_invoice`/`open_channel`/`send_coins` un-gaten. → genau das trennt U1 auf.
- **Strukturelle Schranke:** Der Reflection-Test `test_ln_value_layer_send_gate` erzwingt, dass
  *jeder* öffentliche Write durch `_assert_send_allowed` geht. U1 macht den `direction`-Split
  daher **innerhalb** dieses Chokepoints, nicht in einer Parallel-Funktion.
- S-002 Mint-Limiter (`l402_mint_per_min`, `l402_mint_budget_per_min`) existiert bereits als
  DoS-Guard vor jedem Mint.
- Earnings-Ledger-Struktur + `record_settled_invoices()` existieren
  (`app/lightning/earnings_ledger.py`), aber **kein Job** füttert sie aus `lnd.ListInvoices`.
- **Keine** Demand-Telemetrie (kein Counter „Paywall-Challenge angefragt / Access gewährt").

---

## 2. Architektur

Receive und Send werden strikt getrennt:

| Aspekt | receive | send |
|---|---|---|
| Beispiel-Action | `create_invoice` | `pay_invoice`, `open_channel`, `send_coins`, `keysend` |
| Kapitalrisiko | keines (man wird bezahlt) | echtes Kapital, teils irreversibel |
| Gate-Flag | **`APP_LN_RECEIVE_ENABLED`** (neu, Default false) | `pay_enabled` (unverändert) |
| Default-Verhalten | inert/`disabled` bis Operator-Flip | hart blockiert |

`APP_LN_L402_ENABLED` steuert nur die L402-*Funktion* (ob `/oracle/*` überhaupt 402-Challenges
ausgibt); `APP_LN_RECEIVE_ENABLED` steuert, ob die zugrundeliegende Invoice *gemintet* werden
darf. Beide müssen true sein, damit die Paywall echte Invoices ausgibt. `pay_enabled` bleibt für
diese Probe **immer false**.

**Fail-closed-Prinzip:** Unbekannte/nicht-allowgelistete Actions laufen in den **send**-Zweig
(strengeres Gate). Eine neue Write-Methode kann nicht versehentlich als „receive" durchrutschen.

---

## 3. Units (5 kleine, je eigenständig prüfbare, reversible PRs)

### U1 — Gate-Split (receive ≠ send) — *security-kritisch* (satoshi-GO-mit-Auflagen, hier eingearbeitet)
`_assert_send_allowed()` bekommt einen `direction`-Parameter **innerhalb** des Chokepoints
(Reflection-Test bleibt grün; er prüft Präsenz des Aufrufs, nicht die Argumente).
- **`direction` EXPLIZIT am Call-Site** übergeben — neben `irreversible=` —, NICHT nur ein
  zentraler String-Lookup. (Drift-Schutz: ein `receive`-deklarierter Spend ist im Code-Review
  sofort sichtbar; ein künftiges HODL-Invoice/Refactor kann nicht lautlos in die Allowlist
  rutschen.)
- **Zentrale Backstop-Assertion (fail-closed) in `_assert_send_allowed`:**
  `RECEIVE_ACTIONS = {"create_invoice"}`; `direction=="receive"` ist NUR erlaubt, wenn
  `action in RECEIVE_ACTIONS`, sonst `raise`; unbekannte/inkonsistente `direction` → **send**-Logik
  (strengste). → ein einzelner Drift-Fehler (falsches Literal ODER falsche Allowlist) reicht nicht
  mehr für ein Leck; beide müssten konsistent falsch sein.
- Gate-Logik:
  - `direction=="receive"` → prüfe `cfg.receive_enabled` STATT `pay_enabled`; **`dry_run` bleibt**
    (sonst mintet jeder Aufruf sofort), `irreversible`/`confirm` unverändert.
  - `direction=="send"` → heutige Logik UNVERÄNDERT (`pay_enabled` zuerst).
- Neues Setting `receive_enabled: bool = Field(default=False)` in `lightning_settings.py`
  (Env `APP_LN_RECEIVE_ENABLED`), neben `pay_enabled`, Docstring „receive-side, kapitalfrei,
  entkoppelt vom Spend-Kill-Switch".
- `create_invoice` ruft `_assert_send_allowed(..., direction="receive")`; alle Spends
  (`pay_invoice`/`keysend`/`send_coins`/`open_channel`/`close_channel`/`rebalance_plan`) übergeben
  explizit `direction="send"`.
- **Reflection-Test erweitern:** jede Methode mit `irreversible=True` MUSS `direction="send"`
  übergeben; nur Methoden in `RECEIVE_ACTIONS` dürfen `receive` deklarieren — strukturell
  erzwungen, damit der Invariant einen Refactor überlebt.
- **Receive-Pfad-Härtung (gleicher PR):**
  - **Invoice-Expiry** in `add_invoice` setzen (Default 300s, beschränkt ≤600s) gegen unbezahlt
    akkumulierende Invoices (DB-/HTLC-Last).
  - *(Verschoben nach U2:)* Die **Mint-Limiter-Reichweite** (Client-IP via
    `CF-Connecting-IP`/`X-Forwarded-For` + `ln_control`-Execute-Pfad) wird in U2 gebaut, weil dort
    der gleiche vertrauenswürdige Client-IP-Helfer für den Demand-Fingerprint entsteht — eine SSOT
    für „wer ist der Anfrager" statt zwei.
- **Akzeptanz / Negativ-Regressionstest (Kern-Invariant):** mit `receive_enabled=true,
  pay_enabled=false` → `create_invoice` **executes**, ABER `pay_invoice`/`keysend`/`send_coins`/
  `open_channel`/`close_channel` ALLE `disabled` (Build-Client `assert_not_called`). Dauerhafter
  Regressionstest.
- **Gate vor Merge:** satoshi-Review bestanden (GO-mit-Auflagen) — die Auflagen sind in dieser
  Unit + §6 verankert.

### U2 — Demand-Telemetrie
Neues `app/lightning/demand_ledger.py` → `artifacts/ln_demand_ledger.jsonl`.
- Events:
  - `l402_challenge_minted` — in `_issue_challenge` (jemand trifft die Paywall unbezahlt).
    Felder: `ts`, `event`, `scope`, `requester_fp`, `price_sat`, `payment_hash`.
  - `l402_access_granted` — in `verify()`-Erfolg (gültiges Token+Preimage = hat gezahlt).
    Felder: `ts`, `event`, `scope`, `payment_hash`.
- **Privacy:** `requester_fp` = `sha256(salt || client_ip)[:16]`, Salt aus `l402_secret`
  abgeleitet (nie roh-IP, nie persistierte IP). Dokumentierte Heuristik, keine harte Identität.
- **Reverse-Proxy-Hinweis (Implementierung):** Die App läuft hinter cloudflared —
  `request.client.host` ist der Tunnel, nicht der Client. `client_ip` MUSS aus dem
  vertrauenswürdigen Forward-Header (`CF-Connecting-IP`, sonst erste `X-Forwarded-For`)
  gewonnen werden, sonst kollabieren alle Fingerprints auf einen Wert und der ≥2-FP-Guard (§5)
  ist wirkungslos.
- **Shared Client-IP-SSOT + Mint-Limiter-Reichweite (aus U1 hierher gezogen, satoshi-Auflage 6):**
  Der gleiche vertrauenswürdige `client_ip`-Helfer speist (a) den Demand-Fingerprint und (b) den
  S-002 Mint-Limiter-Key in `_gate_mint` (heute `request.client.host` → hinter dem Tunnel
  wirkungslos). Zusätzlich: der `ln_control.py`-`create_invoice`-Execute-Pfad muss ebenfalls
  rate-limitiert sein (oder bewusst nur authentifizierten Operatoren offenstehen), damit der
  Limiter nicht über die Cockpit-Surface umgangen wird.
- Append-only, fail-soft (Telemetrie-Fehler darf die Antwort nie blockieren).
- **Akzeptanz:** beide Hooks feuern korrekt; Ledger-Zeile validiert gegen Schema; keine Roh-IP
  im Output; Telemetrie-Exception bricht die Request nicht ab.

### U3 — Earnings-Booking-Job
Periodischer Job liest `lnd.ListInvoices`, filtert **settled** Invoices mit Memo-Präfix
`kai-oracle:` und bucht **idempotent** via bestehendem `record_settled_invoices()` ins
`ln_earnings_ledger.jsonl`.
- Read-only gegen den **eigenen** Node (eigene settled Invoices listen) = kapitalfrei.
- Inert bis `APP_LN_ENABLED=true`; als systemd-Timer/Scheduler-Hook im KAI-Muster, **kein**
  `Requires=kai-server` (Lehre [[kai_timer_requires_cascade]]).
- **Akzeptanz:** doppelter Lauf bucht keine Duplikate (Idempotenz via `payment_hash`); nur
  settled + Memo-Match werden gebucht; bei `APP_LN_ENABLED=false` no-op.

### U4 — Demand-Evaluator
`evaluate_l402_demand.py` liest Demand- + Earnings-Ledger und bewertet gegen die
**pre-registrierte** Schwelle (§5): distinkte Challenge-Treffer, distinkte settled Zahlungen,
distinkte Fingerprints, distinkte Kalendertage, Konversion, Zeitfenster.
- Output: G0-PASS / NO-PASS + Kennzahlen, sichtbar in Dashboard/Digest.
- **Akzeptanz:** Schwellenlogik inkl. Fälschungs-Guard (siehe §5) deterministisch getestet;
  „kein Go-Live" → ehrliches „Fenster nicht gestartet".

### U5 — Go-Live-Preflight + Pre-Registration + Listing-Artefakt
`ln_golive_preflight` liefert GO/NO-GO anhand:
lnd `getinfo` erreichbar · macaroon/tls vorhanden · `l402_secret` gesetzt · `receive_enabled`
verdrahtet · Booking-Job geplant · Telemetrie schreibt · `pay_enabled` IST false (Negativ-Check) ·
**Macaroon scope-minimal** (Probe: ein `pay_invoice`-Call MUSS `permission denied` vom Node liefern
→ beweist, dass das eingespielte Macaroon KEINEN Spend-Scope hat; sonst NO-GO).
- Zusätzlich: **Runbook** (exakte Flag-Sequenz für den Flip), **DECISION_LOG-Eintrag** mit der
  fixierten Pre-Registration (§5), **Listing-Artefakt** (Endpoint-URL, Preis, Beschreibung zum
  Posten).
- **Akzeptanz:** GO/NO-GO-Matrix getestet; jeder fehlende Baustein erscheint namentlich im
  NO-GO; das Artefakt ist vollständig (kein Platzhalter).

---

## 4. Daten-/Kontrollfluss

```
GET /oracle/fee-series  (unpaid)
  └─ _issue_challenge → mint invoice (U1: receive-gate) → U2: log challenge_minted
     → 402 + WWW-Authenticate: L402 token=…, invoice=<bolt11>
Client zahlt BOLT11 (off-KAI) → lernt preimage
GET /oracle/fee-series  Authorization: L402 <token>:<preimage>
  └─ verify() ok → U2: log access_granted → Daten serviert
Hintergrund: U3 liest lnd.ListInvoices → bucht settled kai-oracle:* ins earnings_ledger
Auswertung: U4 rechnet ≥3 settled / 14d + Guard → G0-Verdikt
```

---

## 5. Pre-Registration (vor dem Messen fixiert — nicht nachträglich verschiebbar)

- **Preis:** 100 sats / Abruf (Code-Default ist 10; die Probe setzt 100 als realistischere
  Demand-Schwelle).
- **Fenster:** 14 Tage ab dokumentiertem Go-Live-Datum.
- **G0-PASS =** ≥3 settled `kai-oracle:fee-series`-Zahlungen **UND** von ≥2 distinkten
  Requester-Fingerprints **UND** an ≥2 distinkten Kalendertagen.
  - Der `≥2 Fingerprints / ≥2 Tage`-Zusatz verhindert, dass ein einzelner Akteur per 3×
    Selbstzahlung Nachfrage fälscht.
- **Ehrlichkeits-Limitation (dokumentiert, nicht als harte Identität verkauft):** LN-Zahler-
  Identität ist *nicht* beweisbar; der IP-Fingerprint ist nur eine Heuristik. Ein motivierter
  Akteur kann den Guard mit mehreren IPs umgehen — die Schwelle misst „plausibel plurale,
  zahlende Nachfrage", keinen kryptografischen Identitätsbeweis.
- **Distribution:** öffentliche Listung (Nostr / BTC-Dev-Kanäle + ein L402-Verzeichnis). Das
  Posting selbst bleibt ein **externer Operator-Schritt**; U5 liefert nur das fertige Artefakt.

---

## 6. Sicherheit

- `pay_enabled` wird für diese Probe **niemals** aktiviert. Erlaubt sind nur
  `APP_LN_RECEIVE_ENABLED` + `APP_LN_L402_ENABLED`. Senden bleibt hart deaktiviert.
- **U1 ist security-kritisch** (Fehl-Klassifikation = un-gateter Spend) → **satoshi-Review vor
  Merge** ist Pflicht-Gate. Prüf-Fokus: kann *irgendein* Spend-Pfad über den receive-Zweig
  lecken? Ist die Allowlist wirklich fail-closed? Macaroon-Implikation (Minten braucht
  invoice-write-Macaroon, NIE admin/readonly-Verwechslung)? Replay/Abuse des receive-Pfads?
- **Scope-minimales Macaroon (harte Deployment-Auflage, NICHT optional — sonst NO-GO für den
  Live-Flip):** Der lnd-Client nutzt heute EIN Macaroon für read+invoice+spend (`client.py`, ein
  `Grpc-Metadata-macaroon`-Header auf jedem Call). Für den Flip MUSS ein dediziertes
  `invoices:write invoices:read`-only-Macaroon eingespielt werden — KEIN admin, KEIN
  `onchain:write`/`offchain:write`/`peers:write`. Damit lehnt der **Node selbst** bei einem
  App-Bug jeden Spend mit `permission denied` ab — die einzige vom App-Code unabhängige
  Verteidigungsschicht. (Saubere Endform, optional: zwei getrennte Macaroons im Settings-Objekt,
  sodass die Spend-Methoden gar kein invoice-write-Macaroon in der Hand haben.)
- Go-Live-Flip bleibt operator-gegated mit `plan_hash`/`confirm`-Muster (B-005 HOTP existiert) —
  **kein autonomer Flip durch den Agenten**.
- Zahlungsnachweis: L402-Preimage-Besitz *ist* der Settlement-Beweis (lnd gibt das Preimage nur
  bei erfolgreichem Settlement an den Zahler frei). U3 verifiziert zusätzlich gegen
  `ListInvoices`, sodass das Earnings-Ledger nur echt-settled Zahlungen führt.
- S-002 Mint-Limiter bleibt aktiv (DoS-Schutz, da jede unbezahlte Anfrage real mintet).

---

## 7. Scope & Tests

**Scope:** 5 kleine, inerte, reversible PRs (U1–U5), alle kapitalfrei. **Kein Go-Live in diesem
Sprint** — der eigentliche Flip + das öffentliche Posten + der erreichbare echte lnd-Node bleiben
Operator-gegated.

**Tests:**
- U1: Action-Klassifikations-Matrix (jede Action → richtiges Gate · unknown/inkonsistente
  `direction` → send · receive blockiert ohne `receive_enabled` · `dry_run` greift im
  Receive-Zweig) · **Negativ-Kern-Invariant** (`receive_enabled=true, pay_enabled=false` →
  `create_invoice` executes, ALLE Spends `disabled`/`assert_not_called`) · Backstop-Assertion
  (Spend mit `direction="receive"` → raise) · Reflection-Test erweitert (`irreversible=True` ⇒
  `direction="send"`; nur `RECEIVE_ACTIONS` dürfen `receive`).
- U2: Ledger-Schema · Hook-Auslösung beider Events · Hash-Privacy (keine Roh-IP) · fail-soft.
- U3: Idempotenz · Memo-Filter · settled-only · no-op bei `enabled=false`.
- U4: Schwellenlogik · Fälschungs-Guard (≥2 FP / ≥2 Tage) · „Fenster nicht gestartet".
- U5: Preflight GO/NO-GO-Matrix · Negativ-Check `pay_enabled==false` · Artefakt-Vollständigkeit.

**Out-of-Scope (bewusst nicht jetzt):** echte lnd-Node-Konfiguration, Go-Live-Flip, öffentliches
Posting, Mainnet-Channels, jeglicher Spend-Pfad.
