# SPRINT_LEDGER.md

## Canonical Sprint Ledger (2026-03-24)

- current_phase: `PHASE 5 (active) -- Signal Reliability & Trust`
- phase_4_status: `CLOSED (D-87, 2026-03-24)`
- phase_5_status: `active -- PH5A frozen (§83)`
- current_sprint: `PH5A_BASELINE_RELIABILITY_AND_SIGNAL_TRUST`
- next_required_step: `PH5A_EXECUTION`
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

## Tech Sprints (nicht Phase-4-gebunden)

| Sprint | Date | Status | Outcome |
|---|---|---|---|
| N-1 MCP-Split | 2026-03-23 | **closed** | mcp_server.py 2471 → 334 Zeilen; tools/ Submodule; tests grün |
| N-2 gitignore | 2026-03-23 | **closed** | .hypothesis/ in .gitignore |
| N-3 MCP-Test-Migration | 2026-03-23 | **closed** | test_mcp_server.py 2447 + test_mcp_portfolio_read.py 138 → tests/unit/mcp/ (13 Module, 98 Tests, ruff clean) |
| Sprint 45 / N-4 V-4 Phase 3 | 2026-03-24 | **closed** | V-4 Dual-Write + DB-primary snapshot closed; baseline reconfirmed |
| N-5 DoD-Gate | 2026-03-23 | **closed** | Working-Tree-Gate in AGENTS.md §8 verankert |

## DoD-Gate (verbindlich ab sofort)

Ein Sprint gilt als **nicht abgeschlossen**, solange:
- `git status` uncommitted files zeigt
- `pytest` fehlschlägt (außer pre-existing DB-Setup-Fehler)
- `ruff check` Fehler meldet

Governance-Docs, Code und Tests müssen im selben Commit committiert werden.
Sprint-Closeout-Commits ohne sauberen Working Tree sind nicht zulässig.
