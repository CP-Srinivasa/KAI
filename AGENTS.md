


## Current State (2026-06-01)

> **Hinweis:** Der frühere `PHASE 5 SUSPENDED`-Zustand (D-125, TradingView-Pivot)
> wurde mit dem **Pi-5-Cutover am 2026-05-07** abgelöst. Re-Entry ist vollzogen,
> `RE_ENTRY_MODE` ist live. Kanonische Betriebswahrheit: `README.md` + `ARCHITECTURE.md`
> + `DECISION_LOG.md`. Historischer April-Stand siehe Git-History dieser Datei.

| Field | Value |
|---|---|
| current_phase | `Re-Entry + Stabilisierung` (post-PHASE-5-suspension; D-125 aufgehoben am 2026-05-07-Cutover) |
| re_entry_status | `vollzogen -- RE_ENTRY_MODE live; D-125-Gates erfüllt; Kalender-Termin 2026-05-16 historisch überholt` |
| active_workstream | `Diversification/Asset-Reserve in paper (D-226/D-228/S3), Dispatch-Recall-Proxy (D-227), Deadlock-/Drift-Hardening (DS-20260531 V1-V5)` |
| source_of_truth | `Pi 5 (ubuntu@192.168.178.23), live seit 2026-05-07` |
| multi_agent_status | `Codex/Antigravity als externe LLMs nicht reaktiviert (D-186); KAI-interner 6-Agenten-Roster (Claude-Code-only) aktiv -- siehe § Agent Roster + Wiring-Realität` |
| live_execution | `OFF -- paper + operator-approval only (Live-Gates bleiben bis explizite Öffnung zu)` |
| liveness_watchdog | `hardened D-188; loop-open-deadlock-watchdog DS-20260531-V5 live (#113); externer 5-min Service-Watchdog auf Pi` |
| provenance_persistence | `V1 active (D-125/SAT-C-PROV-20260422-001) -- HMAC-seal, zero-downtime rotation D-183, replay-guard D-179` |
| living_architecture | `D-106 -- aktive Architektur ist CLAUDE.md + ARCHITECTURE.md; historische Doku unter docs/archive/` |
| documentation_policy | `D-99 -- keine Sprint-Contract-Docs; Entscheidungen in DECISION_LOG.md oder Code-Kommentaren` |
| baseline | `Testsuite + ruff + mypy + Frontend-Build sind CI-Gates (.github/workflows/ci.yml) -- maßgeblich ist der CI-Lauf, nicht eine eingefrorene Zahl` |
| cloudflare_tunnel | `kai-trader.org active (D-167); Single-Origin-Regel: nur Pi 5 als Connector` |
| cron_status | `Pi 5 systemd-Timer (paper-trading 10 min, daily-strategy, health-probes) -- Windows Task Scheduler nur für lokale Backups/Mirror` |

> **Verbindliches Betriebsdokument für alle Coding-Agenten.**
>
> Aktiver Agent: **Claude Code** (Architekt + Implementierer)
> Multi-Agent-Modell (Codex, Antigravity) **endgültig nicht reaktiviert bis TV-Pivot Re-Entry** (D-186, 2026-04-24) — archivierte Roster-Historie in `docs/archive/AGENT_ROLES.md`.
>
> Dieses Dokument lesen, bevor eine einzige Zeile Code angefasst wird.

## Agent Roster (D-141, 2026-04-15; erweitert auf 6)

Alle **sieben** Agenten werden **ausschließlich von Claude Code** ausgeführt — nicht von Codex,
nicht von Antigravity, nicht von externen LLMs. Permissions-Boundary: read + report;
write nur über `app/agents/tools/guarded_write.py` mit Audit-Trail. Kanonische Roster-Quelle
(SSOT) ist `app/api/routers/agents.py::_AGENTS` (von Worker + Telegram-Menü importiert).

| Agent | ID | Modi | Rolle |
|---|---|---|---|
| **SENTR** | `a708ac129e9cf2569` | inspect, report | Security/Inspection — prüft Code, Configs, Secrets, Auditierbarkeit |
| **Watchdog** | — | `check`, `report` | Health/Drift-Monitor — verifiziert Pipeline-Outputs, Quality-Bar, Regressionen |
| **Architect** | `a14a2b53ba50ebadd` | review, propose | Architektur/Struktur — bewertet Module, Abhängigkeiten, Refactor-Vorschläge |
| **DALI** | — | `audit`, `propose`, `implement` | Design/UI — UI/UX-Audit, Redesign-Konzepte, Patch-Proposals für Dashboard, Telegram, Visual System |
| **Neo** | — | `analyze`, `fix` | Code-Tiefenanalyse — Root-Cause-Debugging, Concurrency, Datenfluss, Performance, Refactor mit Risikoabwägung |
| **SATOSHI** | — | `review`, `verify` | Kryptographie/Wallet/Smart-Contract/Forensik — Signaturen, HMAC, Webhooks, Key-Material, Tokenomics-vs-Onchain, Doc-vs-Code-Konsistenz, Provenance, Threat-Models |
| **KAI-Finder** | — | `search`, `propose` | Quellen-/Daten-Discovery — neue Feeds/APIs recherchieren, bewerten, vorschlagen (Legal/Stabilität/Kosten) |

(Modi = API-Command-Surface `_AGENTS.modes`; die interaktiven `.claude/agents/*.md`-Subagenten können feingranularere Rollen haben.)

**Dropbox-Pattern (honest by design):**
- Findings/Reports: `artifacts/agents/{sentr,watchdog,architect,dali,neo,satoshi,kai-finder}/*.jsonl`
- Status `live` = JSONL in den letzten 24h; `prepared` = Verzeichnis existiert, leer; `unavailable` = kein Verzeichnis
- Kein Fake-Heartbeat, keine Mock-Daten

**DALI Patch-Proposals (D-152):**
- `implement`-Modus schreibt **nie direkt** in Code. Output landet als strukturierter Diff-Proposal in `artifacts/agents/dali/proposals.jsonl` (Felder: `target_path`, `diff`, `rationale`, `scope`, `risk`).
- Operator/Claude Code reviewt und wendet an — kein Auto-Apply.
- Scope-Allowlist (empfohlen, vom Operator verifiziert): `web/src/**`, `app/messaging/telegram_menu.py`, `web/tailwind.config.js`, `web/src/theme/**`, `.claude/agents/**`.
- Proposals außerhalb der Allowlist werden im `risk`-Feld als `out_of_scope` markiert und müssen explizit freigegeben werden.

**Master-Rules** (gelten für alle Agenten): CLAUDE.md Core Rules + Deploy-Regeln + Testing-Regeln.

### Wiring-Realität (ehrlich, Stand 2026-06-30)

Der 7-Agenten-Roster existiert auf unterschiedlich tief verdrahteten Ebenen — das ist
**bewusst so**, kein halbfertiges Feature. Die `_AGENTS`-SSOT macht die Grenze jetzt explizit
über das Feld `wiring` (`autonomous` vs `interactive`), sodass das Dashboard keine autonome
Ausführung suggeriert, die ein interaktiver Agent nie leistet. Der Contract-Test
`tests/unit/test_agents_roster_contract.py` erzwingt: jeder Worker-`HANDLERS`-Agent **muss**
`wiring="autonomous"` sein.

| Ebene | Mechanismus | Verdrahtete Agenten |
|---|---|---|
| **Dashboard-API-Surface (SSOT)** | `app/api/routers/agents.py` (`_AGENTS`) | **alle 7** (sentr, watchdog, architect, dali, neo, satoshi, kai-finder) — Feld `wiring` unterscheidet |
| **Autonomer JSONL-Queue-Worker** (`wiring="autonomous"`) | `app/agents/worker.py` (`HANDLERS`, cron/systemd) | **3**: `watchdog` (check/report), `sentr` (inspect/report/kyt-review/governance-audit), `architect` (review/propose) |
| **Interaktiv** (`wiring="interactive"`, kein Worker-Handler) | `.claude/agents/*.md`, vom Hauptagent on-demand dispatcht | **4**: dali, neo, satoshi, kai-finder |

**Konsequenz / Designentscheidung:** DALI, Neo, SATOSHI und KAI-Finder laufen als **interaktive**
Subagenten (Operator-/Hauptagent-getriggert), nicht als autonome Queue-Worker — für Design-/
Tiefenanalyse-/Krypto-/Discovery-Reviews ist das die richtige Granularität (kein sinnvoller
cron-Default). Sie sind im Dashboard-API gelistet (Status-Sichtbarkeit) und über `wiring`
ehrlich als interaktiv markiert; eine an sie enqueuete Command fährt **kein** autonomer Handler
aus. Autonome Worker-Handler für sie wären ein eigener Sprint mit Tests — **kein offener Bug.**

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
