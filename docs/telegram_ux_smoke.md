# Telegram UX Smoke Test — Phase 1 + 2

**Scope:** Verify Telegram surface after menu redesign (Phase 1) and command/voice/formatter English rollout (Phase 2).
**Mode:** Manual operator walk-through on live Telegram bot (paper environment).
**Pre-req:** Bot running, webhook healthy (`scripts/server_status.sh`), operator chat authorized.

---

## 0. Pre-flight

- [ ] `scripts/server_status.sh` → PID present, webhook registered, no error banner
- [ ] `/ping` (or equivalent) reaches bot — round-trip < 3s
- [ ] BotFather command list shows **English** labels (Main Menu, Portfolio, Signals, …)

Rollback handle: `scripts/server_stop.sh` stops the process; previous version via git checkout.

---

## 1a. Persistent reply-keyboard (Phase 2b)

- [ ] `/start` → bottom reply-keyboard docks with 3 rows: Status/Menu · Portfolio/Signals · Alerts/Help
- [ ] Keyboard stays visible after navigating sub-menus, sending signals, closing/reopening chat
- [ ] Tap `Portfolio` → same output as `/positions`
- [ ] Tap `Signals` → same output as `/signals`
- [ ] Tap `Alerts` → same output as `/alertstatus`
- [ ] Tap `Status` → same output as `/status`
- [ ] Tap `Menu` → inline main-menu card reappears
- [ ] Tap `Help` → `*KAI Help & Support*` card

## 1b. Ring-buffer auto-cleanup (Phase 2b)

- [ ] Tap `Status` 4× in a row → only the **last 3** outputs remain in chat; the oldest one is gone
- [ ] Mix tapped commands (Status · Portfolio · Signals · Alerts) — ring-buffer evicts in FIFO order per chat, depth 3
- [ ] Paste a `[SIGNAL]` envelope between taps → the signal confirmation message **stays permanent** (not auto-deleted)
- [ ] Paste a `[NEWS]` envelope between taps → NEWS card **stays permanent**
- [ ] Send a voice memo → draft preview **stays permanent** until `/ok` or `/cancel`
- [ ] Exchange-Response push (`[EXCHANGE_RESPONSE]`) → confirmation **stays permanent**

## 1. Menu surface (Phase 1)

### 1.1 Main menu
- [ ] `/menu` opens `*KAI Control Center*`
- [ ] 6 rows visible: System Status · Portfolio/Signals · Trades/Alerts · Auto Trading/Agents · Exchanges/Insights · Operations/Help
- [ ] No ASCII separators, no `(WIP)` markers, no umlaut garbling

### 1.2 Navigation chain
- [ ] `menu:portfolio` → Open Positions, Exposure, Daily Report, **Main Menu**
- [ ] `menu:signals` → Active Signals, Pipeline Status, **Submit New Signal**, Main Menu
- [ ] `menu:signals` → `menu:signal_send` → help text contains `[SIGNAL]`, `BUY`, `[NEWS]`, `[EXCHANGE_RESPONSE]`; Back button returns to Signals
- [ ] `menu:trading` → Open Positions / Exposure / Main Menu
- [ ] `menu:alerts` → Alert Status / Quality Metrics / Daily Report
- [ ] `menu:agents` → SENTR, Watchdog, Architect; each sub-menu has Open Chat + mode actions + Back + Main Menu
- [ ] `menu:ops` → Pause / Resume / Emergency Stop / Reload Menu / Validate Menu
- [ ] Every non-main menu exposes a working **Main Menu** button

### 1.3 Operations
- [ ] `cmd:menu_reload` → `*Menu reloaded*` confirmation
- [ ] `cmd:menu_validate` → `*Menu Validation*` with Source / Menus / Warnings / Errors counts

---

## 2. Command outputs (Phase 2)

Trigger each via menu button or `/command` — confirm English headers + card layout:

- [ ] `/status` → `Cycles today · Positions · Ingestion backlog · Alert rate · LLM failures · Latency p95`
- [ ] `/positions` → `*Positions*` / `Paper portfolio · read-only` / `Total: N · Mark-to-market`
- [ ] `/exposure` → `Gross · Net · Mark-to-market · Stale · Missing price`
- [ ] `/signals` → `*Signals*` / `Active · read-only` / `Count: N`
- [ ] `/signalstatus` → `*Signal Pipeline*` / `Handoff · Outbox queued · Sent · Dead-letter`
- [ ] `/alertstatus` → `Total · Digest · Last dispatch`
- [ ] `/qualitaet` → `Forward precision · Priority/hit correlation · Real-price cycles`
- [ ] `/tagesbericht` → `*Daily Report*` / `Cycles · Open incidents · Decision pack`
- [ ] `/hilfe` → `*KAI Help & Support*` with Read-only views, Actions, Message types, Navigation

---

## 3. Signal / News / Exchange Response (inbound)

Paste each envelope into chat (from `menu:signal_send` help block):

### 3.1 SIGNAL
- [ ] Paste `[SIGNAL]` block with Symbol BTC/USDT, Side BUY, Direction LONG, Entry BELOW 65000, Targets 70000, Stop Loss 62000
- [ ] Bot replies with `*Signal Received*` card (structured · audit-only) — **no order dispatched**
- [ ] Envelope written to handoff log

### 3.2 SIGNAL — fail-closed
- [ ] Paste `[SIGNAL]` with missing `Side`
- [ ] Bot rejects with `*Signal could not be normalized*` + reason

### 3.3 NEWS
- [ ] Paste `[NEWS]` block
- [ ] Bot renders `*NEWS*` card (no 📰 emoji), fields: Source / Title / Priority / Timestamp

### 3.4 EXCHANGE_RESPONSE — SUCCESS
- [ ] Paste `[EXCHANGE_RESPONSE]` with Action ORDER_CREATED, Status SUCCESS
- [ ] Bot confirms `*Executed* — \`BTC/USDT\``

### 3.5 EXCHANGE_RESPONSE — ERROR
- [ ] Paste same block with Status ERROR
- [ ] Bot confirms `*Not Executed* — \`...\``

---

## 4. Voice Confirm-Gate (P3)

- [ ] Send voice memo describing a signal ("Long BTC at 65000, target 70000, stop 62000")
- [ ] Bot replies `*Voice Signal — Draft*` + preview card + "Review before confirming."
- [ ] `/ok` → signal handed off, source tagged `voice`
- [ ] (Repeat) `/cancel` → `Voice signal draft discarded.`
- [ ] `/ok` with no pending draft → "No pending voice signal. /ok is only valid after a voice message."
- [ ] `/cancel` with no pending draft → "No pending voice signal — nothing to discard."

---

## 5. Alerts channel (outbound)

Trigger a test alert via CLI or synthetic analysis run:

- [ ] Single alert → card shows sentiment dot + `*Priority N/10 — Label*` + title + explanation + `Assets:` + `Actionable/Informational` + `[Read more]` + `Source:`
- [ ] No 💬 📌 🎯 📰 📊 emoji clutter
- [ ] Digest → `*Alert Digest — period*` / `_N alert(s)_` + line per item

---

## 6. Post-run

- [ ] `artifacts/handoff/*.jsonl` — new rows present, `event: telegram_signal_handoff`
- [ ] `artifacts/message_envelope/*.jsonl` — NEWS + SIGNAL + EXCHANGE_RESPONSE envelopes captured
- [ ] No ERROR rows in `logs/telegram_bot.log` during walk-through
- [ ] DECISION_LOG.md: append 3-line entry (date · scope · result)

---

## Fail-closed escalation

Any card shows German strings, raw JSON, `(WIP)`, debug prefixes, or missing Main Menu navigation → **stop walkthrough, file DECISION_LOG entry, roll back**.
