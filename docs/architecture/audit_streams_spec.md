# Audit Stream Rotation Policy

Status: V6 specification only, 2026-05-21. No runtime rotation change is
enabled by this document.

## Kurzbefund

AuditStream is a trust boundary. Rotation may improve disk hygiene, but it
must not remove rows from the active evidence chain or change JSONL schemas.
The current repo already has a logrotate pattern for service logs in
`deploy/logrotate/kai`; it does not rotate artifact JSONL streams.

V6 therefore chooses Variant A: logrotate-compatible policy, implemented in a
later operational step only after active schema work is merged and Trail API
reader compatibility is checked.

## Vorbedingungen

Operational rotation for audit JSONL files is allowed only when all are true:

1. `pytest tests/integration/test_premium_pipeline_e2e.py -v` is green.
2. No active PR/worktree change is modifying AuditStream schemas.
3. Premium Trail API consumers remain compatible with the selected stream set.
4. A dry-run with the exact target config has been reviewed:
   `sudo logrotate -d /etc/logrotate.d/kai-audit-streams`.
5. A state-file isolated force run has been tested on a copied fixture:
   `sudo logrotate -f -s /tmp/kai-audit-streams.state <fixture-config>`.

## Stream Classes

### Class A: rotation-ready operational audit

These streams are audit evidence, but not primary inputs for the Premium Trail
join as of this spec:

- `artifacts/api_request_audit.jsonl`
- `artifacts/operator_api_guarded_audit.jsonl`
- `artifacts/mcp_write_audit.jsonl`
- `artifacts/operator_commands.jsonl`
- `artifacts/operator_review_journal.jsonl`
- `artifacts/session_log.jsonl`
- `artifacts/backup_audit.jsonl`
- `artifacts/watchdog_incidents.jsonl`

Policy:

- rotate by size, not only by time, because request bursts drive disk growth.
- keep current file path stable after rotation.
- preserve every rotated file as readable JSONL.
- use `create`, not `copytruncate`, because these writers generally open,
  append, and close per event. `copytruncate` has a small write-loss window.
- use compression after one rotation delay so emergency reads can inspect the
  newest archive without decompression.

Candidate logrotate stanza:

```conf
/home/ubuntu/ai_analyst_trading_bot/artifacts/api_request_audit.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/operator_api_guarded_audit.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/mcp_write_audit.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/operator_commands.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/operator_review_journal.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/session_log.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/backup_audit.jsonl
/home/ubuntu/ai_analyst_trading_bot/artifacts/watchdog_incidents.jsonl {
    size 25M
    rotate 30
    missingok
    notifempty
    compress
    delaycompress
    dateext
    dateformat -%Y%m%d-%s
    create 0640 ubuntu ubuntu
    su ubuntu ubuntu
}
```

### Class B: Trail-critical audit streams

These are active inputs for the Premium Trail or execution recovery path:

- `artifacts/telegram_channel_raw.jsonl`
- `artifacts/telegram_message_envelope.jsonl`
- `artifacts/telegram_approval_send.jsonl`
- `artifacts/bridge_pending_orders.jsonl`
- `artifacts/paper_execution_audit.jsonl`
- `artifacts/target_completion_audit.jsonl`
- `artifacts/trading_loop_audit.jsonl`
- `artifacts/entry_watcher_audit.jsonl`

Policy:

- do not operationally rotate in V6 Phase 1.
- rotation is allowed only after readers can load current plus rotated
  segments, or after DuckDB compaction becomes the canonical historical read
  path for the affected stream.
- `paper_execution_audit.jsonl` requires extra care because audit replay,
  portfolio reads, Premium Trail, and recovery logic read it directly.

Reason: current Trail API reads only the current files via
`app.api.routers.premium_signals.trail` and `build_trail(...)`. Rotating these
files before reader support would not break JSON schema, but it would hide
older rows from the live Trail response.

## Reader Compatibility Rule

All rotated JSONL archives remain source-of-truth evidence. A reader that needs
historical continuity must either:

- read `current + rotated plain + rotated .gz` in timestamp order, or
- read from a compacted DuckDB table whose watermark includes the rotated
  segment before the segment is deleted.

Until that exists, Trail-critical streams stay unrotated and are managed by
backup/transfer plus manual operator review.

## No Schema Changes

Rotation must never:

- remove or rewrite JSON keys.
- strip `correlation_id`.
- convert JSONL to non-JSONL in the active path.
- delete `paper_execution_audit` events.
- write synthetic replacement events.

Compression affects only archived files. The active file remains normal JSONL.

## Reproducible Test Plan

Use copied fixtures, never production artifacts:

1. Create a temp directory with sample JSONL rows:
   `mkdir -p /tmp/kai-rotate-fixture/artifacts`.
2. Copy representative files or generate small JSONL fixtures there.
3. Point a temporary logrotate config at the temp paths.
4. Run dry-run:
   `logrotate -d -s /tmp/kai-rotate-fixture/state <fixture-config>`.
5. Run forced rotation:
   `logrotate -f -s /tmp/kai-rotate-fixture/state <fixture-config>`.
6. Assert:
   - active `.jsonl` file exists after rotation.
   - rotated file exists.
   - every line in active and rotated files remains parseable JSON.
   - row count across active plus rotated equals pre-rotation row count.
   - `correlation_id` values, where present, are unchanged.

## Migration Plan

Phase 1:

- Install a separate `kai-audit-streams` logrotate file only for Class A.
- Leave `deploy/logrotate/kai` service-log behavior unchanged.
- Run dry-run in operator review before installation.

Phase 2:

- Add a rotated-segment reader helper or DuckDB-backed historical read path.
- Extend Premium Trail tests to cover current plus rotated segments.
- Only then consider Class B rotation.

Phase 3:

- If DuckDB compaction becomes canonical, allow shorter filesystem retention
  only after compaction watermark confirms the rotated segment was ingested.

## Operator Decision Anchors

- retention size: default candidate is `25M`.
- retention count: default candidate is `30` rotations.
- install path: `/etc/logrotate.d/kai-audit-streams`.
- Class B rotation requires a separate operator sign-off.
