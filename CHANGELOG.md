# CHANGELOG.md

## 2026-03-21

### Added

- Typed `ExecutionMode` contract with fail-closed live mode validation
- Append-only operator review journal and resolution tracking
- Root platform documents: architecture, security, risk policy, runbook, Telegram interface
- `CONFIG_SCHEMA.json` and `DECISION_SCHEMA.json`
- Disabled-by-default persona, TTS, STT, and avatar interface stubs
- Extended Telegram operator commands for exposure, signals, journal, approve/reject, daily summary, and incident intake

### Changed

- KAI assumptions hardened in `ASSUMPTIONS.md`
- Telegram operator audit records now keep command arguments

### Security

- `live` requires explicit aligned settings and fails closed otherwise
- operator approvals over Telegram remain audit-only unless a future explicit approval queue is connected
