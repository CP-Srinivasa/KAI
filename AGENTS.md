


## Current State (2026-04-24)

| Field | Value |
|---|---|
| current_phase | `PHASE 5 (SUSPENDED, D-125)` |
| phase5_status | `SUSPENDED -- TradingView-Pivot active until 2026-05-16 (D-125, 2026-04-16)` |
| re_entry_gate | `≥200 resolved directional alerts OR ≥10 paper fills with PnL at 2026-05-16` |
| re_entry_data_side | `met (305 resolved / 54 fills) -- calendar half pending` |
| re_entry_date | `2026-05-16` (calendar-fixed; no further deferral per D-125 condition 1) |
| active_workstream | `TradingView-Pivot stages TV-1..TV-4b, operator-signal approval bridge, provenance-persistence V8 follow-ups` |
| thirty_day_gate_d117 | `resolved (D-186, 2026-04-24) -- no re-activation of Codex/Antigravity; re-eval after TV-pivot re-entry` |
| multi_agent_status | `paused -- D-186 decided no re-activation; re-eval 2026-05-16` |
| live_execution | `OFF -- paper + operator-approval only` |
| next_operator_milestone | `Pi-Migration cutover 2026-05-01 (D-7) -- see memory reminder_server_migration_pi.md` |
| liveness_watchdog | `hardened D-188 (2026-04-24) -- full-stack restart via server_start.sh + JSONL incidents; external UptimeRobot layer operator-action pending` |
| provenance_persistence | `V1 active (D-125/SAT-C-PROV-20260422-001, commit 66c638d) -- HMAC-seal, zero-downtime rotation D-183, replay-guard D-179; V8 follow-ups: signal_path_id in fresh rows, auth_method at TV-ingress, ReplayCache persistence, shared-token body-signing` |
| living_architecture | `D-106 -- active architecture is CLAUDE.md + docs/contracts.md (slim); historical docs under docs/archive/` |
| documentation_policy | `D-99 -- no sprint-contract docs; decisions live in DECISION_LOG.md or code comments only` |
| baseline | `~1946 tests, ruff clean (Stand D-184, 2026-04-22)` |
| cloudflare_tunnel | `kai-trader.org active (D-167, 2026-04-17); auto-started by scripts/server_start.sh` |
| cron_status | `KAI-PaperTrading every 10 min (Windows Task Scheduler)` |

> **Verbindliches Betriebsdokument für alle Coding-Agenten.**
>
> Aktiver Agent: **Claude Code** (Architekt + Implementierer)
> Multi-Agent-Modell (Codex, Antigravity) **endgültig nicht reaktiviert bis TV-Pivot Re-Entry** (D-186, 2026-04-24) — archivierte Roster-Historie in `docs/archive/AGENT_ROLES.md`.
>
> Dieses Dokument lesen, bevor eine einzige Zeile Code angefasst wird.

## Agent Roster (D-141, 2026-04-15)

Alle drei Agenten werden **ausschließlich von Claude Code** ausgeführt — nicht von Codex,
nicht von Antigravity, nicht von externen LLMs. Permissions-Boundary: read + report;
write nur über `app/agents/tools/guarded_write.py` mit Audit-Trail.

| Agent | ID | Modi | Rolle |
|---|---|---|---|
| **SENTR** | `a708ac129e9cf2569` | inspect, report | Security/Inspection — prüft Code, Configs, Secrets, Auditierbarkeit |
| **Watchdog** | — | `check`, `report` | Health/Drift-Monitor — verifiziert Pipeline-Outputs, Quality-Bar, Regressionen |
| **Architect** | `a14a2b53ba50ebadd` | review, propose | Architektur/Struktur — bewertet Module, Abhängigkeiten, Refactor-Vorschläge |
| **DALI** | — | `audit`, `propose`, `implement` | Design/UI — UI/UX-Audit, Redesign-Konzepte, Patch-Proposals für Dashboard, Telegram, Visual System |
| **Neo** | — | `analyze`, `propose`, `implement` | Code-Tiefenanalyse — Root-Cause-Debugging, Concurrency, Datenfluss, Performance, Refactor mit Risikoabwägung |
| **SATOSHI** | — | `crypto-review`, `forensic`, `threat-model`, `propose`, `implement` | Kryptographie/Wallet/Smart-Contract/Forensik — Signaturen, HMAC, Webhooks, Key-Material, Tokenomics-vs-Onchain, Doc-vs-Code-Konsistenz, Provenance, Threat-Models |

**Dropbox-Pattern (honest by design):**
- Findings/Reports: `artifacts/agents/{sentr,watchdog,architect,dali,neo,satoshi}/*.jsonl`
- Status `live` = JSONL in den letzten 24h; `prepared` = Verzeichnis existiert, leer; `unavailable` = kein Verzeichnis
- Kein Fake-Heartbeat, keine Mock-Daten

**DALI Patch-Proposals (D-152):**
- `implement`-Modus schreibt **nie direkt** in Code. Output landet als strukturierter Diff-Proposal in `artifacts/agents/dali/proposals.jsonl` (Felder: `target_path`, `diff`, `rationale`, `scope`, `risk`).
- Operator/Claude Code reviewt und wendet an — kein Auto-Apply.
- Scope-Allowlist (empfohlen, vom Operator verifiziert): `web/src/**`, `app/messaging/telegram_menu.py`, `web/tailwind.config.js`, `web/src/theme/**`, `.claude/agents/**`.
- Proposals außerhalb der Allowlist werden im `risk`-Feld als `out_of_scope` markiert und müssen explizit freigegeben werden.

**Master-Rules** (gelten für alle Agenten): CLAUDE.md Core Rules + Deploy-Regeln + Testing-Regeln.

## Cross-Reference-Pattern (Gedankenaustausch)

Subagents kommunizieren NICHT direkt miteinander — Architektur-Limit von Claude Code. Jeder Subagent läuft isoliert, gibt einen Report an den Hauptagent zurück. **Der Hauptagent (Claude Code) ist Dispatcher und Moderator.**

Gedankenaustausch zwischen Agenten erfolgt über das **Artifact-Cross-Ref-Pattern**:

1. Subagent A schreibt Finding mit eindeutiger ID (`finding_id`/`report_id`/`proposal_id`/`tm_id`/`impl_id`) in `artifacts/agents/{a}/{kind}.jsonl`.
2. Hauptagent liest, übergibt die ID beim Folgeaufruf an Subagent B im Prompt: *"siehe NEO-F-014 in artifacts/agents/neo/findings.jsonl, prüfe Krypto-Pfad-Implikationen"*.
3. Subagent B liest die Quelle, antwortet, schreibt eigenes Artifact mit `cross_ref`-Feld:
   ```json
   {"ts":"...","finding_id":"SAT-C-031","cross_ref":["NEO-F-014"],"category":"...","detail":"..."}
   ```
4. Hauptagent moderiert iterativ, hält die Kette nachvollziehbar.

**Vorteil:** Auditierbare Spur, kein impliziter State, jede Cross-Bewertung rekonstruierbar.
**Limit:** Latenz höher als bei direktem Crosstalk; Hauptagent muss aktiv routen.

Auto-Routing-Pflicht (welcher Agent bei welchem Topic, inkl. Parallel-Aktivierung) ist in **CLAUDE.md § Auto-Routing-Pflicht** definiert.

## Operator-Trust-Boundary: `monitor/*` (D-177, 2026-04-22)

Die Dateien unter `monitor/` (u. a. `social_accounts.txt`, `keywords.txt`,
`entity_aliases.yml`, `watchlists.yml`, `alert_rules.yml`, `news_domains.txt`,
`website_sources.txt`, `youtube_channels.txt`, `podcast_feeds_*`, `hashtags.txt`,
`historical_events.yml`) sind **operator-curated** und werden vom System als
vertrauenswürdig eingelesen. Sie steuern u. a.:

- Watchlist-basierten Trusted-Author-Gate-Bypass (D-176)
- Keyword-/Ticker-Extraktion und Entity-Alias-Auflösung
- Quellen-Klassifikation und Fetch-Whitelists
- Alert-Regeln und Routing

Wer Schreibzugriff auf `monitor/*` hat, kann Trust-Signale, Bypass-Regeln
und Source-Listen manipulieren. Die Trust-Grenze ist also das Dateisystem-ACL
der Checkout-Ordner, **nicht** ein separates Subsystem. Zugangsbeschränkung
ist Aufgabe des Operators (Datei-Permissions, Code-Review bei PR-Änderungen
an `monitor/*`).

Keine neue Angriffsfläche gegenüber bestehenden `monitor/*`-Loaders — dieser
Abschnitt macht die implizite Grenze explizit, damit spätere Reviewer sie
nicht erneut aufdecken müssen.

## Documentation Policy (D-99, 2026-03-24)

- Neue Sprint-Contract-Dokumente sind gestoppt (keine neuen `docs/sprint*_contract*.md` und keine neuen Sprint-Sections als Primärquelle).
- Entscheidungen werden nur noch dokumentiert als:
  - kurzer Code-Kommentar direkt am geänderten Verhalten, oder
  - kompakter 3-Zeilen-Eintrag in `DECISION_LOG.md`.
- `docs/contracts.md` bleibt kanonisch; historische Vertragsdoku liegt unter `docs/archive/contracts_archive.md`.
