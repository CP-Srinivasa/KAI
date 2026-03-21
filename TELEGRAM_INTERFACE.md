# TELEGRAM_INTERFACE.md — KAI Operator Surface Contract

> **Kanonisches Dokument. Sprint 38+38C. Datum: 2026-03-21.**
> Alle Aenderungen am Telegram-Command-Surface MUESSEN in diesem Dokument
> und in `docs/contracts.md §49` innerhalb desselben Sprints reflektiert werden.

---

## Leitprinzip

**Telegram ist First-Class-Operator-Surface — niemals Execution-Surface.**

Telegram-Kommandos duerfen:
- System-Status und Audit-Daten lesen (read_only) — via MCP canonical read tools
- Operator-Intent als Audit-Eintraege aufzeichnen (guarded_audit)
- Notfall-Steuerung mit expliziter Bestaetigung und dry_run-Gate ausloesen (guarded_write)

Telegram-Kommandos duerfen NICHT:
- Live-Trading-Pfade oeffnen oder triggern
- Approval-State von Decisions automatisch aendern
- Orders einreichen, routen oder promoten
- Core-DB-State ohne expliziten Operator-Gate mutieren

---

## Kanonische Telegram-Command-Surface (Sprint 38+38C)

| command | surface_class | source_of_truth (MCP) | cli_ref | forbidden_side_effects |
|---|---|---|---|---|
| `/status` | read_only | `get_operational_readiness_summary()` | `research readiness-summary` | none |
| `/health` | read_only | `get_provider_health()` | `research provider-health` | none |
| `/positions` | read_only | `get_handoff_collector_summary()` [provisional proxy] | `research handoff-collector-summary` | none; kein Live-Positions-Pfad |
| `/exposure` | read_only | static stub | — | none |
| `/risk` | read_only | `get_protective_gate_summary()` | `research gate-summary` | none |
| `/signals` | read_only | `get_signals_for_execution(limit=5)` | `research signal-handoff` | kein Routing, keine Execution, kein Promote |
| `/journal` | read_only | `get_review_journal_summary()` | `research review-journal-summary` | none |
| `/daily_summary` | read_only | `get_decision_pack_summary()` | `research decision-pack-summary` | none |
| `/approve <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/reject <dec_ref>` | guarded_audit | audit-only: `artifacts/operator_commands.jsonl` | `research review-journal-append` | kein Live-Execution, kein Routing, kein State-Change |
| `/pause` | guarded_write | `RiskEngine.pause()` — dry_run gated | — | kein Trading-Trigger; dry_run=True = no-op |
| `/resume` | guarded_write | `RiskEngine.resume()` — dry_run gated | — | kein Trading-Trigger; dry_run=True = no-op |
| `/kill` | guarded_write | `RiskEngine.trigger_kill_switch()` — 2-Step + dry_run gated | — | Notfall-Only; dry_run=True = no-op |
| `/incident <note>` | guarded_audit | `get_escalation_summary()` (MCP read) + audit-append | `research escalation-summary` | keine State-Mutation, kein Auto-Remediation |
| `/help` | read_only | static | — | none |

---

## Surface-Klassen (kanonisch)

| Klasse | Bedeutung | Beispiele |
|---|---|---|
| `read_only` | Kein Schreiben, kein State-Wechsel; backed by MCP canonical read | `/status`, `/health`, `/risk`, `/signals`, `/journal` |
| `guarded_audit` | Schreibt nur append-only Audit-Log — kein Execution-Seiteneffekt | `/approve`, `/reject`, `/incident` |
| `guarded_write` | Mutiert Risk-Engine-State — explizit dry_run gated | `/pause`, `/resume`, `/kill` |

**Hinweis zu `/incident`**: `guarded_audit` — liest zusaetzlich `get_escalation_summary()` (MCP) fuer Kontext.
Audit-Eintrag wird **immer** geschrieben (via `_audit()` vor dem Handler). MCP-Fehler wird fail-closed abgefangen.

---

## Implementation-Status (Sprint 38+38C — vollstaendig)

| command | Status |
|---|---|
| `/status` | produktiv — `get_operational_readiness_summary()` (MCP) |
| `/health` | produktiv — `get_provider_health()` (MCP) |
| `/positions` | produktiv — `get_handoff_collector_summary()` (MCP, provisional proxy) |
| `/exposure` | stub — kein Portfolio-Provider verbunden |
| `/risk` | produktiv — `get_protective_gate_summary()` (MCP) |
| `/signals` | produktiv — `get_signals_for_execution(limit=5)` (MCP) |
| `/journal` | produktiv — `get_review_journal_summary()` (MCP) |
| `/daily_summary` | produktiv — `get_decision_pack_summary()` (MCP) |
| `/approve` | audit-only — `_validate_decision_ref()` + Audit-Log-Eintrag |
| `/reject` | audit-only — `_validate_decision_ref()` + Audit-Log-Eintrag |
| `/pause` | produktiv — dry_run gated; `RiskEngine.pause()` |
| `/resume` | produktiv — dry_run gated; `RiskEngine.resume()` |
| `/kill` | produktiv — 2-Step confirm + dry_run gated |
| `/incident` | produktiv — `get_escalation_summary()` (MCP) + Audit-Log |
| `/help` | produktiv — statische Kommando-Liste |

---

## Kanonische Inventory-Funktion

`get_telegram_command_inventory()` in `app/messaging/telegram_bot.py` liefert:
- `read_only_commands` — Liste der read_only-Kommandos (aus `_READ_ONLY_COMMANDS`)
- `guarded_audit_commands` — Liste der guarded_audit-Kommandos (aus `_GUARDED_AUDIT_COMMANDS`)
- `canonical_research_refs` — Mapping command → CLI-research-commands

Diese Funktion wird in Tests und im Contract verifiziert:
`test_telegram_command_inventory_references_registered_cli_research_commands` MUSS gruen sein.

---

## Command-Ref-Validierung (Startup-Check)

`TelegramOperatorBot._collect_invalid_command_refs()` prueft beim Initialisieren:
- Alle CLI-refs aus `TELEGRAM_CANONICAL_RESEARCH_REFS` existieren in der CLI
- Bei ungueltigem Ref: `_invalid_command_refs` wird gesetzt
- In `_dispatch`: read_only Kommandos werden fail-closed geblockt wenn `_invalid_command_refs` nicht leer

**Wichtig**: `incident` ist `guarded_audit`, NICHT `read_only` — es wird NICHT durch den read_only-Refs-Check geblockt.
Der Audit-Eintrag wird immer geschrieben (vor dem Handler), unabhaengig vom MCP-Surface-Status.

---

## decision_ref Format (Sprint 38C)

`/approve` und `/reject` akzeptieren nur valide `decision_ref`-Werte:
- Format: `dec_` + 12 Hex-Zeichen (Regex: `^dec_[0-9a-f]{12}$`)
- Ungueltige Refs: fail-closed mit Fehlermeldung — kein Audit-Eintrag der Aktion (nur der Command wird geloggt)
- Implementierung: `_DECISION_REF_PATTERN` + `_validate_decision_ref()` in `telegram_bot.py`

---

## Sicherheitsinvarianten (kanonisch, nicht verhandelbar)

1. Nur `admin_chat_ids` duerfen Kommandos ausloesen — Unauthorized = fail-closed + audit-logged
2. Jedes Kommando wird **vor** Handler-Ausfuehrung in `artifacts/operator_commands.jsonl` geloggt
3. Unbekannte Kommandos: fail-closed mit generischer Meldung — kein Stack-Trace an Operator
4. `/approve` und `/reject` sind NICHT an Live-Execution gekoppelt — ausschliesslich Audit-Eintraege
5. `/kill` erfordert Zwei-Schritt-Bestaetigung (pending_confirm pattern) — ein `/kill` allein triggert nichts
6. Alle guarded_write Kommandos sind im `dry_run=True` Default inaktiv (no-op + log)
7. Kein Telegram-Kommando darf `execution_enabled=True` setzen oder signalisieren
8. Alle read_only MCP-Antworten MUESSEN `execution_enabled=False` und `write_back_allowed=False` enthalten
9. `_cmd_risk` nutzt `get_protective_gate_summary()` (MCP) — keine direkten RiskEngine-Private-Attribute
10. Telegram-Commands sind keine MCP-Tools — sie rufen MCP-Funktionen direkt auf, nicht via Tool-Layer

---

## Audit-Log-Format (`artifacts/operator_commands.jsonl`)

```json
{
  "timestamp_utc": "2026-03-21T10:00:00+00:00",
  "chat_id": 123456789,
  "command": "approve",
  "args": "dec-abc-123",
  "dry_run": true
}
```

Regeln:
- Append-only — keine Zeile wird ueberschrieben oder geloescht
- Wird auch bei Audit-Log-Fehler nicht unterdrueckt (Fehler als error geloggt; Kommando antwortet)
- Enthaelt keine Secrets, Tokens oder Credentials

---

## Was explizit ausgeschlossen ist (nicht verhandelbar)

- Kein Trading ueber Telegram
- Kein Auto-Routing ueber Telegram
- Kein Auto-Promote ueber Telegram
- Keine ungepruefte Telegram-Aktion mit Core-State-Wirkung (ausser guarded_write mit dry_run-Gate)
- Keine Verbindung zu Live-Exchange-Adaptern ueber Telegram
- Kein automatisches `/approve`, das eine Order ausloest
- Kein automatisches Decision-State-Update ohne Operator-Confirm
- Kein Auto-Remediation via `/incident`
