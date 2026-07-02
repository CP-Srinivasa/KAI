# Repo- & Workspace-Hygiene-Policy (Sprint S5, 2026-06-11)

Verbindliche Policy für Branches, Worktrees, Lint-Gate und Audit-Stream-Rotation.
Ziel: das Arbeitsumfeld bleibt klein genug, dass Parallel-Sessions nicht über
stale Branches/Worktrees stolpern (mehrfach dokumentierte Drift-Vorfälle).

## 1. Branch-Policy

**Zielzahlen:** < 15 aktive lokale Branches · < 5 Worktrees (inkl. Haupt-Checkout).

| Kategorie | Regel |
|---|---|
| PR **MERGED/CLOSED** | Branch lokal `-D` und remote löschen (GitHub kann via PR „Restore branch"). Squash-Merges werden über den PR-Status erkannt, nicht über `--merged`. |
| Tip ist Ancestor von p7 | löschen (Inhalt vollständig enthalten). |
| Backup-/Rescue-/Stale-Branches ohne PR | als Tag `archive/<name>_<datum>` konservieren, dann Branch löschen — **nichts geht verloren**, die Branch-Liste bleibt lesbar. |
| Aktive Worktree-Branches, p7, main, Branches mit OPEN PR | geschützt. |
| `origin/backup/*`-Refs | bewusste Remote-Archive — bleiben. |

**Worktrees:** Alter ≥ 6 Tage UND clean → entfernen (Branch bleibt!). Dirty →
Snapshot-Commit auf dem eigenen Branch (`chore(gc): snapshot …`), dann
entfernen. Alter ≤ 3 Tage → potenziell aktive Parallel-Session, nicht anfassen.
Nach jedem Sprint: eigenen Sprint-Worktree sofort entfernen.

Durchführung 2026-06-11: 36 Worktrees entfernt (5 davon mit Snapshot-Commit),
86 + 37 lokale Branches gelöscht/archiviert (→ 6), 34 Remote-Branches gelöscht.

## 2. Lint-Gate (Issue #172 — erledigt)

`ruff check .` und `ruff format --check .` laufen seit diesem Sprint über das
**volle Repo** (vorher nur `app/ tests/`) und sind hartes CI-Gate. Die in #172
gelisteten Altlasten (brand/, scripts/) sind gefixt. Neue Dateien — auch
Hilfsskripte — müssen das Gate bestehen; `# noqa` nur mit Begründung.

## 3. Audit-Stream-Rotation (archivierend)

`scripts/audit_rotate.py` + `kai-audit-rotate.timer` (täglich 04:40, default
**disabled**; Aktivierung = Operator-Schritt). Prinzip: **archivieren, nie
löschen** — oversized Streams wandern komplett nach `artifacts/archive/`,
das Live-File behält die letzten N Zeilen (TTL-Fenster-Konsumenten unberührt).
Ohne `--apply` ist jeder Lauf ein Dry-Run.

**Allowlist** (Stream · Schwelle · Tail · Begründung):
- `bridge_pending_orders.jsonl` · 20 MB · 20 000 — Bridge-Stage-Lookup + Trail brauchen das 24h-TTL-Fenster.
- `telegram_message_envelope.jsonl` · 20 MB · 20 000 — Pending-Scan honoriert 24h-TTL.
- `entry_watcher_audit.jsonl` · 20 MB · 10 000 — forensisch-rezent.

**Hart ausgeschlossen (NIEMALS rotieren):**
- `paper_execution_audit.jsonl` — die PaperExecutionEngine **replayed** dieses File bei jedem One-Shot zur State-Recovery; Rotation würde das Paper-Buch wipen.
- `blocked_outcomes.jsonl` — D-227 aggregiert die volle Historie.
- `shadow_candidate_ledger` / `bayes_*` / `alert_*` — Resolver-/Lern-Zustand braucht unresolved/Backfill-Historie.

Ein neuer Stream kommt NUR auf die Allowlist, wenn alle bekannten Konsumenten
höchstens ein rezentes Fenster lesen — Konsumenten-Audit im PR dokumentieren.

## 4. God-File-Ratchet (Sprint S7, D-234)

Die fünf God-Files (`telegram_bot.py` · `cli/main.py` · `orchestrator/trading_loop.py`
· `envelope_to_paper_bridge.py` · `core/settings.py`) dürfen nur **schrumpfen**.
CI-Gate `scripts/godfile_ratchet.py` gegen `scripts/godfile_baseline.json`:

- **Regel:** Wer ein God-File anfasst, extrahiert das berührte Segment in ein
  eigenes Modul **mit Tests** (Präzedenz: `app/execution/paper_entry_accounting.py`
  — dedupliziert zugleich die opening-fill-Definition von Loop und Route-Limiter).
- Wachstum über die Baseline = CI-Fail. Ausnahme nur über eine bewusste,
  im Diff sichtbare Baseline-Erhöhung, die der PR-Body rechtfertigen muss.
- Nach jeder Extraktion `python scripts/godfile_ratchet.py --update` (zieht die
  Baseline runter und verriegelt den Fortschritt; --update erhöht NIE).
- Kein Big-Bang-Refactor: Abbau über Monate, opportunistisch beim Anfassen.

## 5. MEMORY.md (Session-Gedächtnis)

Index-Zeilen ≤ ~200 Zeichen, ein Eintrag pro Memory, Details gehören in die
Topic-Files. Limit 24 KB — Überschreitung degradiert nachweislich die
Session-Kontinuität (Vorfall 2026-06-11: stale Pi-Stand im Kontext).
