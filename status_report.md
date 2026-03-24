# KAI Platform — Status Report

**Stand:** 2026-03-24
**Branch:** `claude/p6-audit/architectural-invariants`

---

## Governance-Zustand

| Feld | Wert |
|---|---|
| current_phase | `PHASE 5 (active) -- Signal Reliability & Trust` |
| current_sprint | `PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST (results-review D-89, §83)` |
| next_required_step | `PH5A_RESULTS_REVIEW_AND_CLOSE` |
| phase_4_status | `CLOSED (D-87, 2026-03-24) — §82 frozen anchor` |
| baseline | `1610 passed, ruff clean, mypy 0 errors` |

---

## Technischer Zustand

| | |
|---|---|
| Tests | **1610 passed** |
| ruff | clean (E501 für `scripts/*.py` via `pyproject.toml` ausgenommen) |
| mypy | 0 Fehler |
| Working Tree | sauber |

---

## PH5A Ergebnisse (Execution 2026-03-24)

| Metrik | Wert | Interpretation |
|---|---|---|
| Fallback rate | **0.0%** (0/69) | Pipeline vollständig funktional |
| LLM error proxy rate | **27.5%** (19/69) | Hauptbefund: 19 Docs mit priority=1, relevance=0, scope=unknown |
| Provider distribution | openai 100% | Single-provider Shadow Run |
| Priority mean | **3.96 / 10** | High≥7: 15, Mid 4–6: 23, Low≤3: 31 |
| Keyword coverage | **62.3%** (43/69) | 26 Docs noch zero-hit |
| Tag fill rate | **100%** (69/69) | Phase 4 vollständig |
| Watchlist overlap | **52.2%** (36/69) | Stark korreliert mit hoher Priority |
| Actionable rate (Tier3) | **0.0%** (0/69) | I-13 bestätigt |

### Priority Distribution

| Score | Anzahl |
|---|---|
| 1 | 27 |
| 2–3 | 4 |
| 4–6 | 23 |
| 7–9 | 13 |
| 10 | 1 |

### Hauptbefund: LLM Error Proxy 27.5%

19/69 Dokumente erhalten die Signatur `priority=1 + relevance=0 + scope=unknown`. Das ist kein Parse-Fehler — der LLM antwortet, aber produziert ein "Nichts-Nützliches"-Ergebnis. Mögliche Ursachen:
- Irrelevanter Content (Non-Crypto, Non-Finance)
- Zu kurze/fragmentierte Dokumente
- Provider-Response ohne strukturiertes Ergebnis

**Artefakte:**
- `artifacts/ph5a_reliability_baseline.json`
- `artifacts/ph5a_operator_summary.md`

---

## PH5A Aufgaben-Stand

| Task | Status |
|---|---|
| PH5A-1 Diagnostik-Skript | ✅ `scripts/ph5a_reliability_baseline.py` |
| PH5A-2 Fallback rate, LLM error rate, Provider | ✅ |
| PH5A-3 Priority distribution, Actionable rate | ✅ |
| PH5A-4 Keyword coverage, Tag fill rate | ✅ |
| PH5A-5 `artifacts/ph5a_reliability_baseline.json` | ✅ |
| PH5A-6 `artifacts/ph5a_operator_summary.md` | ✅ |
| PH5A-7 Governance-Docs + Sprint schließen | ☐ (pending) |

---

## Offene Risiken

| ID | Beschreibung | Status |
|---|---|---|
| R-PH5-001 | Phase 5 könnte zu breit werden | mitigation: PH5A ist diagnostic-only |
| R-PH5-002 | LLM error proxy 27.5% — root cause unklar | open → PH5B Kandidat |
| E-1 | Externe Key-Rotation | ✅ Geschlossen (2026-03-22) |

---

_Dieses Dokument wird nach jedem Sprint-Abschluss aktualisiert._
