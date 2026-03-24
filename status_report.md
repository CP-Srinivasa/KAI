# KAI Platform â Status Report

**Stand:** 2026-03-24
**Branch:** `claude/p6-audit/architectural-invariants`
**Sprint:** PH5B_LOW_SIGNAL_CLUSTER_ANALYSIS (active D-92, §84)

---

## Governance-Zustand

| Feld | Wert |
|---|---|
| current_phase | `PHASE 5 (active) -- Signal Reliability & Trust` |
| current_sprint | `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)` |
| next_required_step | `STRATEGIC_HOLD -- no new sprint until alert-precision + paper-trading positive` |
| phase_4_status | `CLOSED (D-87, 2026-03-24) â §82 frozen anchor` |
| baseline | `1619 passed, ruff clean, mypy 0 errors` |

---

## Technischer Zustand

| | |
|---|---|
| Tests | **1619 passed** |
| ruff | clean (E501 fÃ¼r `scripts/*.py` via `pyproject.toml` ausgenommen) |
| mypy | 0 Fehler |
| Working Tree | sauber |

---

## PH5A Ergebnisse (Execution 2026-03-24)

| Metrik | Wert | Interpretation |
|---|---|---|
| Fallback rate | **0.0%** (0/69) | Pipeline vollstÃ¤ndig funktional |
| LLM error proxy rate | **27.5%** (19/69) | Hauptbefund: 19 Docs mit priority=1, relevance=0, scope=unknown |
| Provider distribution | openai 100% | Single-provider Shadow Run |
| Priority mean | **3.96 / 10** | Highâ¥7: 15, Mid 4â6: 23, Lowâ¤3: 31 |
| Keyword coverage | **62.3%** (43/69) | 26 Docs noch zero-hit |
| Tag fill rate | **100%** (69/69) | Phase 4 vollstÃ¤ndig |
| Watchlist overlap | **52.2%** (36/69) | Stark korreliert mit hoher Priority |
| Actionable rate (Tier3) | **0.0%** (0/69) | I-13 bestÃ¤tigt |

### Priority Distribution

| Score | Anzahl |
|---|---|
| 1 | 27 |
| 2â3 | 4 |
| 4â6 | 23 |
| 7â9 | 13 |
| 10 | 1 |

### Hauptbefund: LLM Error Proxy 27.5%

19/69 Dokumente erhalten die Signatur `priority=1 + relevance=0 + scope=unknown`. Das ist kein Parse-Fehler â der LLM antwortet, aber produziert ein "Nichts-NÃ¼tzliches"-Ergebnis. MÃ¶gliche Ursachen:
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
| PH5A-1 Diagnostik-Skript | â `scripts/ph5a_reliability_baseline.py` |
| PH5A-2 Fallback rate, LLM error rate, Provider | â |
| PH5A-3 Priority distribution, Actionable rate | â |
| PH5A-4 Keyword coverage, Tag fill rate | â |
| PH5A-5 `artifacts/ph5a_reliability_baseline.json` | â |
| PH5A-6 `artifacts/ph5a_operator_summary.md` | â |
| PH5A-7 Governance-Docs + Sprint schlieÃen | â (pending) |

---

## Offene Risiken

| ID | Beschreibung | Status |
|---|---|---|
| R-PH5-001 | Phase 5 kÃ¶nnte zu breit werden | mitigation: PH5A ist diagnostic-only |
| R-PH5-002 | LLM error proxy 27.5% â root cause unklar | open â PH5B Kandidat |
| E-1 | Externe Key-Rotation | â Geschlossen (2026-03-22) |

---

_Dieses Dokument wird nach jedem Sprint-Abschluss aktualisiert._
