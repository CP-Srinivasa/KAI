


## Current State (2026-03-24)


| Field | Value |


|---|---|


| current_phase | `PHASE 5 (active)` |


| current_sprint | `PH5C_FILTER_BEFORE_LLM_BASELINE (closed D-97)` |


| next_required_step | `STRATEGIC_HOLD -- no new feature-work until alert-hit-rate is calculable on >=50 resolved directional alerts` |


| ph4a_status | `closed (D-53) -- immutable baseline anchor (S67)` |


| ph4b_status | `closed (D-62) -- paired_count=69; root cause: keyword coverage blindness` |


| ph4c_status | `closed -- rule-keyword gap audit; top-3 gaps: macro, regulatory, AI` |


| ph4d_status | `closed (D-68) -- 56 keywords added; zero-hit 42%->37.7%; S71 frozen anchor` |


| ph4e_status | `closed (D-70) -- relevance 41.2% of gap; root cause: defaults by design` |


| ph4f_status | `closed (D-69) -- fallback path identified; actionable missing 69/69; market_scope unknown 69/69` |


| ph4g_status | `closed (D-68/69) -- relevance floor applied; actionable reverted (I-13); S75 frozen anchor` |


| ph4h_status | `closed (D-74/75) -- policy decision: actionable=LLM-only; I-13 confirmed permanent; S76 frozen anchor` |


| ph4i_status | `closed (D-78) -- market_scope enrichment complete; S77 frozen anchor` |


| ph4j_status | `closed -- tags enrichment: keyword-hit 4->7, zero-hit 1->4, assets-only 0->4` |


| ph4k_status | `closed (D-84) -- utility review complete; S79 frozen anchor` |


| v4_dual_write_status | `closed (D-86) -- N-4 closed` |


| baseline | `1039 passed, ruff clean, mypy 0 errors` |


| working_tree | `clean` |


| cli_canonical_count | 53 |


| provisional_cli_count | 0 |


| phase3_status | `closed (2026-03-22) -- GO` |


| phase4_status | `CLOSED (D-87, 2026-03-24) -- 11 sprints PH4A-PH4K + V-4; canonical closeout complete` |


| ph5a_status | `closed (D-91) -- reliability baseline established` |


| ph5b_status | `closed (accepted) -- EMPTY_MANUAL confirmed root cause; no model failure` |


| ph5c_status | `closed (D-97) -- strategic hold active; no new companion-ML sprint/decision/invariant` |


| phase5_status | `HOLD -- PH5C closed (D-97); feature-work blocked until alert-hit-rate is calculable on >=50 resolved directional alerts` |


| production_limits | `D-101 -- Priority MAE=3.13 and LLM-Error-Proxy=27.5% are accepted production metrics; improve via operation and real data, not internal sprints` |


| tier1_fallback_policy | `D-104 -- I-13 remains permanent: actionable=0 in Tier1/keyword fallback; signal quality focus stays on LLM-driven alerts` |


| thirty_day_gate | `D-105 -- review on 2026-04-23 after a real 7-day ingestion run with LLM analysis; if alert_audit.jsonl has <5 triggered alerts or alert precision <30%, stop trading-signal work and focus on data quality (feeds, keywords, spam-filter), no new architecture` |


| living_architecture | `D-106 -- active architecture is CLAUDE.md + docs/contracts.md (slim); all other docs are historical in docs/archive/` |


---> **Verbindliches Betriebsdokument fuer alle Coding-Agenten.**


> Claude Code -- OpenAI Codex -- Google Antigravity


> Dieses Dokument lesen, bevor eine einzige Zeile Code angefasst wird.

## Documentation Policy (D-99, 2026-03-24)

- Neue Sprint-Contract-Dokumente sind gestoppt (keine neuen `docs/sprint*_contract*.md` und keine neuen Sprint-Sections als Primärquelle).
- Entscheidungen werden nur noch dokumentiert als:
  - kurzer Code-Kommentar direkt am geänderten Verhalten, oder
  - kompakter 3-Zeilen-Eintrag in `DECISION_LOG.md`.
- `docs/contracts.md` bleibt kanonisch; historische Vertragsdoku liegt unter `docs/archive/contracts_archive.md`.
