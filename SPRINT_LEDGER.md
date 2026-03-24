ÃƒÂ¯Ã‚Â»Ã‚Â¿# SPRINT_LEDGER.md





## Canonical Sprint Ledger (2026-03-24)





- current_phase: `PHASE 5 (active)`


- phase_4_status: `CLOSED (D-87, 2026-03-24)`


- phase_5_status: `active -- PH5B closed (accepted); PH5C governance reconciliation active`


- current_sprint: `PH5C_GOVERNANCE_RECONCILIATION`


- next_required_step: `PH5C_STATUS_FREEZE`


- baseline: `1609 passed, ruff clean`





| Sprint | Date | Status | Outcome |


|---|---|---|---|


| PH4A | 2026-03-22 | closed | Baseline: paired=0 |


| PH4B | 2026-03-23 | closed | paired=69, MAE 3.13. Keyword blindness |


| PH4C | 2026-03-23 | closed | 42% zero-hit. Gaps: macro/regulatory/AI |


| PH4D | 2026-03-23 | closed | Keywords +56. Zero-hit 42%->37.7% |


| PH4E | 2026-03-23 | closed | relevance 41.2% of gap. Defaults by design |


| PH4F | 2026-03-23 | closed | Fallback path. 65% weight hardcoded |


| PH4G | 2026-03-23 | closed | Relevance floor applied. Actionable blocked (I-13) |


| PH4H | 2026-03-23 | closed | Option B: I-13 permanent, actionable=LLM-only |


| PH4I | 2026-03-23 | closed | market_scope enrichment complete |


| PH4J | 2026-03-23 | closed | Tags enrichment: keyword-hit 4->7, zero-hit 1->4, assets-only 0->4 |


| PH4K | 2026-03-24 | **closed** | Utility review: watchlist overlap 52%, corr=0.56, priority delta +3.1 |


| PH5A | 2026-03-24 | closed | Reliability baseline: fallback=0%, LLM-error-proxy=27.5%, tag-fill=100%, keyword-cov=62.3% |


| PH5B | 2026-03-24 | **closed** | Findings accepted: 19/19 proxy cases = EMPTY_MANUAL; root cause placeholder content |


| PH5C | 2026-03-24 | **active** | Governance reconciliation: harmonize docs, unify baseline, freeze status before execution |


## Tech Sprints (nicht Phase-4-gebunden)





| Sprint | Date | Status | Outcome |


|---|---|---|---|


| N-1 MCP-Split | 2026-03-23 | **closed** | mcp_server.py 2471 ÃƒÂƒÃ‚Â¢ÃƒÂ¢Ã‚Â€Ã‚Â ÃƒÂ¢Ã‚Â€Ã‚Â™ 334 Zeilen; tools/ Submodule; tests grÃƒÂƒÃ‚ÂƒÃƒÂ‚Ã‚Â¼n |


| N-2 gitignore | 2026-03-23 | **closed** | .hypothesis/ in .gitignore |


| N-3 MCP-Test-Migration | 2026-03-23 | **closed** | test_mcp_server.py 2447 + test_mcp_portfolio_read.py 138 ÃƒÂƒÃ‚Â¢ÃƒÂ¢Ã‚Â€Ã‚Â ÃƒÂ¢Ã‚Â€Ã‚Â™ tests/unit/mcp/ (13 Module, 98 Tests, ruff clean) |


| Sprint 45 / N-4 V-4 Phase 3 | 2026-03-24 | **closed** | V-4 Dual-Write + DB-primary snapshot closed; baseline reconfirmed |


| N-5 DoD-Gate | 2026-03-23 | **closed** | Working-Tree-Gate in AGENTS.md ÃƒÂƒÃ‚Â‚Â§8 verankert |


| N-6 MCP-Compat-Extract | 2026-03-24 | **closed** | compat.py aus mcp_server.py extrahiert; 0 inline @mcp.tool(); test_canonical_read + test_guarded_write auf mcp.list_tools() umgestellt |


| N-7 Alert-Integration | 2026-03-24 | **closed** | analyze-pending Phase 4: AlertService.process_document() + --no-alerts Flag + 3 Tests; fail-open |


| N-8 CI-Hardening | 2026-03-24 | **closed** | hypothesis + pytest-mock in dev-deps; bandit B324 (SHA1 usedforsecurity=False); codecov@v5; FORCE_NODE24; ruff format; doppeltes asyncio.run entfernt |





## DoD-Gate (verbindlich ab sofort)





Ein Sprint gilt als **nicht abgeschlossen**, solange:





- `git status` uncommitted files zeigt


- `pytest` fehlschlÃƒÂƒÃ‚ÂƒÃƒÂ‚Ã‚Â¤gt (auÃƒÂƒÃ‚ÂƒÃƒÂ…Ã‚Â¸er pre-existing DB-Setup-Fehler)


- `ruff check` Fehler meldet





Governance-Docs, Code und Tests mÃƒÂƒÃ‚ÂƒÃƒÂ‚Ã‚Â¼ssen im selben Commit committiert werden.


Sprint-Closeout-Commits ohne sauberen Working Tree sind nicht zulÃƒÂƒÃ‚ÂƒÃƒÂ‚Ã‚Â¤ssig.







