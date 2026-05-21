# ONBOARDING.md — KAI / AI-Analyst-Trading-Bot

**Stand:** 2026-05-21 · **Zielgruppe:** menschliche Zweit-Bearbeiter + neue KI-Agenten

Dieses Dokument bringt einen neuen Bearbeiter in **2-3 Stunden** auf einsatzfähiges Wissen. Es ersetzt nicht die Tiefendokumentation — es ist der Einstiegspunkt.

---

## 0. Vor dem ersten Commit lesen

| # | Quelle | Warum |
|---|---|---|
| 1 | `README.md` | Stack + Quick-Start + tägliche Operator-Commands |
| 2 | `ARCHITECTURE.md` | Tragende Strukturen + 16-State-Lifecycle + bekannte Grenzen |
| 3 | `ONBOARDING.md` (diese Datei) | Setup + erste Tasks + Verbote |
| 4 | `docs/AI_HANDOFF.md` | Multi-Agent-Disziplin (Claude/Claude-Code/Codex/Antigravity-Rollen, Worktree-Isolation, V→V-Token, 9 dokumentierte Drift-Vorfälle) |
| 5 | `DECISION_LOG.md` | Decision-History (kompakt, 29KB) |
| 6 | `docs/adr/0001..0004` | Architektur-Entscheidungen (4 ADRs) |
| 7 | `docs/architecture/signal_to_execution_implementation_report_20260510.md` | Pipeline-State + was wirklich gefixt wurde |
| 8 | `artifacts/operator_memos/re_entry_end_of_window_2026-05-23.md` | Aktueller Re-Entry-Stand |
| 9 | Letzte Session-Pin im Memory-Store (`session_pin_next_start_*.md`) | Übergabe-Stand der letzten Session |

**Reihenfolge ist verbindlich.** Wer bei #7 anfängt, versteht das WAS aber nicht das WARUM.

---

## 1. Setup (Workstation)

```bash
# Python 3.12 venv
python -m venv .venv
source .venv/bin/activate   # bash | für Windows: .venv\Scripts\activate

# Dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# .env aus .env.example kopieren + Secrets eintragen
# (oder von Operator anfordern — Pi-`.env` ist NICHT in Git)

# Test-Suite
pytest                                    # 250 Test-Files
pytest tests/integration/ -v              # E2E-Tests
pytest tests/unit/test_paper_execution.py # paper_engine focus

# Lint + Type
ruff check app/ tests/
mypy app/                                 # sofern im Projekt üblich
```

**Pi-Zugang** (für Operator-Smoke + Service-Status): `ssh ubuntu@192.168.178.23`, Working-Dir `/home/kai/ai_analyst_trading_bot` (Symlink auf `/home/ubuntu/`).

---

## 2. Wichtigste Startdateien (für orientierung)

| Datei | Was hier passiert |
|---|---|
| `app/messaging/signal_parser.py` | Telegram-Text → ParsedSignal |
| `app/execution/normalized_signal.py:127` | 16-State-Lifecycle SSoT |
| `app/execution/envelope_to_paper_bridge.py` | Gates + Bridge-Logik |
| `app/execution/paper_engine.py` | Paper-Order-Execution |
| `app/execution/order_intent.py` | ExecutableOrderIntent-Vertrag |
| `app/execution/entry_watcher.py` | Deterministisches Entry-Polling |
| `app/observability/premium_signal_trail.py` | Trail-API + End-to-End-Forensik |
| `app/alerts/auto_annotator.py` | Outcome-Klassifikation (Lernschicht) |
| `app/api/main.py` | FastAPI-Server-Einstieg |
| `app/cli/main.py` | CLI-Commands (operator-Tools) |
| `docs/adr/` + `docs/architecture/` | Decision + Architektur-Doku |
| `artifacts/operator_memos/` | Laufende Operator-Entscheidungen |

---

## 3. Erste 3 Tasks für neue Bearbeiter

### Task 1: Trail-API curlen (Read-only, ~15min)

```bash
ssh ubuntu@192.168.178.23 \
  'curl -sS -H "Authorization: Bearer $(grep APP_API_KEY /home/kai/ai_analyst_trading_bot/.env | cut -d= -f2)" \
   "http://localhost:8000/api/premium-signals/trail?limit=10" | python3 -m json.tool | head -50'
```

**Ziel:** Erstes Verständnis von Pipeline-Stages + Audit-Trail pro Signal. Wenn du das verstehst, hast du die Pipeline-Mechanik geblickt.

### Task 2: Aktuelles Daily-Strategy-File lesen (~20min)

```bash
ssh ubuntu@192.168.178.23 'cat /home/kai/ai_analyst_trading_bot/artifacts/daily_strategy/$(date +%Y-%m-%d).md'
```

**Ziel:** Aktuelle Tagespriorität, offene Carry-overs, P0-Sprints verstehen.

### Task 3: Tests gegen Pipeline laufen lassen (~30min)

```bash
pytest tests/unit/test_envelope_to_paper_bridge.py -v   # 50 Tests
pytest tests/unit/test_paper_execution.py -v            # 56 Tests
pytest tests/unit/test_normalized_signal.py -v          # 54 Tests
```

**Ziel:** Test-Patterns kennenlernen, fixture-Konventionen, monkeypatch-Stil. Das ist die Vorlage für jeden eigenen Test.

---

## 4. Disziplin (verbindlich, nicht verhandelbar)

### Worktree-Isolation Pflicht

Bei ≥2 parallelen Sessions im selben Repo:
```bash
git worktree add ../kai-meintrack-yymmdd <branch>
```
Memory-Pin `feedback_multi_agent_main_worktree_ban` dokumentiert 4 Drift-Vorfälle aus 2026-05-10 bis 2026-05-14, die ALLE durch Parallel-Sessions im Hauptworktree ausgelöst wurden.

### Vor Code-Eingriff: ARBEITSPAKET

Format aus dem `kai-master-coding-regeln`-Skill. Wer im Hauptworktree ohne ARBEITSPAKET an `paper_engine.py` / `envelope_to_paper_bridge.py` / `normalized_signal.py` rumeditiert, verletzt KAI-Master-Regeln.

### Verbote (verbindlich)

- ❌ **Kein Rewrite** von paper_engine, signal_parser, Bridge oder Lifecycle. Architektur ist tragfähig (gap-analysis 2026-05-10 + implementation-report).
- ❌ **Keine zweite parallele State-Machine** zu `LIFECYCLE_TRANSITIONS`.
- ❌ **Kein Touch auf `app/execution/live_engine.py` / `live_caps.py`** ohne explizite Operator-Freigabe.
- ❌ **Kein SHADOW_ONLY-Flip** vor Heuristik [[kai-bayes-shadow-only-flip-heuristik]] (n≥20 ODER ≥4 Wochen + Diversität + Sentinel + Rollback).
- ❌ **Keine `EXECUTION_PAPER_MIN_PRIORITY`-Gate-ADRs** vor 2026-05-30 (Operator-Sign-off Option D ratifiziert, siehe `priority_scoring_decision_brief_2026-05-23.md`).
- ❌ **Keine echten API/Market-Data/Exchange-Calls in Tests.** monkeypatch + tmp_path Pflicht.
- ❌ **Kein Push trotz STOP**, kein force-push auf p7 (Memory-Pin `feedback_antigravity_bypass_pattern`).

### Vor jedem Commit prüfen

```bash
# 1. Lokal
git status
git diff --stat

# 2. Pi-State (falls Pi-relevant)
ssh ubuntu@192.168.178.23 'cd /home/kai/ai_analyst_trading_bot && git status && git log --oneline -3'

# 3. Diff-Plausibilität vor Sign-off (Memory: feedback_diff_plausibility_pre_merge)
git fetch origin <base-branch>
git log origin/<base-branch>..HEAD --stat
```

### Memory-Zitate immer durch Live-Check verifizieren

Memory-Pins dokumentieren WAR-Zustände. Vor jeder Aktion auf Basis eines Memory-Zitats: Pi-/Repo-Live-Check. Pin `feedback_memory_zitat_vor_live_check` dokumentiert 14.05.-Fehlinterpretation („Channel tot" war Cold-Boot).

---

## 5. Branch- und PR-Disziplin

**Aktiver Branch:** `claude/p7/reentry-ia-codex-cycle` (long-running Re-Entry-Branch).

**PR-Pflicht-Sections** (im PR-Body):
- Änderungsbericht
- Quality Gates (`pytest`, `ruff`, `mypy`)
- Risiken
- Nächste TODOs
- Testbefehl

Memory-Pin `feedback_pr_body_edit_no_ci_retrigger`: `gh pr edit --body` triggert KEINE CI-Re-Runs. Wenn Body editiert wird → empty-commit + push als Workaround.

**Pi kann nicht selbst pushen** (`feedback_pi_remote_push_credential_pattern`). Push-Pfad: Pi committet → Workstation fetched + push.

---

## 6. Was tun bei...

| Symptom | Erstes Werkzeug | Dann |
|---|---|---|
| „X funktioniert nicht" | `ssh ubuntu@... 'systemctl status kai-*'` | `journalctl -u kai-server.service --since "1 hour ago"` |
| „UI-Fix nicht sichtbar" | Pi-dist-Hash check (Pin `feedback_pi_deploy_dist_drift_check`) | dann erst Code |
| „Memory-Stand stimmt nicht" | Live-Check vor Memory-Zitat | Memory updaten/ablegen |
| „Test bricht in CI grün in lokal rot" | branch-base-staleness check (Pin `feedback_worktree_base_staleness_risk`) | rebase + diff-plausibility |
| Parallel-Agent-Drift | Worktree-Isolation rückwirkend einrichten | Pin `feedback_drift_awareness_worktree_isolation` |

---

## 7. Phasen-Kontext (kurz)

- **Phase 0** Strategische Bereinigung + Security: ABGESCHLOSSEN (5 PRs gemerged 2026-05-13).
- **Phase 1** Stabilisierter produktnaher Paper-Betrieb: AKTIV. Re-Entry seit 2026-05-16. EOW-Review 2026-05-23 = Validierungs-Sitzung, Decision-Pflicht verschoben auf 2026-05-30.
- **Phase 2** Operator-Usability + Web-UI: TEIL-AKTIV (Premium-Trail-Panel live, DALI-Tracks gemerged).
- **Phase 3** Architekturhärtung: TEIL-AKTIV (DuckDB-Pivot Phase 0+1 merged).
- **Phase 4** Intelligenz-/Wissensausbau: VORBEREITET (V1-V4.1 Lern-Module deployed, atmen aber dünn).
- **Phase 5** Shadow-/Live-Readiness: NICHT AKTIV. Frühestens Q3 nach Phase 1-4 grün.

**Aktueller Sprint-Fokus (DS-20260521):**
- V1 Premium-Pipeline-E2E-Test (Codex aktiv)
- V5 Auto-Annotate Pipeline-A reaktiviert
- V3 ARCHITECTURE.md + ONBOARDING.md (dieses Dokument)
- V7 AI_HANDOFF.md (folgt)

---

## 8. Wenn du nicht weiterkommst

1. **Memory-Pin-Search** im `~/.claude/projects/.../memory/`-Verzeichnis (Stichwort der Frage).
2. **DECISION_LOG.md grep** nach Pattern.
3. **Operator fragen** mit konkretem Kontext (File/Funktion/Zeile, nicht abstrakt).
4. **Daily-Strategy-File** vom aktuellen Tag — dort steht oft die aktuelle Antwort.

**NICHT:**
- Architektur erweitern „weil interessant".
- Code refactoren ohne ARBEITSPAKET.
- Tests skippen weil sie unbequem sind.
- Live-Mode-Code anfassen.

---

## Verweise

- `ARCHITECTURE.md` (parallel zu diesem Dokument, ~250 Zeilen)
- `docs/adr/` — 4 ADRs
- `docs/architecture/` — Sprint-Reports + Gap-Analysen
- `DECISION_LOG.md` — kompakter Verlauf
- `artifacts/operator_memos/` — laufende Decisions
- `artifacts/daily_strategy/YYYY-MM-DD.md` — Tagesübersicht
- `~/.claude/projects/.../memory/MEMORY.md` — Memory-Index
