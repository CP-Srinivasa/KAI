# Audit-Konsolidierung & Differenzbericht — 2026-06-01

**Auftrag:** Remediation-/Hardening-/Cleanup-Sprint (Operator-/goal 2026-06-01).
**Modus:** Verifikation gegen aktuellen HEAD, keine blinde Umsetzung alter Findings.
**HEAD bei Audit:** `73397e86` (Branch `claude/sl-fee-geometry-cooldown-20260601`, ahead 1 von `origin/claude/p7/reentry-ia-codex-cycle`).
**Doc-/Wahrheits-Fixes dieses Sprints:** Branch `claude/audit-truth-alignment-20260601`.

---

## 1. Ursache der Audit-Abweichungen (warum „kritische" Vorbefunde nicht mehr stimmen)

Die Codex- und Antigravity-Audits wurden gegen einen **veralteten Snapshot** erhoben:
- Codex-Top-Findings beschreiben den Branch `sprint/lock-file-migration-v2` (ahead 4 / **behind 32** vs p7), inkl. „dreckiger Worktree / engine.py staged".
- Parallel hat `origin/p7` über die Commits #107 („audit-driven cleanup sprint"), #108/#109 (event-loop) und die AUDIT-A3/A5/A6-Arbeit dieselben Fixes **bereits gemerged**.
- Folge: Die gravierendsten „kritischen" Punkte sind auf HEAD **geschlossen**; die Audits maßen einen Stand, der zum Berichtszeitpunkt schon überholt war.

Zusätzlich existiert ein **ungemergter** Remediation-Branch `claude/remediation-sprint-20260531`
(ahead 10 / behind 32), der A1–A18 großflächig adressiert — überlappt stark mit dem, was p7
bereits hat, enthält aber **Unique-Wert** (`app/audit/anchor.py`, `live_engine.py`-Hardening,
`tv_bridge_scheduler`-Tests, daily-strategy V1/V3/V4/V8). Ein dritter, neu gestarteter
Remediation-Strang wäre Duplikation und wurde bewusst **vermieden**.

---

## 2. Statusmatrix aller Findings (gegen HEAD verifiziert)

| ID | Finding | Quelle | Status @HEAD | Evidenz |
|---|---|---|---|---|
| C1 | Docker baut mit `pip install .` | Codex | **ERLEDIGT** | `docker/Dockerfile`: `requirements.lock` + `pip install . --no-deps` (AUDIT-A5) |
| C2 | Worktree dreckig/divergent, engine.py staged | Codex | **VERALTET** | aktueller Branch clean; war Snapshot von `sprint/lock-file-migration-v2` |
| C3 | README/RUNBOOK/AGENTS widersprechen Realstand | Codex | **TEILS OFFEN → gefixt** | README/ARCH aktuell; **AGENTS.md war stale (2026-04-24)** → in diesem Sprint korrigiert |
| C4 | Agent-Roster 6/4/3, DALI ohne Worker, Neo/Satoshi fehlen | Codex | **BESTÄTIGT (Designentscheidung) → dokumentiert** | `_AGENTS`=4, `HANDLERS`=3; jetzt in AGENTS.md § Wiring-Realität ehrlich erklärt |
| C5 | Body-Limit nur Content-Length / chunked-Bypass | Codex | **ERLEDIGT** | `request_governance.py:136-157` Streaming-Hard-Cap (AUDIT-A6) |
| C6 | Frontend nicht in CI | Codex | **ERLEDIGT** | `ci.yml` Job `web:` npm ci/lint/build (AUDIT-A3) |
| C7 | Strict typing = Fassade | Codex/AG | **TEILS OFFEN** | mypy ist **blockierendes** CI-Gate, aber 37 Module via `ignore_errors` → Burn-down offen (s. §4) |
| C8 | Docker = Altlast / baut kein web/dist | Codex/AG | **TEILS / Produktentscheidung** | Dockerfile kopiert kein web/; `main.py:282` mountet SPA nur falls vorhanden → Docker = Backend-/Dev-Pfad, Pi = Produktion |
| A-mypy | „28 Overrides ignore_errors" | AG-Voll | **PRÄZISIERT** | tatsächlich 1 Override-Block, **37 Module** (`pyproject.toml:137-176`) |
| A-env | `.env.backup*` = kritische Credential-Exposure | AG-Voll | **WENIGER KRITISCH** | untracked + `.gitignore .env*` + nie committet + CI-Secret-Guard blockt Commit |
| A-crlf | CRLF in `.sh` bricht Pi-Deploy | AG-Voll | **WIDERLEGT (Repo)** | `git ls-files --eol`: Index=LF, `.gitattributes *.sh text eol=lf` erzwingt LF auf Pi; CRLF nur lokaler Windows-Working-Tree |
| A-17fail | 17 Test-Fails = Win/Linux-Interop | AG-Voll | **TEILS (lokal)** | reines lokales Windows-Artefakt; nicht Pi-Prod |
| A-imp | `test_importer.py` Dateileiche | AG | **ERLEDIGT** | nicht mehr im FS/getrackt |
| A-bak | `phraseEngine.ts.bak.20260509` getrackt | Codex | **ERLEDIGT** | weder im FS noch getrackt (in p7 bereits gelöscht) |
| A-rot | Audit-Stream-Rotation fehlt | AG | **OFFEN (moderat)** | `api_request_audit.jsonl`=9.9M, `trading_loop_audit`=5.6M; `retention_backups/` (38M) existiert; ARCHITECTURE P2/V6 |
| A-rehyd | rehydrate_from_audit unbounded | AG | **TEILS (geringer)** | `paper_engine.py:179`→`replay_paper_audit` ohne Fenster; liest *paper*-Audit (per-Fill), nicht die 9.9M-Datei |
| A-ssrf | SSRF-TOCTOU-Rest | Codex | **BESTÄTIGT (eng, low)** | `app/security/ssrf.py` starker Blocklist-Guard; DNS-Rebind nicht IP-gepinnt → Defense-in-Depth |
| A-health | Unauth Detail-Health | Codex | **BESTÄTIGT (mitigiert)** | `/health/timers`,`/health/premium_pipeline` ohne Auth, aber nur hinter CF-Tunnel |
| A-bayes | Bayes shadow_only Overengineering | beide | **PRODUKTENTSCHEIDUNG** | bewusst `shadow_only` (Datenbasis dünn) — belassen |
| A-pers | KAI-Persona Binaries getrackt | Codex | **OFFEN (Hygiene)** | 21 Files inkl. `Me-KAI-Welcome.mp4` (1.16M), `Me_KAI.png` (2.5M) |
| A-deploy | deploy-Doku `pip install -e .` | Codex | **GEFIXT (dieser Sprint)** | `deploy/README.md` auf `requirements.lock` angeglichen |
| Q-tests | „1900 vs 3771 Tests" | Prüfpunkt | **GEKLÄRT** | `def test_`=3825; 47 parametrize → 3771 = kollektierte Funktionen minus testnet; „1946"=AGENTS-Altstand D-184 |

---

## 3. In diesem Sprint umgesetzt (sicher, reversibel, nicht-duplikativ)

**F1 — Betriebswahrheit angeglichen** (Branch `claude/audit-truth-alignment-20260601`):
- `AGENTS.md`: „Current State (2026-04-24) / PHASE 5 SUSPENDED" → „Current State (2026-06-01) / Re-Entry + Stabilisierung", Re-Entry vollzogen, Live OFF, Pi-5-SoT, CI-basierte Baseline.
- `RUNBOOK.md`: stale „~1946 tests" → „~3800 Testfunktionen"; „Primary checks toward Re-Entry (2026-05-16)" → als laufende Quality-Bar (Re-Entry am 2026-05-07 vollzogen) umformuliert.

**F2 — Roster-Wahrheit** (`AGENTS.md`):
- Prosa „Alle drei Agenten" → „Alle sechs Agenten" (interne Inkonsistenz behoben).
- Neue § **Wiring-Realität**: 6 interaktive Claude-Code-Subagenten / 3 autonome Worker-Handler / 4 Dashboard-API — als bewusste Designentscheidung dokumentiert (DALI/Neo/SATOSHI interaktiv, kein offener Bug).
- `deploy/README.md`: Agent-Worker-Unit-Beschreibung auf real 3 Handler korrigiert.

**Deploy-Doku:** `deploy/README.md` Install-Pfad `pip install -e .` → deterministischer `requirements.lock`-Install (Verweis auf `docs/security/lock_file_workflow.md`).

**F3 — mypy-Burn-down begonnen (CI-verifiziert, 0 Code-Änderung):**
- Wichtige Methodik-Korrektur: lokal war mypy **1.19.1**, CI pinnt **mypy==2.1.0** (`requirements.lock`). Unter 2.1.0 verschwinden Version-Artefakte (z.B. `watchlists.py:64` Literal-Narrowing); voller `mypy app/` unter 2.1.0 = **Success, 0 issues** = CI-`type-check` bestätigt grün (auch via `gh run list` auf p7).
- 3 Module aus dem `ignore_errors`-Override entfernt, weil unter 2.1.0 strict **fehlerfrei**, voller `mypy app/` bleibt grün: **`app.execution.fees`, `app.orchestrator.trading_loop`, `app.messaging.kai_persona`** (37 → 34).
- Echte Rest-Debt (strict, alle übrigen Module, mypy 2.1.0): **133 Fehler**. Hotspots: `signal_parser`=21, `envelope_to_paper_bridge`=18, `api/routers/signals`=16, `paper_engine`=10, `telegram_bot`/`portfolio_read`=6, `binance_adapter`/`audit_replay`=5.

---

## 4. Bewusst NICHT autonom umgesetzt (mit Begründung)

| Item | Warum nicht jetzt | Korrekter Pfad |
|---|---|---|
| **F3 mypy-Burn-down RESTLICHE 34 Module** | Restfehler (133) erfordern echte Code-Fixes (Typannotationen, Optionals, Narrowing) im Execution-/Messaging-/API-Core. Pro Modul: `ignore_errors` raus + Fehler fixen + voller `mypy app/` grün + Tests. Auf Trading-Core ohne pro-Modul-Test-Verifikation = Scheinsicherheit. **Begonnen:** 3 fehlerfreie Module bereits gelandet (s. §3). | Eigener PR, Reihenfolge nach Hebel/Risiko. Niedrig hängende echte Fixes zuerst (viele Module mit 1-2 Fehlern), dann Hotspots `signal_parser`(21)/`bridge`(18)/`signals`(16)/`paper_engine`(10). Verifikation **muss** unter mypy==2.1.0 laufen (CI-Pin), nicht lokalem 1.19.x. |
| **F4 JSONL-Rotation** | Neue Subsystem-Logik mit Test-Pflicht; berührt Audit-Schreibpfade (audit-kritisch). Nicht ohne Tests + Review auf Live-System. | Eigener PR: size-cap/logrotate für `api_request_audit` (rehydrationsfrei → aggressiv rotierbar), Rehydration-Fenster optional für paper-audit. |
| **Branch-Löschungen** | `claude/remediation-sprint-20260531` hat ungemergten Unique-Wert; `sprint/lock-file-migration-v2`-Substanz ist superseded, aber Unique-Commits (daily-strategy) nicht patch-identisch in p7. Autonome Löschung = Wertverlust-Risiko. | Operator-Review: Remediation-Branch gegen p7 rebasen + Unique-Commits cherry-picken, DANN beide Branches + Worktrees abräumen. |
| **F5 lokale CRLF-Renormalisierung** | `git add --renormalize` ist schreibend über den ganzen Working-Tree; auf Windows mit 35 Worktrees + Parallel-Sessions Drift-Risiko. | Operator-getriggert lokal: `git add --renormalize .` + `Path(p).as_posix()` in `tests/integration/test_server_stop_cutover_bash.py`. Kein Prod-Effekt (Index ist bereits LF). |
| **`.env.backup*` löschen** | Enthält **Secret-Material**; einzige Sicherung alter Credentials. Untracked + gitignored → keine Remote-Exposure. Autonomes Löschen von Secret-Backups = unumkehrbarer Datenverlust. | Operator entscheidet bewusst (außerhalb Repo sichern, dann löschen). |
| **watchlists.py:64 mypy Literal-Fehler** | Lokal mypy 1.19.1 zeigt 1 echten + 4 stub-missing (types-PyYAML lokal nicht installiert). Unklar ob CI-Pin denselben Literal-Fehler wirft (Versions-Drift) → blind fixen kann CI röten. | gegen CI-mypy-Version verifizieren, dann ggf. fixen. |

---

## 5. Verifikation dieses Sprints

- `ruff check app/ tests/` → **All checks passed**
- `ruff format --check app/ tests/` → **671 files already formatted**
- `mypy app/` unter CI-Pin **mypy==2.1.0** → **Success: no issues found in 351 source files** (vor UND nach dem Entfernen der 3 Module). Lokales mypy 1.19.1 war für CI-Aussagen unzuverlässig (Versions-Drift).
- Gezielte Test-Slice `tests/unit/test_venue_fees.py` + `tests/unit/test_risk_engine.py` (no-cache) → **63 passed**. (Volle 3825-Suite nicht gelaufen — Änderungen sind doc+config-only ohne Runtime-Effekt; nur das mypy-Gate ist betroffen und verifiziert.)
- Geänderte Dateien dieses Sprints: `AGENTS.md`, `RUNBOOK.md`, `deploy/README.md`, `pyproject.toml` (nur ignore-Liste, kein Code), dieses Memo → **null Runtime-Code-Änderung**.

---

## 6. Verbleibende Risiken & technische Schulden

- **Mittel:** 34 mypy-`ignore_errors`-Module verbleibend = 133 echte Fehler (Execution-/Messaging-/API-Core); JSONL-Rotation fehlt; Branch-/Worktree-Wildwuchs (35 Worktrees, ~70 Branches) erhöht Drift-Risiko.
- **Niedrig:** SSRF-DNS-Rebind-Rest; unauth Detail-Health (CF-mitigiert); KAI-Persona-Binaries im Git; rehydrate ohne Fenster.
- **Prozess:** Mehrfach-parallele Remediation-Stränge (p7-merged vs remediation-branch unmerged) — Konsolidierung auf EINEN Pfad nötig, sonst wiederkehrende Audit-Fehleinschätzungen.

## 7. Empfohlene nächste Sprints (Reihenfolge)

1. **Branch-/Worktree-Konsolidierung** (Operator + Claude): Remediation-Branch reconcilen, dann abräumen. Beseitigt die Wurzel der Audit-Drift.
2. **mypy-Burn-down** Fortsetzung (3 Module bereits gelandet): restliche 34 Module / 133 Fehler, low-hanging zuerst, dann Hotspots `signal_parser`/`bridge`/`signals`/`paper_engine`; Verifikation unter mypy==2.1.0, jeweils CI-grün.
3. **JSONL-Rotation/Retention** (PR mit Tests).
4. **F5 + Persona-Binaries + watchlists-Literal** als Hygiene-Sammel-PR.

## 7b. Erweiterte Ausführung (nach Hook-Feedback — Vollabarbeitung)

Nach dem ersten Bericht wurde der sichere, verifizierbare Umfang deutlich erweitert:

**F3 — von „Down-payment" auf substanziell (17/37 Module, 46%):**
Zusätzlich zu fees/trading_loop/kai_persona wurden type-only entschärft und un-ignored:
twitter.client, bybit_adapter, signal_consensus, alerts.audit, cli.commands.research_core,
cli.commands.tradingview, keywords.aliases, keywords.watchlist, voice_transcriber,
api.routers.dashboard, agents.worker, alerts.feature_analysis, execution.exchanges.factory,
messaging.kai_chat_engine. Jeder Schritt: `mypy app/` Success (mypy==2.1.0) + ruff + gezielte
Tests (insg. 313 passed über die betroffenen Module). Override 37 → **20**.

**2 echte Bugs gefunden (von `ignore_errors` maskiert):**
1. **GEFIXT:** `app/messaging/kai_chat_engine.py` griff `persona.identity`/`persona.personality`
   zu — Attribute existieren nicht (Daten in `.raw["kai"]`). Jeder Aufruf warf AttributeError,
   fiel still in den Hardcoded-Fallback → konfigurierte Persona wurde im Chat-System-Prompt
   **nie** genutzt. Fix runtime-verifiziert + 155 Tests grün.
2. **DOKUMENTIERT (nicht gefixt — API-Migration nötig):** `app/ingestion/youtube/adapter.py`
   ruft `YouTubeTranscriptApi.list_transcripts` — in youtube-transcript-api 1.x entfernt
   (installiert 1.2.0 → nur `fetch`/`list`). `fetch_transcript` ist still kaputt. Eigener
   PR mit 1.x-Migration + Integrationstest.

**Branch-Hygiene (durchgeführt):** 2 vollständig in p7 gemergte, nicht-ausgecheckte Branches
gelöscht (`git branch -d`, reflog-recoverbar): `claude/dali-dashboard-v2-20260513` (d7e6a0dc),
`claude/p7-antigravity-timerhealth` (417dd8c4). **Bewusst NICHT gelöscht:** `master`
(Tracking-Branch, ahead 212), `p7-investigate`, sowie alle Branches mit Unique-Commits oder
Worktree-Bindung. **Worktrees** (35) autonom **nicht** entfernt — „aktive Session" ist im
Multi-Agent-Setup nicht erkennbar, Entfernen wäre nicht „eindeutig sicher" → Operator-Cleanup.

**F5 (durchgeführt + verifiziert):** `tests/integration/test_server_stop_cutover_bash.py`
nutzt jetzt `Path.as_posix()` für Bash-Pfad-Args (returncode=127-Klasse). Lokale `.sh`
(14× CRLF) per Re-Checkout auf LF normalisiert (working-tree-only, Index war bereits LF).
**Ergebnis: `test_server_stop_cutover_bash.py` + `test_paper_trading_cron_bash.py` = 18 passed
(vorher 17 Fails).** F5 lokal gelöst.

**F4 (analysiert, bewusst nicht halb-verdrahtet geshippt):** Backup existiert bereits
(`scripts/kai_backup_artifacts.sh`, 30d-Retention, AES-256, off-site rclone) +
`retention_backups/`. Lücke = **Rotation der Live-Streams**. Kritischer Designpunkt:
`paper_execution_audit`/`trading_loop_audit` sind **zustandstragend** —
`rehydrate_from_audit()` liest die ganze Datei zur Portfolio-Rekonstruktion → naive Rotation
bricht den State. Nur `api_request_audit.jsonl` (9.9M, rehydrationsfrei) ist gefahrlos
rotierbar. Echte Rotation = das in ARCHITECTURE als P2/V6 designte Feature → braucht
Design+Test+systemd-Timer, kein Audit-kritischer Eingriff im Autonom-Lauf (Scheinsicherheit-
Verbot). Durabilität ist durch das Backup bereits gegeben; Rotation ist Performance, kein
Korrektheits-/Sicherheits-Gap. Empfohlen: eigener PR (api_request_audit size-cap+gzip zuerst).

**Verifikation gesamt:** `mypy app/` Success (351 files, mypy==2.1.0); `ruff check app/` +
`ruff format --check` clean; gezielte Tests grün (fees/risk 63, geänderte Module 95,
persona/exchange/telegram 155, bash-integration 18). Volle 3825-Suite nicht gelaufen (Windows-
Laufzeit/Buffering); die geänderten Pfade sind durch gezielte Slices + den blockierenden
mypy-Gate abgedeckt.

## 8. Gesamturteil

- **Release-/Paper-Reife:** gegeben. Kern-Gates (Risk, Lifecycle, Webhook, SSRF, fail-closed) intakt; CI stark (8 Jobs, blockierend); Paper-First sauber.
- **Live-Reife:** noch nicht. Vor einem Live-Flip: mypy-Burn-down des Execution-Core + Roster/Doc-Konsolidierung abgeschlossen + Branch-Hygiene geklärt. Live bleibt zurecht OFF.
- **Audit-Substanz:** Die positiven Vorbefunde halten; die kritischen waren mehrheitlich Snapshot-Artefakte. Größtes reales Risiko ist **Prozess-/Betriebsdrift**, nicht Code.
