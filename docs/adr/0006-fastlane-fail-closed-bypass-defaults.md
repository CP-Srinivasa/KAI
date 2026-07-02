# ADR 0006 — Premium-Fastlane: fail-closed Bypass-Defaults + Entry-Mode-Override-Preflight

- Status: accepted
- Datum: 2026-06-08
- Kontext-Issue: #181 (Follow-up zu #179, ADR 0005)
- Supersedes (teilweise): ADR 0005 Default-Posture der Bypässe

## Problem (#179-Incident)

ADR 0005 führte die 30-Tage-Fastlane mit **sieben Gate-Bypässen ein, die alle
per Default `True`** waren. Der Master-Schalter `PREMIUM_FASTLANE_ENABLED`
defaultete zwar `False`, aber sobald ein Operator (oder eine Session) ihn auf
`true` setzte, war **die gesamte Bypass-Kaskade scharf** — inklusive
`bypass_entry_mode_for_paper`.

Damit hörte `EXECUTION_ENTRY_MODE=disabled` auf, *disabled* zu bedeuten: der
globale Kill-Switch wurde für den Premium-Paper-Pfad durch **einen einzigen
Flag-Flip** neutralisiert. Auf der Pi war die Fastlane real scharf (0 Fills, 0
Positionen — der Pfad feuerte nie, war aber bewaffnet). Das ist kein additiver
Pfad, sondern ein **zweiter Betriebsmodus mit eigener Safety-Semantik**, der
fünf Safety-Layer auf einmal umging.

## Entscheidung

1. **Alle sieben Bypass-Defaults → `False` (fail-closed).** Das Aktivieren der
   Fastlane relaxt für sich genommen **kein** Gate mehr. Jeder Bypass ist ein
   expliziter, einzeln auditierbarer Per-Flag-Opt-in.

2. **Zweiter, unabhängiger Entry-Mode-Override** `allow_entry_mode_disabled_override`
   (default `False`). Unter `entry_mode=disabled` wird `bypass_entry_mode_for_paper`
   **nur dann** honoriert, wenn dieses zweite Acknowledgement *zusätzlich* gesetzt
   ist. Das spiegelt das bestehende Live-Triple-Flag-Muster: das Aushebeln des
   globalen Kill-Switch erfordert nun **zwei** explizite Opt-ins statt einem.

3. **Preflight-Guard** `fastlane_entry_mode_override(settings) -> (allowed, refusal_code)`
   als reine SSOT-Funktion. Bridge **und** `fastlane_status` (Dashboard) lesen
   dieselbe Funktion, damit Operator-Wahrheit und Laufzeitverhalten nicht driften.
   Wird der Bypass gewünscht, aber der Override ist nicht scharf, läuft der Pfad
   **fail-closed** in den normalen `rejected_entry_mode`-Terminal und protokolliert
   eine Refusal mit Reason-Code `FASTLANE_ENTRY_MODE_OVERRIDE_NOT_ARMED`.

## Bypass-Audit (Issue #181 §1)

| Bypass | alter Default | neuer Default | Risiko bei ON |
|---|---|---|---|
| `bypass_manual_approval` | True | **False** | überspringt Operator-Freigabe |
| `bypass_source_allowlist` | True | **False** | nicht-allowlistete Quelle wird geroutet |
| `bypass_entry_mode_for_paper` | True | **False** | **neutralisiert den globalen Kill-Switch** (nur mit Override) |
| `bypass_risk_quality_gates` | True | **False** | reward/risk-Quality-Gates beobachtend statt blockend |
| `bypass_source_quality_gates` | True | **False** | Source-Quality beobachtend |
| `bypass_priority_tier_gates` | True | **False** | Priority-Tier beobachtend |
| `bypass_forward_precision_gates` | True | **False** | Forward-Precision beobachtend |
| `allow_entry_mode_disabled_override` *(neu)* | — | **False** | zweite Arm-Stufe für den Kill-Switch-Override |

## Invarianten (unverändert)

- LIVE bleibt durch das Triple-Flag hart geschützt; diese Bridge schreibt nie
  eine Live-Order.
- Die Minimum-Guards (schema-valid, entry/SL/targets/side/symbol, dup-suppression,
  qty>0, notional in [min,max], Geometrie, Scale) bleiben nicht-bypassbar.
- Der klassische Pfad jeder nicht-Premium-Quelle ist unberührt.

## Konsequenzen

- Re-Enable der Fastlane erfordert jetzt bewusste, dokumentierte Per-Flag-Opt-ins
  plus den separaten Override-Arm — kein versehentliches Scharfschalten durch
  einen Master-Flag mehr.
- `fastlane_status.overrides_classic_block` meldet nur noch `True`, wenn der
  Override real scharf ist (Dashboard-Wahrheit == Laufzeit).
- Tests pinnen: `disabled` + `PREMIUM_FASTLANE_ENABLED=true` ohne Arm → 0 Fills /
  0 Orders / 0 Positionen; Bypass-Flag ohne Override → fail-closed + Refusal-Record.

## Bewusst nicht in diesem PR (Issue #181 §5/§8)

- Per-Source-Limits, max trades/hour, max notional/day (über die bestehenden
  `max_open_positions`/per-symbol-Caps hinaus).
- Vollständiges Remodelling als expliziter `entry_mode`-Enum-Wert
  (`premium_paper_limited`) statt Bypass-Flags.

Beide sind durch die fail-closed-Posture nicht-dringlich (der Pfad bleibt ohne
explizites Arming OFF) und gehören in einen Folge-Sprint.

## Re-Enable-Merge-Gate (BINDEND)

Premium-Fastlane-Paper bleibt **OFF** als kanonische Betriebswahrheit (D-231 /
D-232). Dieser PR (#185) liefert den fail-closed-Kern des in D-231 geforderten
#181-Safety-Sprints — er **re-aktiviert nichts**.

**Kein** PR darf Premium-Fastlane oder Premium-Paper-Execution scharfschalten
(weder per Default-Flip, Runtime-Flag, `.env`, noch aus Memory), solange nicht
**alle** folgenden Punkte in *einem* operator-reviewten PR erfüllt und durch
Tests belegt sind:

1. **Expliziter, bounded, operatorlesbarer Modus** statt impliziter Bypass-Kaskade
   — z.B. `EXECUTION_ENTRY_MODE=premium_paper_limited` (nur authentische
   Premium-Paper-Kandidaten, mit Limits + Audit, kein Live, keine Auto-Promotion).
2. **Fail-closed Preflight**: `EXECUTION_ENTRY_MODE == "disabled"` ∧
   `PREMIUM_FASTLANE_ENABLED` ⇒ fail-closed, außer der bounded Modus aus (1) ist
   explizit gesetzt. (In diesem PR via `fastlane_entry_mode_override` + Zwei-Flag-Arm
   gelegt; ein künftiger Re-Enable ersetzt den Zwei-Flag-Arm durch den expliziten
   Modus, lockert ihn nicht.)
3. **Tests** für `disabled` (∧ Fastlane scharf, ohne bounded Modus) ⇒
   **0 Fills / 0 Orders / 0 Positionen** — plus per-Achse-Refusal-Tests.
4. **Per-Source-/Rate-/Notional-Limits**: max trades/hour, max notional/day,
   max open positions, per-source-Cap (Issue #181 §5).
5. **reason_codes + Operator-Sichtbarkeit**: jede Fastlane-Routing-/Refusal-
   Entscheidung trägt einen stabilen `reason_code` und ist im Dashboard/Audit
   sichtbar (`overrides_classic_block` == Laufzeit-Wahrheit).

Fehlt einer dieser Punkte → **fail-closed**: der PR wird nicht gemerged und
Fastlane bleibt OFF. Dieser Gate ist die operationalisierte Form von D-231s
„Reaktivierung nur über den #181-Safety-Sprint".

**Erzwingung (Zähne, nicht nur Doku):** Die fail-closed-Posture ist code-seitig
durch Tests gepinnt — `tests/unit/test_premium_fastlane_settings.py::test_fastlane_defaults_are_fail_closed`
(alle sieben Bypass-Defaults + `allow_entry_mode_disabled_override` == False) und
`tests/unit/test_premium_fastlane_bridge.py::test_fastlane_enabled_defaults_do_not_bypass_entry_mode`
(disabled ∧ Fastlane ohne Arm → 0 Fills). Ein PR, der einen Default zurück auf
`True` flippt oder den Preflight aushebelt, dreht CI **rot** und ist damit nicht
mergebar — die Kriterien (1)–(3) sind also nicht nur Review-Politik, sondern
Test-erzwungen.
