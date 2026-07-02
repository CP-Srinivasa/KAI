# PR #5 Phase 5 — Test-Failures Klassifikation (2026-05-11)

**Stand:** Nach Phase 3 (Lint+Format) + Phase 4 (mypy) gruen.
17 Test-Failures auf claude/signal-pipeline-gap-analysis-20260510
HEAD `7f7cf63` blockieren Phase 5 (CI gruen + Merge zu p7).

Cross-Refs:
- `session_2026_05_10_signal_pipeline_drift.md` — Memory dokumentiert
  "Phase 2: 29 → 15 fails, +130 Tests gruen". Heute 17 = +2 ggue. Stand
  Memory. Ursache der +2 noch nicht zugeordnet (Phase-3 reformat-side-
  effect? Phase-4 cast-side-effect? Eigene Re-Run-Differenzen?).

---

## Cluster A — Voice-Mock-Drift (2 Tests, ~30 Min Fix)

| Test | Symptom |
|---|---|
| `test_telegram_bot.py::test_voice_message_transcribed_and_processed` | `assert [] == ['Bitcoin ist bullish']` |
| `test_telegram_bot.py::test_voice_signal_handoff_marks_source_voice` | `assert False` |

**Hypothese:** `test_voice_transcriber.py` ist sauber gemockt
(verifiziert), aber die Telegram-Bot-Voice-Handoff-Tests in
`test_telegram_bot.py` haben veralteten Mock-Pfad oder Patch-Target.
Memory `session_2026_05_10_signal_pipeline_drift.md` Phase-3 hatte
genau diese als Blocker dokumentiert.

**Sub-Sprint:** Voice-Mock-Drift-Fix.
**Aufwand:** ~30 Min (1 File, 2 Mocks).
**Risiko:** Niedrig — sauberer Patch, keine Logik-Aenderung.

---

## Cluster B — OpenAI-Schema-Drift (2 Tests, ~45 Min Fix)

| Test | Symptom |
|---|---|
| `test_openai_provider.py::test_analyze_returns_llm_output` | `pydantic.ValidationError` for `LLMAnalysisOutput` |
| `test_openai_provider.py::test_analyze_passes_context_to_prompt` | `pydantic.ValidationError` for `LLMAnalysisOutput` |

**Hypothese:** `LLMAnalysisOutput` Schema wurde erweitert (z.B. neue
Pflichtfelder), Tests-Fixture liefert noch alten Stand. Phase-3-ruff-
format hat `app/integrations/openai/provider.py` reformatiert — aber das
sollte Schema nicht aendern.

**Sub-Sprint:** OpenAI-Test-Fixture an aktuelles Schema anpassen.
**Aufwand:** ~45 Min (Fixture-Update + ggf. neue Felder defaultsen).
**Risiko:** Mittel — Schema-Aenderungen koennten Production-Aufrufe
ebenfalls brechen, sollten verifiziert werden (data-quality-inspector
Sub-Agent waere passend).

---

## Cluster C — Forward-Simulation Filter-Drift (7 Tests, 1-2h Fix)

Alle 7 Tests sind in `tests/unit/test_feature_analysis.py`:
- `test_forward_simulation_filters_bearish` — `assert 0 == 2`
- `test_forward_simulation_filters_low_priority` — `assert 0 == 1`
- `test_forward_simulation_filters_low_precision_source` — `assert 0 == 2`
- `test_forward_simulation_filters_not_actionable` — `assert 0 == 1`
- `test_forward_simulation_combined_filters` — `assert 0 == 2`
- `test_forward_simulation_filters_reactive_bullish_title` — `assert 0 == 2`
- `test_forward_simulation_uses_title_by_doc_fallback` — `assert 0 == 2`

**Hypothese:** Forward-Simulation liefert konsistent leere Liste. Eine
gemeinsame Filter-Aenderung in `app/analysis/feature_analysis.py` oder
einer aufgerufenen Eligibility-Funktion blockt jetzt alle simulierten
Documents. Wahrscheinlich Logik-Drift seit Memory `session_2026_05_10_morning.md`
V-DB5-Backend-Backup `3a1accf` oder seit V-DB5-merge `2107a27`.

**Sub-Sprint:** Forward-Simulation Filter-Logik investigieren — Neo
Sub-Agent fuer Code-Tiefenanalyse.
**Aufwand:** 1-2h (Root-Cause-Suche dominant).
**Risiko:** Mittel — koennte echter Bug sein (verbietet Signale die in
Production durchgehen sollten) ODER Test-Fixture-Drift gegen neue
Filter-Strenge. Cross-Ref zu Cluster D wahrscheinlich.

---

## Cluster D — D-118 Price-Trend-Gate Fail-Open (2 Tests, 30-60 Min Fix)

| Test | Symptom |
|---|---|
| `test_d118_price_trend_gate.py::test_price_check_fail_open_on_api_error` | "API failure must fail-open (alert dispatched)" |
| `test_d118_price_trend_gate.py::test_price_check_fail_open_on_none_ticker` | "None ticker must fail-open" |

**Hypothese:** Price-Trend-Gate hat keinen Fail-Open mehr — bei
API-Fehler oder None-Ticker wird Alert geblockt statt durchgereicht.
Vermutlich Logik-Aenderung im D-118-Pfad.

**Sub-Sprint:** Fail-Open-Verhalten wiederherstellen ODER Tests an neue
Fail-Closed-Strategie anpassen (Operator-Decision welche).
**Aufwand:** 30-60 Min nach Strategie-Klaerung.
**Risiko:** Hoch wenn fail-closed default — koennte legitime Alerts
unterdruecken. Operator-Approval vor Fix.

---

## Cluster E — Eligibility/Annotation-Drift (2 Tests, ~45 Min Fix)

| Test | Symptom |
|---|---|
| `test_alerts.py::test_log_result_marks_directional_crypto_assets_eligible` | `assert False is True` |
| `cli/test_alerts_hold_ops.py::test_alerts_pending_annotations_lists_unannotated_only` | "0 pending directional alerts" statt `'doc-1' in ...` |

**Hypothese:** `eligibility.py` Logik (welche meine Phase-4-Edits an
Type-Annotations beruehrt haben, aber nicht Logik) hat sich geaendert
oder die Annotation-CLI-Lookup-Filter haben Drift.

**Sub-Sprint:** Eligibility-Annotation-Lookup nachfahren.
**Aufwand:** ~45 Min.
**Risiko:** Niedrig — kleine Tests, klar abgrenzbar.

---

## Cluster F — Dashboard-Route-Drift (1 Test, ~15 Min Fix)

| Test | Symptom |
|---|---|
| `test_api_dashboard.py::test_dashboard_routes_in_main_app` | `/dashboard` not in registered routes |

**Befund:** Routes-Liste enthaelt `/dashboard/api/events`, `/dashboard/api/quality`
etc. — aber NICHT das base `/dashboard` (Static-Files-Mount).

**Hypothese:** StaticFiles-Mount wurde aus `main.py` entfernt oder unter
einen anderen Pfad verschoben (Memory `session_2026_05_10_evening_dist_path_drift.md`
beschreibt Pfad-Drift im Dashboard-Bundle-Setup).

**Sub-Sprint:** Static-Files-Registrierung verifizieren + Test
aktualisieren ODER Mount nachziehen.
**Aufwand:** ~15 Min.
**Risiko:** Niedrig — read-only Verifikation reicht oft.

---

## Cluster G — Pi-Transfer-Shell-Drift (1 Test, ~15 Min Fix)

| Test | Symptom |
|---|---|
| `tests/integration/test_shell_scripts_smoke.py::test_pi_transfer_env_group_shows_secrets_handler` | `'scp '` not in script output |

**Hypothese:** `scripts/pi_transfer_artifacts.sh` env-group output ist
ohne `scp` — vermutlich umgeschrieben (Memory `kai_pi5_cutover_track.md`
zeigt aktive Pi-Cutover-Aenderungen).

**Sub-Sprint:** Test an neuen Output anpassen ODER `scp`-Hint im Script
restoren.
**Aufwand:** ~15 Min.
**Risiko:** Niedrig.

---

## Aggregat-Aufwand-Schaetzung

| Cluster | Tests | Aufwand | Risiko |
|---|---|---|---|
| A Voice-Mocks | 2 | 30 Min | niedrig |
| B OpenAI-Schema | 2 | 45 Min | mittel |
| C Forward-Sim | 7 | 1-2h | mittel |
| D D-118 Fail-Open | 2 | 30-60 Min | hoch |
| E Eligibility | 2 | 45 Min | niedrig |
| F Dashboard-Route | 1 | 15 Min | niedrig |
| G Pi-Transfer | 1 | 15 Min | niedrig |
| **Total** | **17** | **~4-6h** | **mixed** |

## Empfohlene Reihenfolge

1. **F + G** zuerst (jeweils 15 Min, Risiko niedrig — Quick Wins)
2. **A + E** danach (Mock/Lookup-Drift, klar abgrenzbar)
3. **B** mit data-quality-inspector Sub-Agent (Schema-Drift verifizieren)
4. **D** Operator-Decision (fail-open vs fail-closed Default)
5. **C** mit Neo Sub-Agent (Root-Cause forward-simulation Filter)

Nach allen Clustern gruen: Phase 5 (CI komplett gruen + Merge zu
`claude/p7/reentry-ia-codex-cycle`).
