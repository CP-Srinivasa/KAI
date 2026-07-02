# Entry-Mode-Policy — explizite Modi, Aliase, Limits (Sprint S3, D-233)

**Stand:** 2026-06-11 · **Schließt:** Issue #181 · **SSOT-Code:** `app/execution/entry_policy.py`

`EXECUTION_ENTRY_MODE` ist der globale Gate auf risiko-erhöhende NEUE Positionen.
Exits/Risk-Reduktionen werden nie davon gegated. Dieses Dokument ist der
Audit-Anker für alle Modi, Routen, Aliase, Bypässe und Limits.

## Modus-Matrix (Route × Modus)

| Route \ Modus | disabled | paper_premium_limited | paper_learning | paper | probe | live_* |
|---|---|---|---|---|---|---|
| autonomous_loop | ❌ `entry_mode_disabled` | ❌ geschlossen | ❌ geschlossen | ✅ | ✅ | ✅ (Settings-Validator erzwingt `EXECUTION_MODE=live`) |
| premium_paper (Bridge) | nur via **Alias A** | ✅ (Master-Enable nötig) + **Limits** | ✅ (Master-Enable) + **Limits** | ✅ (Master-Enable) | ✅ | ✅ |
| real_analysis_paper (Feeder/run_cycle) | nur via **Alias B** | ❌ `learning_route_closed_…` | ✅ (Master-Enable) + **Limits** | nur via Alias B | nur via Alias B | nur via Alias B |
| premium_fastlane | Legacy-Two-Flag-Gate (D-232), faktisch OFF | ❌ hart | ❌ hart | Legacy-Gate | Legacy-Gate | Legacy-Gate |

**Master-Enables** (immer zusätzlich nötig, fail-closed):
`PREMIUM_PAPER_EXECUTION_ENABLED` für premium_paper, `REAL_ANALYSIS_PAPER_ENABLED` für real_analysis_paper.

## Migrations-Aliase (Pi-Neutralität)

| Alias | Spelling (drei Arme) | Entspricht neuem Modus |
|---|---|---|
| **A** `premium_three_arm_ack` (#208) | `PREMIUM_PAPER_EXECUTION_ENABLED=true` ∧ `PREMIUM_ALLOW_PAPER_WHILE_ENTRY_DISABLED=true` ∧ `PREMIUM_ENTRY_DISABLED_OVERRIDE_ACK=I_UNDERSTAND_PREMIUM_PAPER_WHILE_DISABLED` | `paper_premium_limited` |
| **B** `real_analysis_three_arm_ack` (#209) | `REAL_ANALYSIS_PAPER_ENABLED=true` ∧ `…_ALLOW_PAPER_WHILE_ENTRY_DISABLED=true` ∧ `…_ENTRY_DISABLED_OVERRIDE_ACK=I_UNDERSTAND_REAL_ANALYSIS_PAPER_WHILE_DISABLED` | (zusammen mit A) `paper_learning` |

Unter `disabled` delegiert `resolve_entry_policy` **byte-identisch** an die
Legacy-Override-Funktionen — ein Pi mit `disabled`+Acks verhält sich nach dem
Merge exakt wie davor; das Verdikt markiert nur zusätzlich `alias_used`.
In den neuen Modi sind die Acks NICHT nötig: der explizit gesetzte Modus ist
die Operator-Erklärung (#181 §8). Die Aliase sind deprecated, bleiben aber
funktional, bis der Operator die `.env` auf den neuen Modus migriert.

## Route-Volumen-Limits (#181 §5)

Env je Route (`PREMIUM_…` / `REAL_ANALYSIS_PAPER_…`), 0 = unlimited:
`PAPER_ROUTE_MAX_TRADES_PER_HOUR`, `PAPER_ROUTE_MAX_NOTIONAL_PER_DAY_USD`,
`PAPER_ROUTE_MAX_OPEN_POSITIONS`.

Default-Injektion NUR in den neuen Modi (alle drei Env auf 0):
premium 6/h · 10 000 USD/Tag · 10 offen — learning 6/h · 5 000 USD/Tag · 10 offen.
Messung read-only aus `artifacts/paper_execution_audit.jsonl`
(Label-Join `paper_trade_label.order_id→source_name`, opening fills only);
`max_open_positions` prüft gegen die Engine-globale Zählung (kann das
Risk-Limit nur verschärfen, nie aufweichen). Verstoß → Refusal mit
`ROUTE_LIMIT_EXCEEDED` + Usage-Snapshot im Bridge-/Cycle-Audit.

## Kontradiktions-Preflight (#181 §7)

Fail-closed: bei aktivem limitierten Modus UND (`PREMIUM_FASTLANE_ENABLED=true`
∨ `PREMIUM_LIVE_EXECUTION_ENABLED=true` ∨ `PREMIUM_FASTLANE_LIVE_ENABLED=true`)
verweigert die Policy ALLE Routen (`ENTRY_POLICY_CONTRADICTION`), bis der
Operator die Konfiguration bereinigt. Unter `disabled` gilt unverändert das
D-232-Two-Flag-Gate der Fastlane (nicht doppelt bewertet — Pi-neutral).

## Reason-Codes (Audit-Vertrag, #181 §6)

`ENTRY_MODE_DISABLED` · `ROUTE_NOT_OPEN_IN_MODE` · `ROUTE_LIMIT_EXCEEDED` ·
`ENTRY_POLICY_CONTRADICTION` · `FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED` (legacy)
· textuelle Refusals der Aliase (`premium_paper_entry_disabled_override_not_armed` …).
Jede Routing-Entscheidung der neuen Modi schreibt Stage + reason_codes in
`bridge_pending_orders.jsonl` bzw. Cycle-Notes (`route_limit_reject:…`).

## Mapping der 8 Punkte aus Issue #181

| # | Forderung | Erfüllung |
|---|---|---|
| 1 | Audit aller Bypass-Defaults | dieses Dokument + D-232 (Fastlane-Defaults False) + Alias-Tabelle oben |
| 2 | fail-closed Defaults | alle neuen Felder default 0/False; Modus-Default bleibt `paper` (legacy); Master-Enables Pflicht |
| 3 | `disabled` blockt ALLE risiko-erhöhenden Pfade | Invariant-Test: voller Flag-Sweep ohne korrekte Acks ⇒ 0 offene Routen (`test_entry_policy.py::test_disabled_without_acks_refuses_every_route_across_flag_sweep`) |
| 4 | Tests `disabled`+Fastlane ⇒ 0 Fills | `test_bridge_entry_mode_guard.py` (bestehend + S3-Erweiterung) |
| 5 | Limits pro Quelle/Stunde/Tag/Positionen | `RouteLimits` + `check_route_limits` + Default-Injektion in neuen Modi |
| 6 | reason_codes + Audit je Routing-Entscheidung | neue `ExecutionBlockerCode`s + Stage-Records + Cycle-Notes |
| 7 | Preflight `disabled`∧Fastlane ⇒ fail-closed | D-232-Two-Flag-Gate (legacy) + Kontradiktions-Preflight (neue Modi) |
| 8 | expliziter operator-lesbarer Modus statt Bypass-Kaskade | `paper_premium_limited` / `paper_learning` |

## Operator-Migration (optional, separater Schritt)

Heutiger Pi-Stand (`disabled` + Aliase A+B armed) ≙ `paper_learning`.
Migrationsschritt (wenn gewünscht, kein Zwang):
`EXECUTION_ENTRY_MODE=paper_learning` setzen, die 4 Alias-Env-Zeilen
(`*_ALLOW_PAPER_WHILE_ENTRY_DISABLED`, `*_ENTRY_DISABLED_OVERRIDE_ACK`)
entfernen, Master-Enables behalten. Verhalten identisch — zusätzlich greifen
dann die Default-Route-Limits (gewollt). Rollback = Env zurück.
