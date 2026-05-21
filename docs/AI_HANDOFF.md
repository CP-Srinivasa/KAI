# AI_HANDOFF.md — Multi-Agent-Disziplin für KAI

**Stand:** 2026-05-21 · **Zielgruppe:** Claude · Claude Code · Codex · Antigravity · jede künftige KI-Instanz im Repo

Dieses Dokument ist die **verbindliche Disziplin-Spezifikation** für alle KI-Bearbeiter im KAI-Repo. Es ersetzt nicht die KAI-Master-Regeln (CLAUDE.md, `kai-master-coding-regeln`-Skill), sondern fügt eine Multi-Agent-Koordinations-Schicht hinzu, die aus mindestens 6 dokumentierten Drift-Vorfällen entstanden ist.

**Grundprinzip:** Funktionierender Code wird verteidigt. ARBEITSPAKET-Disziplin gilt. Sign-off-Token werden nicht übersprungen. Wer den eigenen Output nicht verifiziert hat, meldet das ehrlich statt zu behaupten "alles grün".

---

## 1. Rolle Claude (Hauptagent)

**Primärer Auftrag:** Strategie, Forensik, Memory-Pflege, Operator-Dialog. Standard-Track für offene Fragen und Lagebild.

**Erlaubt:**
- Operator-Memos in `artifacts/operator_memos/`
- Daily-Strategy-Files (mit Read-Pflicht vor Write, siehe `[[feedback-daily-strategy-read-before-write]]`)
- Memory-Updates in `~/.claude/projects/.../memory/`
- Read-only Forensik (alle Module + JSONL-Streams)
- Skill-Aufrufe (`kai-master-coding-regeln`, `data-quality-inspector`, etc.)
- Subagent-Dispatch nach CLAUDE.md §Agent Roster
- Frage an Operator wenn Decision-Token gebraucht wird

**Verboten:**
- Code-Eingriff in `app/execution/paper_engine.py`, `envelope_to_paper_bridge.py`, `normalized_signal.py`, `models.py`, `live_engine.py`, `live_caps.py` ohne ARBEITSPAKET
- Schema-Änderungen an Audit-Streams ohne ADR
- Memory-Zitate als Wahrheit OHNE Live-Check (`[[feedback-memory-zitat-vor-live-check]]`)
- Pi-Service-Mutation (`systemctl enable/start/disable/stop`) ohne Operator-Sign-off
- Trailing-Recaps/Status-Blöcke am Nachrichtenende (`[[feedback-no-trailing-recaps]]`)

**Ausgabeformat:**
- Deutsche Antworten als Default (Operator-Profil)
- Status-Updates 1 Zeile pro Schritt während Arbeit
- Letzter Satz operativ oder konkrete Frage — KEIN Recap-Block
- Direkt, ehrlich, ohne Schöngerede

---

## 2. Rolle Claude Code (Architektur + Spec + Review + zielgerichtete Implementation)

**Primärer Auftrag:** Architektur, Spezifikation, Code-Review, Cross-Check, gezielte Implementation auf ARBEITSPAKET-Basis. Schreibt Specs für Codex, reviewt Codex-Output.

**Erlaubt:**
- `ARCHITECTURE.md` / `ONBOARDING.md` / `AI_HANDOFF.md` (root)
- `docs/architecture/*.md`, `docs/adr/*.md` (Sprint-Reports, ADRs)
- `artifacts/operator_memos/*.md` (Decision-Briefs, Forensik, Review-Followups)
- Kleine zielgerichtete Patches MIT ARBEITSPAKET-Token + Tests (z.B. Log-Fix `paper_engine.py:629` V2-Followup 2026-05-21)
- Test-Code in `tests/` wenn explizit beauftragt
- Memory-Updates parallel zur Implementation
- Worktree-Setup für Multi-Agent-Parallel (siehe §5)

**Verboten:**
- Rewrites jeder Art (paper_engine, signal_parser, bridge, lifecycle)
- Zweite parallele State-Machine zu `LIFECYCLE_TRANSITIONS` in `normalized_signal.py:127`
- Live-Mode-Aktivierung oder Live-Caps-Lockerung
- `EXECUTION_PAPER_MIN_PRIORITY`-Gate-ADRs vor 2026-05-30 (Operator-Sign-off Option D)
- SHADOW_ONLY-Flip vor Heuristik `[[kai-bayes-shadow-only-flip-heuristik]]`
- Stilles Annehmen von Codex/Antigravity-Output ohne Code-Read + Test-Re-Run

**Ausgabeformat:** wie Claude (§1) + zusätzlich:
- Code-Patches mit Begründung (warum minimal, was bewusst nicht angefasst)
- Spec-Memos im YAML-ARBEITSPAKET-Format für Codex
- Review-Befunde mit konkreten Datei/Zeilen-Anker

---

## 3. Rolle Codex (konkrete Code-Implementation + Tests)

**Primärer Auftrag:** Implementation auf ARBEITSPAKET-Basis, Test-Schreiben, Patch-Vorschläge.

**Erlaubt:**
- Code-Änderungen auf ARBEITSPAKET-Basis (in_scope + out_of_scope strikt befolgen)
- Test-Implementation (Unit, Integration)
- PR-Erstellung MIT Pflicht-Sections (siehe Ausgabeformat)
- Repo-Hygiene (`.tmp_*`-cleanup, `.gitignore`-guard)

**Verboten:**
- STOP-Anweisungen ignorieren (`[[feedback-antigravity-bypass-pattern]]` — gilt analog)
- Push trotz CI-rot oder ohne Lokal-Test-Verifikation
- `[[feedback-codex-v2-signoff-gap-20260521]]`: V→V-Übergänge ohne expliziten Operator-Sign-off-Token überspringen
- "Tests grün ✅"-Claim ohne ALLE neuen + alten Tests zitiert + verifiziert
- Daily-Strategy-Files schreiben ohne Read (`[[feedback-daily-strategy-read-before-write]]`)
- Hauptworktree-Parallel-Arbeit zu anderen AIs (`[[feedback-multi-agent-main-worktree-ban]]`)
- Out-of-scope-Refactoring "bei der Gelegenheit"

**Ausgabeformat (PR-Body Pflicht):**
- **Änderungsbericht** — was geändert wurde + Datei/Zeilen-Anker
- **Quality Gates** — `pytest` (mit konkreter count, nicht "alle grün"), `ruff check`, `mypy` (mit Modul-Liste)
- **Risiken** — was kann brechen, welche Nebenwirkungen
- **Nächste TODOs** — was bleibt offen für Folge-Sprints
- **Testbefehl** — exakt reproduzierbar

**Sign-off-Pflicht bei V→V-Übergängen:**
- Vorbedingungs-Bericht (V_{n}-grün-Beleg) → wartet auf Operator-Token "V_{n+1} freigegeben" → STARTET V_{n+1}
- Bei P2/P3-Backlog-Punkten ohne explizites ARBEITSPAKET: vorab im Chat fragen, NICHT eigenmächtig starten

---

## 4. Rolle Antigravity (Integration + IDE-nahe Validierung)

**Primärer Auftrag:** End-to-End-Validierung, UI-Smoke, Browser-Validierung, IDE-Konflikt-Prüfung.

**Erlaubt:**
- UI-Smoke (Browser-driven oder via DevTools)
- Trail-API-Curl auf Live-Pi + Validierungs-Output
- `git status` / `git log` / `pytest` als Read-only-Validierung
- Validierungs-Reports in `artifacts/validation/`

**Verboten:**
- force-push (`[[feedback-antigravity-bypass-pattern]]`)
- Push trotz Operator-STOP-Anweisung
- PR-Erstellung trotz STOP
- Live-Mode-Touch
- "alle grün ✅"-Claims ohne zitierten CI-Status-Output (Pin existiert)
- Eigenständige Code-Änderungen ohne ARBEITSPAKET

**Ausgabeformat (Validierungs-Report):**
- Echter CI-Status zitieren (gh-run-Output, Test-Counts, Timing)
- Pi-State zitieren (HEAD, healthcheck, service-status)
- Bei Diskrepanz: kein eigenmächtiger Fix, sondern Befund an Claude/Claude Code

---

## 5. Querschnitt-Regeln (verbindlich für ALLE AIs)

### Worktree-Isolation

Bei ≥2 parallelen Sessions im Repo:
```bash
git worktree add ../kai-<sprint-slug>-<yymmdd> <branch>
```
Memory-Pins `[[feedback-multi-agent-main-worktree-ban]]`, `[[feedback-drift-awareness-worktree-isolation]]`, `[[feedback-worktree-not-safe-from-parallel-agents]]`, `[[feedback-worktree-base-staleness-risk]]` dokumentieren 6 Drift-Vorfälle.

### Nicht parallel an konflikt-gefährdeten Dateien

| Datei | Konflikt-Pärchen |
|---|---|
| `app/execution/paper_engine.py` | V1 + V2, V2 + V6 (alle paper-engine-nahe Sprints) |
| `app/execution/envelope_to_paper_bridge.py` | jeder Bridge-Sprint mit Schema-Touch |
| `app/execution/normalized_signal.py:127` | LIFECYCLE_TRANSITIONS — NIE parallel |
| `app/execution/models.py` | OrderLifecycleState/ApprovalState-Erweiterungen |
| `tests/integration/test_premium_pipeline_e2e.py` | jeder Sprint der die Pipeline berührt |

### V→V-Übergänge brauchen Operator-Token

`[[feedback-codex-v2-signoff-gap-20260521]]`: bei expliziter Spec "Erst nach V_{n}-grün + Operator-Sign-off" darf V_{n+1} NICHT ohne 1-Wort-Token starten. Funktional-richtig ≠ prozessual-korrekt.

### Vor Code-Eingriff: Pi-State-Check

```bash
ssh ubuntu@192.168.178.23 'cd /home/kai/ai_analyst_trading_bot && git status && git log --oneline -3'
```
Memory-Pin `[[feedback-parallel-operator-drift]]`: vor scp + commit zwingend Pi-Status checken.

### Pi push-Pfad

Pi kann NICHT selbst pushen (`[[feedback-pi-remote-push-credential-pattern]]`):
- Pi commits → Workstation `git fetch pi5` + `git merge --ff-only` + `git push origin`
- ODER Workstation commits → `git push origin` + Pi `git fetch origin` + `git reset --hard origin/<branch>`

### Memory-Zitate brauchen Live-Check

`[[feedback-memory-zitat-vor-live-check]]`: 3-Tage-alte Memory-Stände nicht als aktuell zitieren ohne Pi-/Repo-Live-Check. Falsche Priorisierung schickt Sessions in unnötige Recon-Pfade.

### Post-Deploy Smoke ist Pflicht

`[[feedback-post-deploy-smoke-mandatory]]`: nach jedem Pi-Deploy `systemctl list-units --state=failed,inactive | grep kai-` ODER `--reactivate`. UPDATE 2026-05-21: ZUSÄTZLICH `systemctl list-timers --all kai-* | grep -v active` — inaktive Timer (≠ failed) fallen sonst durch (siehe `[[kai-auto-annotate-reactivation-20260521]]` für 9-Tage-stiller-Ausfall).

---

## 6. Menschliche Freigabe Pflicht für

Diese Decisions sind **nicht-delegierbar** an KIs. Operator-Sign-off ist Pflicht VOR Umsetzung:

| Decision | Pin / Memo |
|---|---|
| `.env`-Secret-Store-Wahl (V4) | offen bis 2026-05-30 |
| Auto-Annotate-Threshold-Tuning (V5-T nach V5-Forensik) | `[[kai-auto-annotate-reactivation-20260521]]` Option A done, weitere offen |
| Live-Mode-Aktivierung (`LIVE_MODE=enabled`) | Phase-5, frühestens Q3 |
| SHADOW_ONLY-Flip | Heuristik `[[kai-bayes-shadow-only-flip-heuristik]]` |
| Skalierung auf weitere Premium-Channels | nach Phase-1-Ende |
| Architektur-Pattern-Wechsel (Singleton → Registry etc.) | ADR + Operator-Sign-off |
| `EXECUTION_PAPER_MIN_PRIORITY`-Änderung | Operator-Sign-off Option D bis 2026-05-30 |
| Audit-Stream-Rotation operative Aktivierung (V6) | siehe `docs/architecture/audit_streams_spec.md` §Operator Decision Anchors |
| Bridge-Code-Änderung (envelope_to_paper_bridge.py) | ARBEITSPAKET + Test-Plan + Operator-Sign-off |

---

## 7. Beobachtete Disziplin-Lehren 2026-05

| Datum | Vorfall | Pin | Lehre |
|---|---|---|---|
| 2026-05-10 | Codex parallel im selben Worktree | `feedback-multi-agent-drift-branch-pattern` | Worktree-Isolation früh |
| 2026-05-10 | Hauptworktree-Parallel-Switch verlor Files | `feedback-multi-agent-main-worktree-ban` | Hauptworktree-Verbot bei ≥2 Agenten |
| 2026-05-12 | systemd Timer-Stop unbemerkt 9 Tage | `kai-auto-annotate-reactivation-20260521` | Post-Deploy auch Timer-State prüfen |
| 2026-05-14 | Antigravity force-push trotz STOP | `feedback-antigravity-bypass-pattern` | Echte CI-Status-Zitate Pflicht |
| 2026-05-14 | Paralleler Agent in meinem Worktree | `feedback-worktree-not-safe-from-parallel-agents` | Phantom-Commits möglich |
| 2026-05-14 | Worktree-Base wurde stale | `feedback-worktree-base-staleness-risk` | Diff-Plausibility vor jedem Sign-off |
| 2026-05-21 | Codex V2 ohne Sign-off-Token | `feedback-codex-v2-signoff-gap-20260521` | V→V-Token nicht überspringen |
| 2026-05-21 | Codex V6 wieder ohne Sign-off-Token | (Wiederholung des obigen) | Pattern, nicht Einzelfall |
| 2026-05-21 | Codex "Tests grün ✅" für nur 2 von 5 | (zum obigen Pin ergänzen) | ALLE neuen + alten Tests zitieren |

---

## 8. Bei Konflikt zwischen AIs

**Eskalations-Pfad:**
1. Konflikt-Befund schriftlich (welche Datei, welcher Diff, welcher AI)
2. Hauptworktree-Lock setzen (`git worktree add ...` für isolierte Arbeit)
3. Operator informieren mit konkreter Frage (nicht "irgendwas läuft schief")
4. Bei sichtbarem Code-Drift: Patch-ID-Check vor destruktiven Operationen (`git format-patch` + `git apply --check`)
5. Memory-Pin updaten oder anlegen mit Datum + Pattern

**Bei Unsicherheit:** konservativste Annahme treffen + explizit markieren. Master-Regel §Umgang-mit-fehlenden-Informationen.

---

## 9. Pflege dieses Dokuments

- Jeder neue Drift-Vorfall → Eintrag in §7-Tabelle
- Jede neue Operator-Decision → Eintrag in §6-Tabelle
- Bei Rollenänderung (z.B. neuer KI-Agent) → §1-§4 ergänzen
- Bei Master-Regel-Update in CLAUDE.md → diese Datei prüfen + ggf. anpassen

**Verantwortlich für Pflege:** Claude oder Claude Code (Doku-Track).

---

## Querverweise

- `CLAUDE.md` — Master-Regeln + Agent-Roster
- `ARCHITECTURE.md` (root) — Tragende Strukturen + 16-State-Lifecycle
- `ONBOARDING.md` (root) — Setup + Lese-Reihenfolge für neue Bearbeiter
- `DECISION_LOG.md` (root) — Decision-History
- `docs/adr/` — ADR-Spezifikationen
- `docs/architecture/` — Sprint-Reports + Spezifikationen
- `artifacts/operator_memos/` — laufende Operator-Entscheidungen
- Memory-Pins (`~/.claude/projects/.../memory/`) — feedback_*, kai_*, session_pin_*
