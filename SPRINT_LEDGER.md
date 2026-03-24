# SPRINT_LEDGER.md

## Canonical Sprint Ledger (2026-03-24)

- phase_4_status: `active (technical stabilization complete)`
- current_sprint: `open — PH4L definition or Phase 4 closeout`
- last_closed_sprint: `SPRINT_45_V4_DB_PRIMARY_PORTFOLIO_SNAPSHOT (closed D-86, §81)`
- next_required_step: `PH4L definition or Phase 4 closeout decision`
- baseline: `1604 passed, ruff clean, mypy 0 errors`

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
| Sprint 45 / N-4 V-4 Phase 3 | 2026-03-24 | **closed** | session_factory refactor; PortfolioStateRecord dual-write + DB-primary snapshot; 14 neue Tests; 1604 passed |
| N-5 DoD-Gate | 2026-03-23 | **closed** | Working-Tree-Gate in AGENTS.md §8 verankert |

## DoD-Gate (verbindlich ab sofort)

Ein Sprint gilt als **nicht abgeschlossen**, solange:
- `git status` uncommitted files zeigt
- `pytest` fehlschlägt (außer pre-existing DB-Setup-Fehler)
- `ruff check` Fehler meldet

Governance-Docs, Code und Tests müssen im selben Commit committiert werden.
Sprint-Closeout-Commits ohne sauberen Working Tree sind nicht zulässig.
