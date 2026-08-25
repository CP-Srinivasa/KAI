# STAB-2026-08 — Kurzurteil-Review + Stabilisierungspaket (Claude ⇄ Codex)

> **SSOT im Repo** (D-235/D-236). Spiegel: `KAI-mirror/stabilisierungspaket_claude_codex_20260825.md`. Für Codex-Aufträge gelten §4 (Pakete/Owner), §5 (Scope-Allowlist, Tabu-Pfade, Dispatch-Kapsel) und §6 (Arbeitspakete). Deploy-Hinweis STAB-07: `pip install -e .` ist Pflicht, damit der `kai`-Entry auf dem Pi existiert.

Stand: 2026-08-25, ~12:30 CEST · Prüfung read-only (Pi `ubuntu@192.168.178.23`, GitHub, Mainline `claude/p7/reentry-ia-codex-cycle`) · nichts deployed, gemergt, gelöscht, neu gestartet.

Bindender Rahmen (unverändert): ADR-0012/-0014 (Research-/Truth-Plattform, Tier-2-STOP) · ⛔ keine Kalt-Ansprache (prove-by-doing) · Paper/Lern-Phase · Edge nur via `canonical-edge` · kein Aggregat ohne Zerlegung · Voll-Gate vor Push · kein Auto-Merge bei Architektur-PRs · Live-Trading, LN-Pay, Kapital, Pi-Restart = Operator-GO.

---

## 0. Was ich gemessen habe (Beweise, nicht Meinung)

| Behauptung im Kurzurteil | Live-Messung 25.08. | Urteil |
|---|---|---|
| Pi-Checkout `52145cc1`, Prozess auf `79e6fca7`, 23 / 26 Commits hinter Checkout / Mainline | `git rev-parse HEAD`=`52145cc1`; `kai-server` MainPID 2736616 seit **18.08. 22:30:09**; Reflog 22:30:08 = `79e6fca7`; `rev-list --count` = **23** / **27** (Mainline ist seit #756 `2ebf13d4`) | ✅ bestätigt (27 statt 26) |
| `/health` gibt keine Version des laufenden Codes aus | `{"status":"ok","version":"0.1.0"}` — keine Commit-Info | ✅ bestätigt |
| `/dashboard/api/quality` blockiert den Event-Loop | Code: `dashboard.py:728` `async def dashboard_quality_api` ruft `_load_jsonl()` **dreimal synchron** (Exec-Audit, Alert-Audit, Outcomes) + `read_text()`-Scans; Cache nur TTL, kein Single-Flight | ✅ strukturell bestätigt |
| Watchdog registrierte kurz Ausfall | `kai-server-health-watchdog` (alle 2 min, `--threshold 3 --cooldown-s 300`): **8× `health=DOWN consecutive=1`** in 14 Tagen (21.08. 11:32/11:56, 22.08. 23:28, 23.08. 19:54, 24.08. 20:24/23:24, 25.08. 07:26/11:08), nie 2 in Folge ⇒ 0 Restarts. `/health` kalt **1,7–2,4 s**, warm 4 ms | ⚠ bestätigt, aber **Ursache nicht belegt**: die Aussetzer liegen zu Zeiten, in denen niemand das Dashboard öffnet |
| 13 offen / 8 überfällig / 7 ohne Watcher / 0 bewertbar | `prereg-maturity --json`: **14** im Reifeblick — 4 RESOLVED, 2 NOT_DUE, 1 EVAL_CHECK_DUE (K1), **7 UNWATCHED** ⇒ **8 fällig**. Ledger hat **19** Einträge; 5 davon sind im Reifeblick **unsichtbar** (V5, ND-fwd, ND-hedged-btc, ND-micro, C1). „13 offen" nicht reproduzierbar (10 im Reifeblick, +5 unsichtbar) | ⚠ 8/7/0 bestätigt, 13 nicht; zusätzlich ein Zähl-Artefakt (s. §1.3) |
| Edge n=208, −20,9 bps, P=20,4 % | `canonical-edge --json`: `trade_count=208`, `mean_net_bps=−20,86`, `median=−111,2`, `trimmed=−50,2`, `p_mu_net_positive=0,204`, ohne Best-Trade (+3.861 bps) **−39,6 / P=0,014**, `gross_mean=−0,86` (P_gross 0,46), Kosten 20 bps, realized **−1.049,95 USD** | ✅ bestätigt; **Memory (n=68/10,44 % vom 01.07.) ist veraltet → aktualisiert** |
| Backups laufen, tar rc=1 toleriert | `kai-standby-data.timer` (00/06/12/18:20) → `/mnt/kai-data/kai-standby/data_*.tar.gz` (189 MB, **unverschlüsselt, exfat**); Log 25.08. 04:20: `tar: artifacts: file changed as we read it … rc=1 … accepted`. **`kai-backup-artifacts.service` (verschlüsselt, Restore-Vertrag) hat KEINEN Timer und ist NIE gelaufen** (`ExecMainStartTimestamp=` leer) | 🔴 **untertrieben**: das eigentliche Backup existiert nur als Unit-Datei |
| DECISION_LOG endet im Juni | letztes Datum **2026-06-11** | ✅ bestätigt |
| 89 Worktrees / 42 > 14 d | `git worktree list`: **13** (8 > 14 d) — inkl. fremder Arbeitskopie #759 und heutigem `kai-audit-2phase` (#769) | ❌ nicht reproduzierbar (andere Zählung?) — Kernaussage „Leichen" trotzdem richtig |
| alter Claim ohne Ablauf | `ACTIVE_CLAIMS.md`: `20260711-llm-foundation` ohne `expires_at`; `20260805-c1-verdikt` abgelaufen, nie geschlossen | ✅ bestätigt (2, nicht 1) |
| 8 Godfiles 1.800–3.400 Z. | `trading.py` 3.423 · `telegram_bot.py` 3.359 · `dashboard.py` 3.066 · `cli/main.py` 2.435 · `trading_loop.py` 2.298 · `envelope_to_paper_bridge.py` 1.942 · `settings.py` 1.883 · `paper_engine.py` 1.882 | ✅ bestätigt |
| 13 Module mypy-Override | `pyproject.toml` `[[tool.mypy.overrides]]`: **12** Module `ignore_errors=true` | ≈ (12) |
| Branch Protection ohne Reviews, Admin-Bypass | `required_approving_review_count=None`, `enforce_admins=false`, `strict=false`, 7 Pflicht-Checks | ✅ bestätigt |
| #756 „mergefähig" | **MERGED** 25.08. 09:41:56Z = `2ebf13d4` — aber ⚠ **nicht verdrahtet** (kein Venue-Adapter, kein Timer, kein Caller) | ❌ veraltet |
| #759 CLEAN | OPEN, Head `516f2039`, `MERGEABLE/CLEAN`, **1 Commit hinter Mainline**, gehört der **Parallelsession** (Worktree `C:/tmp/kai-feat-frozen-evaluation-contract-20260821-…`) | ✅ + Hinweis: heute zusätzlich **#769** offen (Parallelsession aktiv!) |
| Mainline-CI grün | `2ebf13d4` CI success 09:41Z | ✅ |
| 0 failed Units, Paper an, Live/LN-Pay aus | 0 failed · 52 kai-Timer · `ENTRY_MODE=paper`, `APP_LN_PAY_ENABLED=false` (Memory 06.08.) | ✅ |

---

## 1. Review der fünf Kritikpunkte

### 1.1 Runtime ≠ Checkout ≠ Mainline — P0 ✅ zu Recht, Ergänzung
Richtig und der wichtigste Punkt. Ergänzungen:
- Es ist **nicht nur** Server vs. Timer: die Timer laufen bereits auf `52145cc1`, während `/health`, Dashboard, Paper-Engine (in-process) und Scheduler auf `79e6fca7` stehen. Alles, was seit 18.08. 22:30 gemergt wurde und den **In-Prozess-Pfad** betrifft (#723–#762, u. a. Sample-Integrität #757, Finite-Execution-State #762), ist **nicht im Speicher**. Das Memory-Prinzip „Code auf Platte ≠ Code im Prozess" ist hier seit **7 Tagen** verletzt.
- Der Deploy-Pfad (`kai_deploy.sh`) restartet — die letzten 4 ff-Merges am 20./21.08. liefen **ohne** Restart (Reflog zeigt nur Fast-Forwards). Das heißt: es gibt einen zweiten, unbeaufsichtigten Update-Pfad (manueller `git pull`/Unit-Apply), der die Runtime-Identität still auseinanderzieht. Der Fix muss diesen Pfad **sichtbar** machen, nicht nur den Deploy härten.
- **Umsetzungsvorschlag präzisiert:** `runtime_identity` beim Import einmalig aus `git rev-parse HEAD` + `pyproject`-Lock-Hash + Start-UTC → in `/health` (`runtime_commit`, `checkout_commit`, `drift_commits`, `started_at_utc`) + Datei `artifacts/runtime/runtime_identity.json`. `kai-health-check` meldet Befund bei `drift_commits>0` länger als 60 min. Deploy-Urteil: `runtime≠checkout` nach Restart ⇒ `FAILED`, nicht HOLD (das ist ein kaputter Restart). Zusätzlich die Lock-Datei prüfen (`pip install -e .` vergessen = eigene Drift-Klasse, Memory 18.08.).
- **Restart:** getrennt, mit Operator-GO, als regulärer `kai_deploy.sh`-Lauf (Unit-Sync + Beweis + Rollback), nicht als nackter `systemctl restart`. Vorher: 0 offene Paper-Positionen prüfen (Stop-Kaskade 21.08.), `RISK_MAX_OPEN_POSITIONS` unverändert.

### 1.2 Quality-Endpoint blockiert die API — P0/P1 ⚠ richtig, aber Ursache unbewiesen
- Der Code-Befund stimmt (drei synchrone `_load_jsonl` im `async def`, `dashboard.py:745/885/889`). `asyncio.to_thread` + Single-Flight + gekennzeichneter Stale-Cache ist richtig.
- **Aber:** die 8 Watchdog-Aussetzer liegen nachts/abends, nicht bei Dashboard-Nutzung. Und `/health` braucht **kalt 1,7–2,4 s** — das ist ein zweiter, ungeklärter Stall (Import-Lazy-Loading? Scheduler-Tick? SQLite-Checkpoint?). Quality ist **ein bewiesener** Blocker, nicht **der** bewiesene. Wer jetzt nur Quality umbaut und „gelöst" meldet, wiederholt die Lehre vom 18.08. („Schwellen messen, nicht aus einem Vorfall setzen").
- ⇒ **Reihenfolge ändern:** erst Event-Loop-Lag **messen** (In-Prozess-Sampler, 1-s-Tick, p50/p95/max in JSONL + `/metrics`), dann Quality off-loop, dann mit Messung **nachweisen**, dass die Aussetzer verschwunden sind. Alles andere ist Symptom-Patch.
- Watchdog-Semantik: ein einzelner 5-s-Timeout heißt im Log `DOWN`. Korrekt wäre `SLOW` (1/3) vs. `DOWN` (3/3). Kosmetik, aber genau diese Unschärfe hat das Kurzurteil zu „Ausfall" verleitet.
- Uvicorn-Worker: Zustimmung — **nicht** erhöhen, solange Scheduler in-process sind (doppelte Scheduler = doppelte Orders).

### 1.3 Prä-Registrierungen werden nicht geschlossen — fachlich P0 ✅, plus ein Zähl-Artefakt
- Die 8 fälligen sind real. K1 (`00c75a76`) seit 03.08. — nur der Operator kann die qualifizierten Anfragen zählen (Memory-Punkt 1 seit 17.08.).
- **Neu gefunden:** `0879a65c` (LN-Reconciliation) steht `UNWATCHED`, obwohl ein **PASS-Verdikt** und ein Timer (#686) existieren. Der Reifeblick liest die Verdikt-Attestierung nicht ⇒ mindestens 1 der 7 „ohne Watcher" ist ein Artefakt. Und 5 Ledger-Einträge (V5, C1, ND-Varianten) tauchen im Reifeblick **gar nicht** auf — „terminal" ist dort nicht abbildbar. `prereg-list --json` hat **kein** `state`-Feld; der Operator muss zwei Quellen im Kopf joinen. Das ist exakt das „Wachlisten gegen die Quelle abgleichen"-Muster vom 18.08.
- ⇒ Zusätzlich zur Operator-Bereinigung: **Reconciliation** Ledger ↔ Reifeblick ↔ Verdikt-Ledger als Invariante (jede versiegelte Prä-Reg ist genau eines von: `WATCHED` | `MANUAL_DUE(date)` | `RESOLVED(verdict)` | `ARCHIVED(reason)`), als Health-Check-Befund + Contract-Test. Automatisches Schließen: **nein** — nur automatisches **Sichtbarmachen**.

### 1.4 Backups — P1 ❌ untertrieben, real P0-Operator-Entscheidung
- Was läuft: Standby-Tarballs alle 6 h auf USB, **unverschlüsselt**, exfat (keine Rechte/Hardlinks), `tar rc=1 accepted`. Das ist ein Crash-Snapshot, kein Backup-Vertrag.
- Was **nicht** läuft: `kai-backup-artifacts.service` (verschlüsselt, mit Restore-Pfad, im Backup-Vertrag der Prä-Reg-Locks referenziert) hat **keinen Timer und ist nie gestartet** — weil `KAI_BACKUP_PASSPHRASE` seit 17.08. fehlt. Eine Restore-Probe gab es nie.
- ⇒ Einstufung: **P0 (Operator: Passphrase, offline gesichert — nicht nur auf dem Pi)** + P1 (Codex: Timer + Restore-Drill mit Beweis-Artefakt). Für die Standby-Tarballs: `crash_consistent:true` ins Manifest schreiben und die JSONL-Writer vor `tar` kurz per Lock-Datei anhalten oder per `--snapshot` (SQLite `.backup`) sichern — nicht „rc=1 akzeptieren" und schweigen.

### 1.5 Doku/Live-State-Drift — P1 ✅
- DECISION_LOG bis 11.06. — bestätigt. Juli/August (ADR-0014, LN-Disarm, Mock-Wurzel #728, sudo-Rückbau #731/#734, Deploy-Urteil #739–#741, Broker, Close-Verification-Stack, Frozen-Contract) fehlen komplett. Das Memory dieser Sessions ist derzeit die einzige Chronik — Bus-Factor 1 im wörtlichen Sinn.
- `AGENTS.md` sagt noch „Codex nicht reaktiviert (D-186)" — dein heutiger Auftrag hebt das für dieses Paket auf ⇒ braucht einen D-Eintrag, sonst widerspricht das Betriebsdokument dem Betrieb.
- Worktrees: 13, nicht 89 — aber 8 davon > 14 d, plus zwei stehende Claims (07-11 ohne Ablauf, 08-05 abgelaufen). Heute existieren **zwei fremde aktive Arbeitskopien** (#759, #769) — das Kollisionsrisiko ist akut, nicht theoretisch (dreimal belegt: #630/#636/#760).

---

## 2. Rahmen-Kritik

**„Technisch reifer als ein typischer Trading-Bot"** — falscher Maßstab. `docs/KAI_IDENTITY.md:23` verbietet genau diese Darstellung, ADR-0012:31 sagt „KAI ist NICHT (mehr) ein Alpha-generierender Trading-Bot". Reife ist am **eigenen** Anspruch zu messen: auditierbare Falsifikation, Betriebs- und Wahrheitskohärenz. An diesem Maßstab ist das Urteil ehrlicher: die Falsifikation funktioniert (Edge klar widerlegt, Mock-Epoche gefunden und korrigiert), die **Kohärenz** nicht (Runtime-Drift, nie gelaufenes Backup, 8 unentschiedene Experimente, Chronik 10 Wochen alt).

**Der Vergleich steckt im Code — Bestand (Mainline):**
- CLI-Entry `trading-bot = app.cli.main:app` (`pyproject.toml:89`), Typer-Name + Help „AI Analyst Trading Bot CLI" (`app/cli/main.py:40`), Package-Name `ai-analyst-trading-bot` (`pyproject.toml:6`)
- FastAPI-Titel „AI Analyst Trading Bot" (`app/api/main.py:252`) — steht in OpenAPI/`/docs`
- MCP „KAI Analyst Trading Bot" (`app/agents/mcp_server.py:91`, `app/agents/tools/compat.py:82`)
- Header „KAI / AI-Analyst-Trading-Bot" (`ARCHITECTURE.md:1`, `ONBOARDING.md:1`)
- ~40 Docstrings/Runbook-Zeilen `trading-bot <cmd>` (`app/cli/commands/*.py`, `docs/runbooks/*.md`)
- **71** Nennungen außerhalb des Pfadnamens; **137 Dateien** enthalten den Pfad `ai_analyst_trading_bot` (Units, Runbooks, Pi-Migration)
- `docs/archive/**` (historisch, bleibt unangetastet)

**Vorschlag in zwei Stufen:**
- **Stufe 1 (jetzt, gefahrlos, ein PR):** `kai` als kanonischer CLI-Entry, `trading-bot` bleibt als **deprecierter Alias** auf dieselbe Typer-App (kein Unit/Runbook bricht); Typer-Name/Help, API-Titel, MCP-Name, Doc-Header, Docstrings → „KAI"; `docs/archive` unberührt; CI-Ratchet: keine neue „Trading Bot"-Selbstbezeichnung außerhalb `docs/archive/` und Pfadnamen. Package-Name in `pyproject` **nicht** in Stufe 1 (ändert das Dist-Wheel/`.egg-info`, Pi braucht dann `pip install -e .` — gehört ins Deploy-Fenster).
- **Stufe 2 (gated, P2, Operator-GO):** Repo-/Verzeichnis-Umbenennung `ai_analyst_trading_bot` → betrifft 137 Dateien, Pi-Pfad + Symlink `/home/kai`, 117 Units, Backup-Pfade, Worktrees, Cloudflare-Tunnel-Config. Nur mit Deploy-Fenster und **nach** STAB-02 (sonst ist die Drift danach unsichtbar). Empfehlung: nicht in diesem Paket.

**Prioritäten/Aufwände:** Reihenfolge P0 stimmt bis auf zwei Korrekturen: (a) Backup von P1 → **P0-Operator**, (b) Event-Loop-**Messung** vor dem Quality-Umbau. Aufwände sind plausibel; die versteckten Kosten sind Koordination (zwei fremde Sessions) und das Deploy-Fenster.

---

## 3. Integrationen: akzeptiert / geändert / zusätzlich / abgelehnt

| Vorschlag | Entscheidung | Begründung |
|---|---|---|
| Prometheus/OTel-Metriken | **geändert → leichtgewichtig**: In-Prozess-Sampler (Event-Loop-Lag, Endpoint-p95, Runtime-Commit, Snapshot-Alter, Scheduler-Tick-Alter) → JSONL-Ring + `/metrics` (Prometheus-Textformat, **ohne** neue Abhängigkeit). **Kein** Prometheus-Server/Grafana/OTel-Collector auf dem Pi | Pi-CPU ist knapp (Kadenzen wurden gerade erst gesenkt); der Konsument ist `kai-health-check` + Telegram, kein Grafana |
| Binance+Bybit nur für Close-Evidence | **akzeptiert**, mit Memory-Auflage: Shadow-Report (N, Verdikte, `UNVERIFIED`-Gründe, Divergenz-Verteilung) **ohne** Buchänderung; `close_classification` erst danach; `MAX_QUOTE_AGE_MS`/`VENUE_BAND_TOLERANCE_PCT` sind gesetzt, nicht gemessen — der Shadow-Report misst sie | ADR-0012-konform (Falsifikation, keine Signalquelle) |
| Datensparsamer Demand-Funnel | **akzeptiert mit hartem Rahmen**: nur Zählung auf eigenen Endpunkten (`/paper`, Oracle, L402-Preflight), keine Cookies/Tracker, **⛔ keine Kalt-Ansprache, keine Outreach-Kits**, Verdikte nur über Prä-Reg (K1 ist genau dieser Funnel — erst K1 entscheiden, dann automatisieren) | Externe Einnahmen lifetime 0 sat ist die ehrliche Zahl; ein Funnel, der das misst, ist prove-by-doing |
| **Zusätzlich:** Runtime-Identität als Deploy-Invariante | **neu, P0** | s. §1.1 |
| **Zusätzlich:** Prä-Reg-Reconciliation-Invariante | **neu, P0** | s. §1.3 |
| **Zusätzlich:** Backup-Timer + Restore-Drill | **neu, P0/P1** | s. §1.4 |
| **Zusätzlich:** Health-Semantik (liveness/readiness/pipeline) als ein Dokument + drei getrennte Signale | **neu, P1** | fünf „health"-Units (`/health`, `kai-health-check`, `kai-server-health-watchdog`, `kai-service-watchdog`, `kai-pi-health`) ohne definierte Semantik erzeugen genau den Widerspruch aus §1.2 |
| **Zusätzlich:** TV-Ingest-Breite | **Operator-Punkt, kein Code**: ~1,5 % der früheren Alert-Breite (13 Events seit 18.08. vs. ~97/Tag zuvor) — restliche Alerts scharf stellen | Eingangsseite fehlt im Kurzurteil komplett (Lehre 18.08.: Monitoring wacht über Ausgänge) |
| Weitere Agenten/LLMs, Feeds, Strategiegeneratoren, Kubernetes, Live | **abgelehnt** (Zustimmung) | Engpass ist Kohärenz, nicht Daten |
| Mehr Uvicorn-Worker | **abgelehnt** | in-process Scheduler ⇒ Doppelausführung |
| Repo-Umbenennung Stufe 2 | **verschoben (gated)** | s. §2 |

---

## 4. Paket STAB-2026-08 — Wellen + Owner

Legende: **C** = Claude (Wahrheits-Semantik, Deploy, Memory, Operator-Kommunikation, Merge) · **X** = Codex (isolierte, testbare Code-Arbeit mit klarem Datei-Scope) · **O** = Operator (gated).

| ID | Titel | Owner | Prio | Welle | Abhängig von | Aufwand |
|---|---|---|---|---|---|---|
| STAB-00 | Koordination: Claims, D-Eintrag „Codex für STAB reaktiviert", AGENTS.md-Zeile, Paketdatei ins Repo | C | P0 | 0 | — | 1 h |
| STAB-01 | Identity Stufe 1: `kai`-CLI + Alias, API/MCP/Doc-Namen, Ratchet | C | P1 | 0 | — | 3–4 h |
| STAB-02 | Runtime-Identität: `/health`-Felder, Artefakt, Health-Check-Befund, Deploy-Urteil | C | **P0** | 1 | — | 4–6 h |
| STAB-03 | Event-Loop-Lag-Sampler + `/metrics` (Textformat, keine Deps) | X | **P0** | 1 | — | 4–6 h |
| STAB-04 | Quality off-loop: `to_thread` + Single-Flight + Stale-Kennzeichnung | X | P0/P1 | 1 | STAB-03 (Messung vorher/nachher) | 4–8 h |
| STAB-05a | Backup-Passphrase setzen (offline-Kopie!), `KAI_BACKUP_RCLONE_REMOTE` optional | O | **P0** | 1 | — | 15 min |
| STAB-05b | `kai-backup-artifacts.timer` + Restore-Drill-Skript mit Beweis-Artefakt (sha256-Vergleich, Zählung), Standby-Manifest `crash_consistent` | X | P1 | 1 | 05a für den Live-Beweis | 4–6 h |
| STAB-06a | K1 zählen + Verdikt; 7 UNWATCHED je: Watcher / Termin / Archiv | O | **P0** | 1 | — | 1–2 h |
| STAB-06b | Prä-Reg-Reconciliation: Reifeblick liest Verdikt-Ledger, Ledger↔Reifeblick vollständig, `prereg-list --json` mit `state`, Invariante als Health-Befund + Contract-Test | C | **P0** | 1 | — | 4–6 h |
| STAB-07 | Controlled Restart = `kai_deploy.sh` nach Merge von 02/03/04 (Snapshot, Unit-Sync, Beweis, Smoke) | C + O-GO | **P0** | 1→2 | 02, 03, 04 gemergt | Betriebsfenster 30 min |
| STAB-08 | Health-Semantik: liveness/readiness/pipeline definiert, Watchdog `SLOW` vs `DOWN`, Schwellen aus STAB-03-Daten | C | P1 | 2 | 03 (7 Tage Daten) | 3–4 h |
| STAB-09 | Close-Evidence Shadow: Binance- + Bybit-`CandleFetcher`, CLI `--shadow`, Shadow-Report, Divergenz-Metrik; **keine** Klassifikations-Anbindung | X | P1 | 2 | — | 1–2 Tage |
| STAB-10 | #759 Review + Restore-Probe + Aktivierungsplan (kein Force-Push, PR gegen Branch) | C | P1 | 2 | — | 3–4 h |
| STAB-11 | DECISION_LOG Nachtrag 06-11 → 08-25 (aus Memory + Git-Log, D-Nummern) | C | P1 | 2 | — | 3–4 h |
| STAB-12 | Worktree-/Claims-Hygiene: Report-Skript (Alter, Merge-Status, Claim-Ablauf) — **nur melden, nie löschen** | X | P2 | 3 | — | 2–3 h |
| STAB-13 | mypy-Overrides 12 → ≤ 6, ein Modul pro PR, null Verhaltensänderung | X | P2 | 3 | — | je 1–2 h |
| STAB-14 | Godfile-Extraktion reiner Helfer aus `trading.py` + `dashboard.py` (Ratchet: keine Datei wächst) | X | P2 | 3 | 04 gemergt | je 3–4 h |
| STAB-15 | Branch Protection: `strict=true` (Branch aktuell), Required-Checks bleiben; Reviews-Pflicht bei Solo-Account nicht erzwingbar → Doktrin „kein Auto-Merge bei Architektur" bleibt der Ersatz | O | P2 | 3 | — | 10 min |
| STAB-16 | TV-Alerts scharf stellen (restliche Symbole) | O | P1 | 1 | — | 30 min |

**Welle 0 (heute):** 00, 01. **Welle 1 (P0, parallel):** C: 02, 06b · X: 03, 04, 05b · O: 05a, 06a, 16. **Welle 2:** 07 (Fenster), dann C: 08, 10, 11 · X: 09. **Welle 3:** X: 12, 13, 14 · O: 15.

Definition of Done je Paket: Tests grün im Voll-Gate · CI grün · Claude-Review · Squash-Merge mit `--match-head-commit` · Live-Nachweis (wo Pi betroffen) im PR-Text · Memory-/Doku-Sync.

---

## 5. Spielregeln Claude ⇄ Codex (bindend für dieses Paket)

1. **Claim vor Worktree** in `KAI-mirror/ACTIVE_CLAIMS.md` mit Datei-Scope + `expires_at` ≤ 24 h; vorher `git worktree list` **und** `gh pr list --state open` lesen. Überlappung = STOPP.
2. **Codex-Scope (erlaubt):** `app/api/routers/dashboard.py` (nur STAB-04/14), `app/observability/**`, `app/execution/close_evidence*` + neue Adapter unter `app/execution/venues/`, `scripts/kai_backup_*`, `scripts/standby_to_usb.sh`, `deploy/systemd/kai-backup-artifacts.timer` (Datei, nicht Apply), `tests/**`, `pyproject.toml` nur `[tool.mypy]`.
   **Codex-Tabu:** `deploy/bin/`, `scripts/kai_deploy*.sh`, `scripts/pi_*`, `.env*`, `app/execution/paper_engine.py`, `app/execution/envelope_to_paper_bridge.py`, `app/orchestrator/trading_loop.py`, `app/research/prereg*`, `artifacts/**`, sudoers/Broker, alles auf dem Pi.
3. **Claude-Scope:** Wahrheits-/Verdikt-Semantik, Deploy + Unit-Apply, Prä-Reg, Memory, DECISION_LOG, Identity, Merges, Operator-Rückfragen.
4. **Branches:** `codex/stab-XX-<slug>` bzw. `claude/stab-XX-<slug>`; ein Thema pro PR, ≤ ~400 Diff-Zeilen, **nie** Rename + Logik im selben PR.
5. **Kein Auto-Merge.** Codex-PRs werden von Claude reviewt und gemergt; Architektur-Berührung ⇒ Rebase + CI erneut + `--match-head-commit`.
6. **Kein Codex-Zugriff auf den Pi.** Live-Beweise liefert Claude (read-only bis STAB-07) und trägt sie in den PR ein.
7. **Messen vor Behaupten:** kein „gelöst" ohne Vorher/Nachher-Zahl (STAB-03 liefert die Zahl für 04/08).
8. **Codex-Dispatch-Kapsel (in jeden Codex-Auftrag kopieren):**
   > KAI = Research-/Truth-Plattform (ADR-0012/-0014), kein Trading-Bot; Paper-/Lernphase; Live-Trading, LN-Pay, Kapital, Pi-Restart sind gesperrt; ⛔ keine Kalt-Ansprache/Outreach vorbereiten; Edge ist widerlegt (n=208, −20,9 bps, P=0,204) — nie Gegenteil andeuten; Mock-Adapter darf nie wieder Preise in den Geldpfad liefern; kein Aggregat ohne Zerlegung; jede Schwelle wird gemessen, nicht gesetzt; Tests zuerst, Voll-Gate vor Push, kein Auto-Merge; Scope exakt wie im Arbeitspaket, Tabu-Pfade nicht anfassen; Ergebnis = PR + Beweis, keine Zusammenfassungs-Prosa.

---

## 6. Arbeitspakete Welle 0–1 (Master-Format)

```yaml
ARBEITSPAKET:
  task_id: STAB-01
  phase_id: PHASE-0 (Bereinigung/Identität)
  sprint_id: STAB-2026-08-W0
  titel: KAI-Identität im Code — Stufe 1 (Alias, keine Pfadänderung)
  warum_jetzt: Betriebsdokumente verbieten die Trading-Bot-Darstellung; CLI/API/MCP führen sie weiter.
  ziel: Selbstbezeichnung „Trading Bot" aus CLI-Help, API-Titel, MCP-Name, Doc-Headern, Docstrings; `kai`-Entry kanonisch.
  in_scope: [pyproject [project.scripts] kai + trading-bot(Alias), app/cli/main.py:40, app/api/main.py:252, app/agents/mcp_server.py:91, app/agents/tools/compat.py:82, ARCHITECTURE.md:1, ONBOARDING.md:1, Docstrings app/cli/commands/*.py, docs/runbooks/*.md (Befehlszeilen → `kai …`), CI-Ratchet-Test]
  out_of_scope: [Repo-/Verzeichnisname, pyproject name, docs/archive/**, Units, Pi]
  betroffene_module: [app.cli, app.api, app.agents]
  betroffene_dokumente: [README.md, ARCHITECTURE.md, ONBOARDING.md, docs/KAI_IDENTITY.md (Verweis), CHANGELOG.md]
  umsetzungshinweise: [Alias über zweiten Script-Eintrag auf dieselbe Typer-App; Deprecation-Hinweis nur im --help, kein Warn-Spam in Units; Ratchet als Test, der `git grep` gegen Allowlist prüft]
  tests_erforderlich: [Entry-Points beide importierbar, `kai --help` enthält kein „Trading Bot", Ratchet-Test rot bei neuer Nennung]
  validierung: [Voll-Gate lokal, CI grün, `openapi.json` Titel = KAI]
  akzeptanzkriterien: [0 Nennungen außerhalb Allowlist, `trading-bot`-Alias funktioniert, keine Unit geändert]
  risiken: [Pi nutzt `.venv/bin/trading-bot` in 4 Runbooks — Alias hält das; `pip install -e .` nötig für neuen `kai`-Entry auf dem Pi ⇒ erst mit STAB-07 live]
  doku_sync_pflicht: [CHANGELOG, ONBOARDING Befehlszeilen]
  naechster_folgeschritt: Stufe 2 nur nach Operator-GO, nach STAB-02.
```

```yaml
ARBEITSPAKET:
  task_id: STAB-02
  phase_id: PHASE-1 (Betriebswahrheit)
  sprint_id: STAB-2026-08-W1
  titel: Runtime-Identität als Deploy-Invariante
  warum_jetzt: Server läuft seit 7 Tagen 23 Commits hinter seinem Checkout; kein Signal zeigt das an.
  ziel: Laufender Commit sichtbar in /health + Artefakt; Drift ist Befund; Deploy-Urteil FAILED bei Runtime≠Checkout nach Restart.
  in_scope: [app/core/runtime_identity.py (neu), /health-Felder runtime_commit/checkout_commit/drift_commits/started_at_utc/lock_sha256, artifacts/runtime/runtime_identity.json, kai-health-check-Befund (drift>0 & >60 min), kai_deploy.sh-Nachbedingung]
  out_of_scope: [Restart selbst, Unit-Änderungen, Frontend]
  betroffene_module: [app.core, app.api.main, scripts/kai_health_check*, scripts/kai_deploy.sh]
  betroffene_dokumente: [RUNBOOK.md, docs/deploy/*, ARCHITECTURE.md (Abschnitt Betriebswahrheit)]
  umsetzungshinweise: [Commit einmal beim Start ermitteln (git rev-parse, Fallback Datei), nie pro Request; checkout_commit zur Anfragezeit lesen (billig: .git/HEAD+ref); Zeit injizierbar (Lehre 17.08.)]
  tests_erforderlich: [Unit: drift=0/>0, Fallback ohne .git, injiziertes now; Contract: /health-Schema; Deploy-Skript: Nachbedingung FAILED im Fake-Repo]
  validierung: [Voll-Gate, CI grün, nach STAB-07 live: /health zeigt runtime_commit==checkout_commit==Mainline]
  akzeptanzkriterien: [Drift ist ohne SSH sichtbar, Health-Check meldet sie, Deploy erkennt kaputten Restart]
  risiken: [git-Aufruf beim Import auf dem Pi ~50 ms — einmalig, ok; Symlink /home/kai beachten]
  doku_sync_pflicht: [RUNBOOK „Wie prüfe ich, welcher Code läuft"]
  naechster_folgeschritt: STAB-07 Restart-Fenster mit Operator-GO.
```

```yaml
ARBEITSPAKET:
  task_id: STAB-03
  phase_id: PHASE-1
  sprint_id: STAB-2026-08-W1
  titel: Event-Loop-Lag-Sampler + /metrics (Codex)
  warum_jetzt: 8 Health-Aussetzer in 14 Tagen ohne belegte Ursache; /health kalt 1,7–2,4 s.
  ziel: p50/p95/max Event-Loop-Lag, Endpoint-Latenz (p95 je Route), Scheduler-Tick-Alter, Runtime-Commit, Snapshot-Alter als /metrics (Prometheus-Text) + JSONL-Ring (24 h).
  in_scope: [app/observability/event_loop_lag.py (neu), app/api/routers/metrics.py (neu), Middleware für Route-Latenz, Startup-Task-Registrierung]
  out_of_scope: [Prometheus-Server, Grafana, OTel-SDK, Schwellen/Alarme (→ STAB-08), Quality-Umbau (→ STAB-04)]
  betroffene_module: [app.observability, app.api]
  betroffene_dokumente: [docs/watchdog/*, RUNBOOK.md]
  umsetzungshinweise: [Sampler = `await asyncio.sleep(1)` und Überschuss messen; Ringpuffer im Speicher + JSONL-Append alle 60 s; keine neuen Abhängigkeiten; Middleware darf bei Fehler nie den Request brechen; GZip bleibt innerste Middleware (Memory-Falle)]
  tests_erforderlich: [Sampler mit Fake-Clock, /metrics-Format-Test, Middleware fail-soft, kein Request-Overhead >1 ms (Benchmark-Job)]
  validierung: [Voll-Gate, CI grün (inkl. Benchmarks), nach STAB-07: 24 h Daten → Zahl für STAB-04-Nachweis]
  akzeptanzkriterien: [/metrics liefert die 5 Gauges, JSONL-Ring rotiert, 0 Verhaltensänderung an Fachpfaden]
  risiken: [Pi-CPU: Sampler 1 Hz ist vernachlässigbar; JSONL-Wachstum begrenzt durch Ring]
  doku_sync_pflicht: [docs/watchdog: Metriken-Katalog]
  naechster_folgeschritt: STAB-04 nutzt Vorher-Zahl; STAB-08 setzt Schwellen aus 7 Tagen Daten.
```

```yaml
ARBEITSPAKET:
  task_id: STAB-04
  phase_id: PHASE-1
  sprint_id: STAB-2026-08-W1
  titel: Quality-Endpoint off-loop (Codex)
  warum_jetzt: Drei synchrone JSONL-Scans im Event-Loop (dashboard.py:745/885/889) blockieren nachweislich /health.
  ziel: Berechnung in Thread, ein Rechner zur Zeit (Single-Flight), Cache mit `generated_at_utc` + `stale:true` bei Überalterung, Antwort nie >250 ms bei warmem Cache.
  in_scope: [dashboard_quality_api + Helfer, _quality_cache, asyncio.Lock/Event für Single-Flight]
  out_of_scope: [Materialisierte Snapshots per Timer (Folge-PR nach Messung), Frontend, andere Endpunkte]
  betroffene_module: [app.api.routers.dashboard]
  betroffene_dokumente: [docs/ui/*, RUNBOOK.md]
  umsetzungshinweise: [`await asyncio.to_thread(_compute_quality_payload)`; zweiter Aufrufer wartet auf Event statt neu zu rechnen; Stale-Kennzeichnung im Payload, nicht per HTTP-Code; kein Verhalten der Kennzahlen ändern (Zahlen vorher/nachher byte-gleich)]
  tests_erforderlich: [Concurrency-Test: /health antwortet <100 ms während Quality rechnet (Fake-Slow-Loader); Single-Flight-Test: 5 parallele Aufrufe = 1 Berechnung; Golden-Test: Payload identisch]
  validierung: [Voll-Gate, CI, nach STAB-07: STAB-03-p95 vorher/nachher im PR]
  akzeptanzkriterien: [Golden-Payload gleich, Concurrency-Test grün, Stale sichtbar]
  risiken: [Thread liest JSONL, während Writer schreibt — bereits heute so; Stale-Kennzeichnung muss im Frontend später gerendert werden (Folge, wie epochWarning)]
  doku_sync_pflicht: [docs/ui: Stale-Semantik]
  naechster_folgeschritt: Materialisierter Snapshot per Timer nur, wenn STAB-03 zeigt, dass to_thread nicht reicht.
```

```yaml
ARBEITSPAKET:
  task_id: STAB-05b
  phase_id: PHASE-1
  sprint_id: STAB-2026-08-W1
  titel: Backup-Timer + Restore-Drill mit Beweis (Codex)
  warum_jetzt: Das verschlüsselte Artefakt-Backup ist nie gelaufen; Standby-Tarballs sind nur crash-konsistent.
  ziel: `kai-backup-artifacts.timer` (täglich), `scripts/kai_backup_restore_drill.sh` (monatlicher Timer) mit Beweis-Artefakt (Dateizahl, sha256-Manifest-Vergleich, Dauer, Ergebnis), Standby-Manifest mit `crash_consistent`/`tar_rc`.
  in_scope: [deploy/systemd/kai-backup-artifacts.timer (neu), deploy/systemd/kai-backup-restore-drill.{service,timer} (neu), scripts/kai_backup_restore_drill.sh (neu), scripts/standby_to_usb.sh (Manifest), tests/ops/*]
  out_of_scope: [Passphrase (Operator), Unit-Apply auf dem Pi (Claude, via pi_apply_systemd_units.sh), rclone-Remote]
  betroffene_module: [scripts, deploy/systemd]
  betroffene_dokumente: [KAI-mirror/RESTORE_FROM_USB.md, RUNBOOK.md, SECURITY.md (Passphrase-Ablage)]
  umsetzungshinweise: [Drill restauriert in tmp-Verzeichnis, vergleicht Manifest, löscht nichts Produktives; Beweis nach artifacts/ops/backup_drill/<ts>.json; `OnFailure=` an den Notifier wie die anderen Units; Timer mit `Persistent=true`]
  tests_erforderlich: [bats/pytest gegen Fixture-Backup: Drill PASS/FAIL-Pfade, Manifest-Schema, fehlende Passphrase ⇒ klarer Befund statt Exit 0]
  validierung: [shellcheck, CI, nach Apply: erster Lauf mit Beweis-Artefakt im PR-Kommentar (Claude)]
  akzeptanzkriterien: [Backup läuft täglich, Drill monatlich, Beweis liegt vor, Health-Check meldet fehlendes/zu altes Backup]
  risiken: [exfat ohne Rechte/Symlinks — Manifest statt Attribute vergleichen; USB-Ausfall ⇒ Befund, kein Abbruch]
  doku_sync_pflicht: [RESTORE_FROM_USB.md aktualisieren]
  naechster_folgeschritt: Off-site (rclone) nur nach Passphrase-Entscheidung.
```

```yaml
ARBEITSPAKET:
  task_id: STAB-06b
  phase_id: PHASE-1
  sprint_id: STAB-2026-08-W1
  titel: Prä-Reg-Reconciliation-Invariante (Claude)
  warum_jetzt: Reifeblick zeigt 0879a65c als UNWATCHED trotz PASS-Verdikt; 5 Ledger-Einträge sind unsichtbar; prereg-list hat keinen Zustand.
  ziel: Jede versiegelte Prä-Reg ist genau eines von WATCHED | MANUAL_DUE(date) | RESOLVED(verdict) | ARCHIVED(reason); Abweichung = Health-Befund; `prereg-list --json` liefert `state` + `state_source`.
  in_scope: [app/research/prereg_maturity.py, app/cli/commands/trading.py prereg-list, app/observability/operator_board_live.py, kai-health-check-Befund, Contract-Test]
  out_of_scope: [Verdikte selbst (Operator), automatisches Schließen, neue Prä-Regs]
  betroffene_module: [app.research, app.cli, app.observability]
  betroffene_dokumente: [docs/research/prereg_*.md, RUNBOOK.md]
  umsetzungshinweise: [Verdikt-Ledger (attestiert) ist Quelle für RESOLVED; Reifeblick darf nie „UNWATCHED" sagen, wenn ein Verdikt existiert; Zeit injizierbar; Zerlegung nach Status im Health-Befund mitliefern]
  tests_erforderlich: [Contract: Ledger-Fixture mit 19 Einträgen → 19 Zustände, 0 unklassifiziert; Regression 0879a65c; injiziertes now für Fristen]
  validierung: [Voll-Gate, CI, live read-only: prereg-maturity zeigt 19/19 klassifiziert]
  akzeptanzkriterien: [Kein Ledger-Eintrag ohne Zustand; UNWATCHED nur ohne Watcher UND ohne Verdikt UND ohne Termin]
  risiken: [Kriterien versiegelter Prä-Regs nicht anfassen (nur Sichtbarkeit)]
  doku_sync_pflicht: [docs/research: Zustandsmodell]
  naechster_folgeschritt: Operator entscheidet die 8 Fälligen mit korrekter Liste.
```

---

## 7. Operator-Entscheidungen (gated, nur du)

1. **STAB-05a** Backup-Passphrase setzen + offline sichern (P0).
2. **STAB-06a** K1 zählen/entscheiden; 7 UNWATCHED je Watcher/Termin/Archiv (P0).
3. **STAB-07** GO für das Restart-/Deploy-Fenster nach Merge von 02/03/04.
4. **STAB-16** restliche TV-Alerts scharf stellen.
5. Identity **Stufe 2** (Repo-/Pfad-Umbenennung): jetzt nicht — bestätigen oder widersprechen.
6. **STAB-15** Branch Protection `strict=true`.
7. Codex-Reaktivierung für dieses Paket (hebt D-186 partiell auf) — wird als D-Eintrag festgehalten.
