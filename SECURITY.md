# SECURITY.md — KAI Sicherheits-Überblick (kanonisch)

**Stand:** 2026-07-02 · **Status:** lebendes Dokument · **Detail-Specs:** `docs/security/` (Index unten)

Kurzfassung des Threat-Models und der umgesetzten Schutzschichten. Dieses Dokument enthält bewusst **keine Live-Werte, Secrets oder Exploit-Details** — Betriebswerte liegen ausschließlich in der `.env` des Zielsystems, Befund-Details in den referenzierten Specs.

---

## Threat-Model-Grundsätze

1. **Fail-closed by default.** Fehlender Boot-Check, stale Marktdaten, Permission-Drift, Verify-Fehler ⇒ Aktion wird verweigert, nie still ausgeführt. Gilt für Gates, Feeds und Deploy-Checks gleichermaßen.
2. **Live-Execution mehrfach verriegelt (Triple-Flag + ACK-Sentinel).** Der Live-Pfad bleibt hart blockiert, solange nicht ALLE Arme gesetzt sind: zwei unabhängige Enable-Flags **plus** ein explizit vom Menschen getipptes ACK-Sentinel (`app/core/settings.py`, `LIVE_CANARY_ACK_SENTINEL`). Ein einzelner Flag-Flip kann Live nie scharfschalten; ein Tippfehler hält die Sperre.
3. **Kapital-Aktionen per-command HOTP-bestätigt.** Jede irreversible Kapital-Aktion verlangt eine HOTP-Bestätigung (RFC 4226, `app/security/hotp_auth.py`) — `verify_capital_confirm` koppelt HOTP + Plan-Hash + Idempotency. Kein Session-Timer-Theater (Red-Team-Befund S-001, `docs/security/red_team_response_v1.md`): Auth gilt pro Kommando, nicht pro Sitzung.
4. **Hardcoded Caps statt Settings.** Live-Limits (`app/security/live_caps.py`) sind Code-Konstanten, kein Env-Tuning — jede Änderung ist review-pflichtig. Kein Dev-Mode-Bypass-Flag.
5. **LN-Wert-Schicht invertiert authentifiziert + policy-gegated.** Lightning-Endpunkte laufen default-deny über eine explizite Allowlist (invertierte Auth-Logik; LN-Review-Blocker S-001 geschlossen); die Zahlungs-Schicht selbst bleibt policy-gegated inert. Macaroon-Berechtigungen: `docs/lightning_macaroon_matrix.md`.
6. **Ingress nur über Cloudflare Tunnel.** Der API-Server bindet an `127.0.0.1` (LAN-Bind nur als explizites Opt-in); Remote-Zugang ausschließlich über den Named Tunnel, Single-Origin-Regel: genau EIN Connector (Pi 5). Dashboard-Auth via Email-Allowlist; Service-Tokens werden auf `/dashboard/*` abgelehnt.
7. **systemd-Hardening + Watchdogs.** Dienste/Timer laufen mit gehärteten Unit-Files (`deploy/systemd/`); Liveness-Watchdogs prüfen echte Service-Zeitstempel statt Timer-Trigger; Post-Deploy-Smoke (failed-units-Check) ist Pflicht.
8. **Operator-Trust-Boundary `monitor/*`.** Operator-kuratierte Dateien (Trusted-Author-Bypass, Whitelists) sind über File-System-ACL geschützt — die ACL ist die Vertrauenslinie (siehe `AGENTS.md`).

## Audit- & Attestation-Kette

- **Append-only JSONL-AuditStreams** mit `correlation_id`-Pflicht auf allen pipeline-relevanten Events; Audit-Schreiben VOR der Aktion.
- **Tamper-evidente Hash-Chain** (`app/audit/`) über kritische Streams; attestierte Verdikte der Wahrheitskette (prereg → eval → prereg-check → attested verdict → family-status).
- **Externe Verankerung via OpenTimestamps** (L3-OTS, real auf Bitcoin verankert) — Runbook: `docs/deploy/l3_ots_cutover_runbook.md`.
- **Structured Reasoning statt roher Chain-of-Thought** + PII-/Secret-Redaction (`app/audit/structured_reasoning.py`, `sanitization.py`).

## Secrets-Handling

- **Keine Secrets im Repo.** `.gitignore` schützt `.env*`, DB-Dateien, Artifacts; Provenance-/Webhook-Secrets werden generiert, nie recycelt.
- **Injektion ausschließlich via Environment/`.env`**; `validate_secrets()` fail-fast außerhalb von dev; keine hardcoded Fallback-Keys.
- **Keine Plaintext-Keys, HOTP-Codes oder Seeds in Logs/Audits** — nur Counter-Werte und Hashes.
- Webhook-Eingänge (TradingView) sind shared-token + HMAC-signiert (`docs/security/tv_webhook_migration.md`).

## Bekannte offene Härtungen

- **In Arbeit: LN value-layer hardening** (generisch; Details erst nach Abschluss in den Specs). Die Wert-Schicht bleibt bis dahin policy-gegated inert.
- Voll-Stack-Live-Sicherheit (Vault, Co-Sign, Kill-Switch-Daemon) ist bewusst **Phase 1+** — Anker: `docs/security/live_trading_circuit_breaker_v1.md`; bis dahin gilt Phase-0-Posture (Light-Live-Spec, Live disabled).

## Spec-Index (`docs/security/`)

| Spec | Inhalt |
|---|---|
| [`red_team_response_v1.md`](docs/security/red_team_response_v1.md) | Red-Team-Review der Live-Trading-Architektur (Showstopper S-001..S-004, Anti-Hypothese §6) |
| [`decision_log_20260509.md`](docs/security/decision_log_20260509.md) | Operator-Entscheid: Option B „Light-Live" als Phase-0-Architektur |
| [`kai_light_live_phase0_spec.md`](docs/security/kai_light_live_phase0_spec.md) | Phase-0-Implementation-Spec (HOTP per-command, Caps, fail-closed) |
| [`live_trading_circuit_breaker_v1.md`](docs/security/live_trading_circuit_breaker_v1.md) | Voll-Stack-Spec (SATOSHI) — Anker für Phase 1+2 |
| [`operator_runbook_phase0.md`](docs/security/operator_runbook_phase0.md) | Operator-Setup-Runbook Phase 0 |
| [`phase0_pre_sprints.md`](docs/security/phase0_pre_sprints.md) | Pre-Sprints der Phase-0-Live-Security |
| [`governance_gates.md`](docs/security/governance_gates.md) | Governance-Gates (SENTR) |
| [`lock_file_workflow.md`](docs/security/lock_file_workflow.md) | Dependency-Lock-File-Workflow (Supply-Chain) |
| [`lock_file_migration_sprint_spec.md`](docs/security/lock_file_migration_sprint_spec.md) | Sprint-Spec der Lock-File-Migration |
| [`tv_webhook_migration.md`](docs/security/tv_webhook_migration.md) | TradingView-Webhook-Auth-Migration (Token + HMAC) |
| [`yubikey_bio_integration_plan_2026-05-24.md`](docs/security/yubikey_bio_integration_plan_2026-05-24.md) | YubiKey-Hardware-Stack-Integrationsplan |

## Weitere Verweise

- `app/security/README.md` — Modul-Regeln (hardcoded Caps, kein Bypass-Flag, Audit-vor-Action)
- `docs/architecture/execution_gate_chain_and_truth_layer_v2.md` — non-bypassable Gate-Kette (Schicht H)
- `docs/lightning_macaroon_matrix.md` — LN-Macaroon-Berechtigungsmatrix
- Supply-Chain in CI: `pip-audit` + `bandit` + Lock-File-Gate (siehe `RISK_REGISTER.md` R7)

**Meldeweg:** Solo-Betrieb — Befunde direkt an den Operator (Repo-Owner). Keine öffentliche Bug-Bounty; verantwortungsvolle Meldung über GitHub-Issues ohne Exploit-Details.
