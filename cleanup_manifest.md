# Cleanup Manifest — 2026-06-02 (Premium-Signal-Pipeline-Hardening Sprint)

**Status: PROPOSAL ONLY. Nichts wurde automatisch gelöscht.** Dieses Manifest
listet Kandidaten mit Pfad, Grund und Sicherheitsbeweis. Löschung ist eine
explizite Operator-Aktion. Grundsatz: nur **sicher regenerierbare** Daten; keine
DBs, Secrets, Sessions, Live-Audits oder ungesicherten Logs.

## SAFE — regenerierbar, jederzeit löschbar

| Pfad | Größe | Grund | Sicherheitsbeweis |
|---|---|---|---|
| `.mypy_cache/` | ~200M | mypy-Inkrement-Cache | Wird bei nächstem `mypy`-Lauf neu erzeugt; kein Quellinhalt. |
| `.pytest_cache/` | ~387K | pytest-Lastlauf-Cache | Regeneriert bei nächstem `pytest`. |
| `.ruff_cache/` | ~354K | ruff-Cache | Regeneriert bei nächstem `ruff`. |
| `__pycache__/` (74 dirs) | — | Python-Bytecode | Aus `.py` regeneriert. `find . -type d -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +` |

Sichere Bereinigung (idempotent):
```bash
rm -rf .mypy_cache .pytest_cache .ruff_cache
find . -type d -name __pycache__ -not -path './.git/*' -prune -exec rm -rf {} +
```

## REQUIRES OPERATOR REVIEW — nicht automatisch anfassen

| Pfad | Grund | Auflage |
|---|---|---|
| 36 git-Worktrees (`git worktree list`) | Viele alte Sprint-Worktrees; einige evtl. merged/stale. | Pro Worktree erst `git -C <wt> status` + Merge-Status gegen Integrationsbranch prüfen. **Windows:** `node_modules`-Locks → `worktree remove --force` kann fehlschlagen (siehe Memory). |
| **`/c/tmp/kai-premium-ledger-sprint-20260531`** | **16 uncommittete Codex-Dateien** (telegram_channel_*, bridge, premium_event_store etc.). | **NICHT LÖSCHEN/BEREINIGEN.** Backup liegt unter `KAI-mirror/sprint-backups/20260602_premium_hardening/`. Erst nach Commit/Merge durch Eigentümer. |

## NEVER DELETE — Trust-Boundary / Live-State

- `.env`, `.env.backup.*` — Secrets (u.a. Approval-HMAC). Nur rotieren, nie löschen.
- `*.session` / Telethon-Session — würde Re-Auth + AuthKeyDuplicated-Risiko auslösen.
- `artifacts/*.jsonl` — Live-Audit-Trails (paper_execution_audit, bridge_pending_orders,
  telegram_message_envelope, alert_audit …). Tamper-evidente Spur. Höchstens **rotieren/archivieren**, nie löschen.
- `artifacts/analytics.duckdb` — operative DB.
- `monitor/` — Operator-kuratierte Quell-/Watchlist-Definitionen (D-181 ACL-Trust-Boundary).

## Archivierung statt Löschung (für große Logs)
Wenn `artifacts/*.jsonl` zu groß werden: rotieren mit Zeitstempel-Suffix
(`*.jsonl.YYYYMMDD`) + gzip, Original-Pfad neu anlegen. Niemals truncaten,
solange ein Prozess schreibt (Lockfiles `*.jsonl.lock` beachten).
