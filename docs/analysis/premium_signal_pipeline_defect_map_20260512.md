# Premium-Signal-Pipeline — Defektkarte 2026-05-12

**Phase 1 Recon-Outcome.** Vor Sprint-Start, ohne Code-Edit.

## 1. Architektur, die TATSÄCHLICH existiert

```
Telegram NewMessage
  → telegram_channel_worker._handler (worker.py:751)
  → process_message (worker.py:359)
  → parse_premium_channel_message (telegram_channel_parser.py:327)
  → ParsedSignal (telegram_channel_parser.py:60)
  → emit_parsed_signal (telegram_channel_envelope.py:142)
  → artifacts/telegram_message_envelope.jsonl
       source="telegram_premium_channel"
       execution_enabled=False
       write_back_allowed=False
  → _send_approval_for_envelope (worker.py:675)
  → Operator-Telegram-Button [✅Fill][❌Ignore]
  → handle_signal_approval (telegram_channel_approval.py:476)
  → re-emit mit source="telegram_premium_channel_approved"
  → run_tick (envelope_to_paper_bridge.py:525)
  → 6 Gates: allowlist → TTL → completeness → position-exists → market-data/entry-band → risk → sizing
  → PaperExecutionEngine.execute_intent (paper_engine.py:115)
  → PaperPosition in PaperPortfolio.positions
  → /operator/portfolio-snapshot
  → Portfolio.tsx
```

## 2. Was bereits sehr gut gebaut ist (überraschend!)

- **Parser** (`telegram_channel_parser.py`): 4 Symbol/Direction-Patterns, 4 Entry-Formen (range/above/at/inline), Targets-Forms A/B/C (single-line dash / emoji-inline / emoji-label), Leverage, Margin, Exchange-Scope. Unicode-Dashes. Pure, testbar.
- **Normalized-Schema** (`normalized_signal.py`): 16-State-Lifecycle-Enum, Transition-Matrix, Validator mit geometrischer Plausibility (LONG: SL<entry<targets), Sizing-Pflicht, immutable.
- **Bridge** (`envelope_to_paper_bridge.py`): 6 Gates, Scale-Detection für Bybit-Integer-Ticks (1000LUNC/SWARMS), Position-already-exists-Schutz, Staged-Exit-Tiers V25-C (4 Targets → 25/25/25/25 split), Idempotency via `opbridge:{envelope_id}`.
- **Approval-Flow**: TTL-safe, double-click-dedup, source-suffix-Pattern, separate audit-log, send-audit-trail.
- **Worker**: Heartbeat-Reactivity-Counter (F3), Checkpoint mit marked chat_id (F6), Gap-Replay nach Outage (V25), FloodWait-Härtung (F1), strukturierte Exception-Handling (F2), Replay-Approval-Symmetry (V25).
- **PaperPosition**: keine 0.01-Defaults — alle Felder explizit oder None.

## 3. Bruchstellen — wahre Wurzelursachen

### P0-A: `operator_signal_bridge_enabled = False` default
- **File:** `app/core/settings.py:217`
- **Effekt:** `run_tick()` returnt sofort. Bridge ist tot. KEIN Signal wird je als Paper-Position gefüllt.

### P0-B: `operator_signal_source_allowlist = "dashboard"` default
- **File:** `app/core/settings.py:218`
- **Effekt:** Selbst wenn Bridge aktiviert, source `telegram_premium_channel` oder `_approved` ist NICHT in Allowlist → `skipped_source` audit-record, KEIN Fill.

### P0-C: `operator_signal_approval_enabled = False` default
- **File:** `app/core/settings.py:227`
- **Effekt:** Bei deaktiviertem Approval bleibt source `telegram_premium_channel` (ohne `_approved`-Suffix). Bridge-Allowlist hätte selbst nach P0-B-Fix nur die `_approved`-Variante drin.
- **Operator-Wille (Auftrag Sektion 4):** "Auch wenn keine manuelle Bestätigung erfolgt, muss das Signal mindestens im Paper Trading verarbeitet werden." → Allowlist muss BEIDE Varianten erlauben ODER es braucht einen Auto-Fill-Pfad.

### P1-D: Operator-Beobachtung "0.01-Positionen" nicht im Code reproduzierbar
- Grep `0\.01` in `app/` findet nur **`fees.py:36` Kommentar** ("1bps = 0.01%"). KEINE 0.01-Defaults.
- Grep `0\.01` in `web/` findet NICHTS.
- **PaperPosition** in `models.py:75` hat keine 0.01-Defaults — alle Felder explizit/None.
- **Portfolio.tsx** rendert `fmt$(null) = "—"`, nicht "0.01".
- **Hypothesen** (zu verifizieren):
  - (H1) Operator sieht alten Frontend-Cache mit alter Logik (siehe `session_2026_05_10_evening_dist_path_drift.md` — Dual-Origin-Drift 2026-05-11 hat das schon mal verursacht)
  - (H2) Es gibt eine alte `paper_execution_audit.jsonl`-Datei mit 0.01-Werten aus früheren fehlerhaften Fills, die `rehydrate_from_audit()` heute zurückspielt
  - (H3) Die "Positionen" die Operator sieht sind NICHT PortfolioPage sondern eine andere View (vielleicht Pending-Signals-Liste oder Bridge-Pending-Records)
  - (H4) Eine andere Source (signal_parser.py /trade command, TradingView, dashboard-paste) hat in der Vergangenheit Test-Positionen mit 0.01-Min-Quantity gefüllt
- **MUSS VERIFIZIERT WERDEN** durch: Pi-Inspection `cat artifacts/paper_execution_audit.jsonl | jq` + Screenshot des Operator-Views

### P2-E: Kein Auto-Fill-Pfad für Premium-Signale
- Heute: jedes Premium-Signal braucht zwingend Operator-Klick auf Telegram-Approval-Button
- Operator-Auftrag Sektion 4 verlangt: Auto-Fill als Fallback + manuelle Trigger-Buttons im Dashboard

### P2-F: Target-Completion-Meldungen werden nicht geparsed/reconciled
- Parser kennt nur "neue Signale"-Format
- `🎯 #ON/USDT has touched 19561 and has completed all the profit targets` wird vom `parse_premium_channel_message` mit `None` returned (kein symbol+direction+entry+stop_loss alle vorhanden)
- KEIN orphan_target_completion-Pfad existiert

### P3-G: Dashboard-Funktionstasten fehlen
- "Signal manuell auslösen / Paper Trade erzwingen / Signal erneut verarbeiten / Position reparieren / Reconcile Target Completion" — keine dieser Buttons existiert in Portfolio.tsx
- Backend-Endpunkte für diese Aktionen fehlen ebenfalls

## 4. Korrigierter Sprint-Plan (4 Sprints, nicht 8)

| Sprint | Inhalt | Aufwand |
|---|---|---|
| **0 (Verify)** | Pi-State-Check: laufende .env-Konfig, `paper_execution_audit.jsonl` content, ob 0.01-Records existieren, ob Approval-Mode an ist, ob telegram_channel_worker live ist und Heartbeat schreibt | ~30min, ssh-only |
| **A (Activate)** | Pipeline aktivieren: settings.py defaults ändern (bridge_enabled=True, allowlist um beide telegram_premium_channel-Varianten erweitern, approval_enabled-Decision treffen). Wenn Auto-Fill-Wille: telegram_premium_channel **direkt** in allowlist statt nur _approved-Suffix. Optional: Channel-Auto-Mode-Flag | ~1h |
| **B (Reconcile)** | Target-Completion-Parser + Reconciler. Neue Regex `_TARGET_COMPLETION = re.compile(r"#(?P<symbol>\S+).*has touched (?P<price>[\d.]+).*completed all the profit targets")`. Mapping zu offener Position via Symbol-Match. `orphan_target_completion` audit-event. | ~2h |
| **C (UI)** | 5 Dashboard-Buttons + 5 Backend-Endpunkte (idempotent). Buttons: Signal-trigger / Paper-Force / Signal-Reprocess / Position-Repair / Target-Reconcile. Backend in dashboard.py + bridge-helpers. | ~3h |
| **D (Tests + Smoke)** | Parser-Tests für TRUTH/OPG/IRYS + Target-Completion + Bridge-Integration. Smoke der 3 Beispielsignale auf Pi paper-mode. | ~2h |

**Realistischer Umfang: ~8.5h, ein vollständiger Arbeitstag — NICHT 5 Sprints.**

## 5. Was im Operator-Auftrag NICHT gebaut werden muss

Diese existiert bereits und ist robust:
- Sektion 5 (Repo-Module-Inventur) — alles da, voll funktional
- Sektion 6 (Parser-Regeln) — Parser bereits 95% der Varianten abdeckend; nur TARGET_COMPLETION fehlt
- Sektion 7 (Preisnormalisierung) — Scale-Detection in Bridge V25-D bereits implementiert (1000LUNC, SWARMS)
- Sektion 8 (Paper-Trading-Execution) — vollständig in paper_engine + bridge implementiert
- Sektion 13 (Audit-Events) — die meisten bereits da (`telegram_channel_envelope`, `telegram_channel_approval`, `paper_execution_audit`, `operator_signal_bridge`, `lifecycle_transition`)
- Sektion 14 (Fail-Closed) — bereits implementiert via Validator + Gates + Refused-States

Was wirklich neu gebaut werden muss:
- Auto-Fill-Pfad (Settings + Allowlist + evtl. neue source-tag-Variante)
- Target-Completion-Reconciler
- 5 Dashboard-Buttons + Endpunkte
- Tests für die 3 Operator-Beispielsignale
- Verifikation woher "0.01-Positionen" tatsächlich kommen (P1-D)

## 6. Risiken

- **Auto-Fill ohne Approval ist eine Sicherheits-Lockerung.** Pre-V25 war die Logik bewusst "Operator klickt zuerst". Operator-Auftrag verlangt jetzt explizit Auto-Fill. Entscheidung muss dokumentiert sein (ADR).
- **0.01-Quelle unklar.** Wenn ich die nicht finde und fixe, sieht Operator nach allem Wahrscheinlichkeit dieselben 0.01-Werte (alter Cache / alte audit-records).
- **Test-Coverage:** Existierende Tests müssen erweitert werden, nicht parallel daneben aufgebaut.
- **PR-Stacking:** Stackt auf PR #5 (mergebar). Falls #5 vor diesem PR merged wird, einfach Rebase auf p7.

---

**Recommendation:** Vor Sprint A erst Sprint 0 (Pi-Verify) machen — 30 Minuten investiert ersparen 4h Fixerei am falschen Problem.
