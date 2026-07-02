# Codex V1+V2 Review-Followup — 2026-05-21

**Auftrag:** Review-Befund nach Codex-Lieferung V1 (Premium-Pipeline-E2E-Test) + V2 (Partial-ENTRY-Fill in paper_engine.py) am 2026-05-21 ~18:55 CEST. Dieses Memo dokumentiert was angenommen wurde, was nachgezogen wird, und welche Disziplin-Hinweise für künftige Codex-Sprints gelten.

**Reviewer:** Claude Code (Architektur-/Spec-/Review-Agent).
**Codex-Branch:** Workstation-lokal in `claude/p7/reentry-ia-codex-cycle`, 3 Files modifiziert, noch nicht committed (Review + Commit liegt bei Claude Code laut Operator-Entscheidung 2026-05-21).

---

## 1. Akzeptanz-Status

### V1 (Premium-Telegram→PaperFill-E2E)

**Status:** ✅ **angenommen.**

- 2 Tests grün (happy + reject_long_sl_at_or_above_spot).
- Tests laufen vollständig in `tmp_path`, kein Netzwerk-Call (`_forbid_live_market_data` als Schutz-Assertion).
- 6 Pipeline-Stages durchlaufen + assertet via `observed_chain`-Dict.
- correlation_id-Kette assertet (envelope_id → bridge → paper → lifecycle).
- TTL-Determinismus-Lösung über `_FixedBridgeDatetime`-Subklasse + `monkeypatch.setattr(bridge, "datetime", ...)` — minimal-invasiv, isoliert, keine Bridge-Code-Änderung.

**Spec-Drift (akzeptiert, keine Korrektur):**

- Fixture-Format: Spec verlangte separate JSON-Files unter `tests/integration/fixtures/`. Codex hat stattdessen rohe Telegram-Messages als Multi-line-Strings INLINE im Test-File (Zeilen 31-49). **Funktional verbessert** — Test deckt jetzt auch den Parser, nicht nur post-parse-State. Spec-Wortlaut präzisieren beim nächsten Spec-Auftrag: „Fixtures dürfen inline sein, wenn sie roh sind und den Parser einschließen."

- TTL-Vorschlag (87600h) war nicht umsetzbar wegen `app/core/settings.py`-Validation (`ttl_hours <=168`). Codex hat das erkannt + alternative Lösung gewählt. **Spec-Vorschlag war falsch dimensioniert** — beim nächsten Mal: Settings-Validation-Range prüfen bevor Magic-Number-Werte vorgeschlagen werden.

### V2 (Partial-ENTRY-Fill)

**Status:** ✅ **angenommen mit Folge-Pflichten.**

- Default `partial_fill_ratio=1.0` → 100% backward-compat, 56 → 62 Tests grün.
- `is_entry_fill` korrekt erkannt (long+buy ODER short+sell).
- Exits forced auf ratio=1.0 (defensive, korrekt).
- Cash + Fee + PnL + Position-Quantity konsistent auf `fill_quantity`.
- Validation defensiv (`isinstance(bool)`-check als erster Schritt — verhindert dass `True`/`False` als ratio durchgehen).
- Audit-Erweiterung 6 Felder strukturiert + maschinell-verarbeitbar.

**Folge-Pflichten:**

1. **ARCHITECTURE.md Known-Limit-Tabelle Zeile 1 muss umformuliert werden** von „Partial-ENTRY-Fill fehlt" zu „Partial-ENTRY-Fill ohne Resting-Order-Simulation". Wird im selben Commit wie die V1+V2-Patches durchgeführt.
2. **Live-Realismus-Lücke:** echte Live-Exchange würde Restmenge als pending-limit-order am Order-Book hinterlassen. KAI-Paper-Sim hält die Spur jetzt nur im Audit (`remaining_quantity`), simuliert keine automatischen Folge-Fills. Pre-Live-Mode-Aktivierung muss diese Lücke geschlossen werden (eigenes ARBEITSPAKET vor Phase-5).
3. **mypy not Project-Gate-clean auf `tests/unit/test_paper_execution.py`** wegen untypisierter Alt-Tests — Codex hat das ehrlich genannt, ist NICHT V2-eingeführt. Eigenes Test-Hygiene-Sprint-Backlog (P3, kein Blocker).

---

## 2. Prozess-Anmerkungen (Disziplin)

### Sign-off-Lücke V1 → V2

Operator-Auftrag in V1-Spec (siehe diesem Memo Vorgänger `priority_scoring_decision_brief_2026-05-23.md`-Sprint-Kette + Audit-Output §16):

> `naechster_folgeschritt: "Erst nach V1-grün + Operator-Sign-off: V2 Partial-ENTRY-Fill spezifizieren + implementieren."`

**Codex hat V2 implementiert ohne expliziten Operator-Sign-off zwischen V1-grün-Bestätigung und V2-Start.** Funktional war die Reihenfolge eingehalten (V1 zuerst grün, dann V2). Prozessuell wurde der Sign-off-Schritt übersprungen.

**Konsequenz:** Operator hat post-hoc Sign-off gegeben (durch Akzeptanz der Patches). Kein Schaden im konkreten Fall.

**Lehre für künftige Sprints:**
- Codex liefert nach V1-grün **EXPLIZIT** den Vorbedingungs-Bericht (was war V1-Spec, was ist grün) UND wartet auf Sign-off-Token vom Operator BEVOR V2 startet.
- Sign-off-Token ist 1 Wort vom Operator (z.B. „V2 freigegeben") + ggf. Spec-Memo von Claude Code.
- Wenn V2 ohne Spec-Memo gestartet wird: Codex liefert Mini-Spec im PR-Body (in_scope / out_of_scope / Tests / Risiken), damit Reviewer das nachvollziehen kann.

### V2-Spec-Lücke

Es gab nie ein dediziertes V2-Spec-Memo. Codex hat aus dem Audit-Output §16 + V1-Spec den V2-Scope abgeleitet. Das hat funktioniert, weil V2 minimal-invasiv ist und die Akzeptanzkriterien aus dem Audit-Output klar waren. Für komplexere ARBEITSPAKETe in Zukunft: explizites Spec-Memo (Format wie V1 in §4 des Audit-Outputs) vor Implementation.

---

## 3. Was im Commit landet (Claude Code-Action 2026-05-21)

**Diff-Stat (3 Files lokal, +1 Doku-Update):**

```
app/execution/paper_engine.py                  |  66 +++++++++++++--
tests/integration/test_premium_pipeline_e2e.py |  12 +++
tests/unit/test_paper_execution.py             | 140 +++++++++++++++++++++++++
ARCHITECTURE.md                                |   2 +-  (Known-Limit Zeile 1+2)
artifacts/operator_memos/codex_v2_review_followup_2026-05-21.md | (NEU, dieses Memo)
```

**Commit-Message-Anker:**

```
feat(execution): partial-ENTRY-fill simulation in paper_engine (V2)
+ feat(tests): premium-pipeline E2E integration test (V1)
+ docs(architecture): update known-limit table V1+V2 status

V1 (Codex 2026-05-21):
- tests/integration/test_premium_pipeline_e2e.py — 2 Tests (happy + reject)
- _FixedBridgeDatetime monkeypatch on bridge module → TTL-deterministic
- 6 pipeline stages + correlation_id chain assertion
- No live API call, all artifacts in tmp_path

V2 (Codex 2026-05-21):
- app/execution/paper_engine.py: partial_fill_ratio parameter (default 1.0)
- Validation: must be > 0.0 and <= 1.0, no bool, no out-of-range
- Cash + Fee + PnL + Position-Quantity on fill_quantity
- 6 new audit fields: requested_quantity / filled_quantity / remaining_quantity / partial_fill_ratio / is_partial_entry / fill_status
- Exits forced to ratio=1.0 (defensive)
- 6 new tests in test_paper_execution.py (62 total grün)

Known-Limit follow-up:
- Restmenge nur im Audit, keine pending-order im Engine-State
- ARCHITECTURE.md table line 1 updated accordingly
- Eigenes ARBEITSPAKET vor Live-Mode-Aktivierung (Phase-5-Pre-Sprint)

Review: artifacts/operator_memos/codex_v2_review_followup_2026-05-21.md
```

---

## 4. Was NICHT im Commit ist

- Keine Bridge-Code-Änderung (V1-Test patcht datetime via monkeypatch).
- Keine Live-Engine-Touch.
- Keine LIFECYCLE_TRANSITIONS-Änderung.
- Keine ExecutableOrderIntent-Schema-Erweiterung.
- Keine paper_engine_singleton-Änderung.
- Kein fees.py/scale_resolver.py-Touch.
- Keine Settings-Validation-Lockerung für TTL (87600h-Vorschlag verworfen).

---

## 5. Test-Coverage-Stand nach V2

| Modul | Tests pre-V2 | Tests post-V2 |
|---|---|---|
| `tests/unit/test_paper_execution.py` | 56 | **62** (+6 partial-fill cases) |
| `tests/integration/test_premium_pipeline_e2e.py` | 0 | **2** (V1, NEU) |
| `tests/unit/test_envelope_to_paper_bridge.py` | 50 | 50 (unverändert) |
| `tests/unit/test_normalized_signal.py` | 54 | 54 (unverändert) |
| Total Test-Files Pi-relevant | 250 | **251** (+ test_premium_pipeline_e2e) |

---

## 6. Codex Anweisungen für nächste Sprints (V4-Backup-Spec, V6-Audit-Rotation, V8-Telegram-Trail)

Wenn ein neues ARBEITSPAKET (V4/V6/V8 oder Partial-Entry-Resting-Order) startet:

1. **Vor Code-Eingriff:** Spec-Memo von Claude Code oder Operator abwarten.
2. **PR-Body Pflicht-Sections** (Pin existiert: `feedback_pr_body_edit_no_ci_retrigger`):
   - Änderungsbericht (was, wo, warum)
   - Quality Gates (pytest, ruff, mypy mit konkreten Zahlen)
   - Risiken (was kann brechen)
   - Nächste TODOs (offene Folgepunkte)
   - Testbefehl (genau, reproduzierbar)
3. **Out-of-Scope explizit benennen** (was NICHT angefasst wird).
4. **Bei Spec-Drift:** im PR-Body explizit als „Spec-Drift, akzeptiert weil X" markieren.
5. **Settings-Validation:** wenn env-Werte vorgeschlagen werden, Range in `app/core/settings.py` prüfen + im PR zitieren.

---

## 7. Querverweise

- V1-Spec-Quelle: Audit-Output 2026-05-21 §4 (im Chat, vor diesem Memo).
- V2-Sign-off-Auftrag: ARBEITSPAKET V1 `naechster_folgeschritt`-Feld.
- ARCHITECTURE.md (root) `Bekannte Grenzen`-Tabelle (Zeile 1+2 mit diesem Commit aktualisiert).
- Pin: [[feedback-pr-body-edit-no-ci-retrigger]], [[feedback-multi-agent-main-worktree-ban]], [[feedback-worktree-not-safe-from-parallel-agents]].
- Folge-ARBEITSPAKET (Phase-5-Pre-Sprint): Resting-Order-Simulation für partial-entry-Fortsetzung. NICHT heute, NICHT diesen Sprint.
