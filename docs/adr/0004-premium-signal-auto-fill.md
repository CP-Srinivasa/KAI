# ADR 0004 — Premium-Signal Auto-Fill (Paper-Mode)

**Status:** Accepted — 2026-05-12
**Operator-Auftrag:** "Premium Telegram Signals End-to-End Execution Fix" 2026-05-12, Sektion 4
**Scope:** Paper-Mode only. Live-Mode bleibt durch Phase-0-Gates blockiert.

## Kontext

Pre-2026-05-12 verlangte jedes auto-ingested Premium-Signal aus dem Telegram-Channel
einen expliziten Operator-Klick auf den Telegram-Approval-Button bevor die Bridge
die Position fillen konnte. Die Begründung in V25 (2026-05-04) war:

- Approval-Audit-Trail enthält explizite Operator-Verantwortlichkeit
- TTL-Refused-Pfad prevents stale fills nach Outage
- Operator behält Veto-Recht bei dubiousen Signalen

**Beobachtung am 2026-05-11/12:** Von drei vom Operator dokumentierten
Premium-Signalen (TRUTH 18:23, OPG 22:44, IRYS 02:05) wurde nur eines (OPG)
innerhalb von 50 Sekunden approved. TRUTH wurde nach 8 Minuten approved (zu
spät — Risk-Limit war voll). IRYS wurde nicht angefasst und nach 4h TTL-expired.

Operator-Auftrag 2026-05-12 Sektion 4: *"Auch wenn keine manuelle Bestätigung
erfolgt, muss das Signal mindestens im Paper Trading verarbeitet werden."*

## Entscheidung

Neues Setting `operator_signal_premium_auto_fill_enabled` (Default `False`,
fail-closed). Wenn aktiviert, schreibt der `telegram_channel_worker` nach jedem
accepted Envelope SOFORT einen auto-approved Envelope:

```python
handle_signal_approval(
    action="fill",
    envelope_id=env_id,
    envelope_log=envelope_log_path,
    ttl_minutes=approval_ttl_min,
    approved_by="auto-fill",
)
```

Damit greift der bereits etablierte Approval→Bridge-Pfad ohne dass der Operator
klicken muss.

## Konsequenzen

### Was bleibt unverändert (Schutz)

- **Bridge-Risk-Gates** greifen unverändert: kill_switch, max_open_positions,
  daily_loss, sizing, position-already-exists. Ein vollgelaufenes
  max_open_positions blockt Auto-Fill genauso wie manuelles Fill.
- **Fail-Closed-Pfad** bleibt: unparsbare Signale landen nicht als Envelope,
  also auch nicht als Auto-Fill.
- **Approval-Audit-Trail** wird weiter geschrieben:
  - `approved_by="auto-fill"` ist im JSONL sichtbar — Operator kann
    nachvollziehen welche Fills Auto vs. Manual waren.
  - `idempotency_key` und double-click-dedup verhindern double-emit.
- **Operator-Override** bleibt erhalten: Telegram-Buttons werden weiter
  gesendet. Operator kann manuell Ignore klicken — der Auto-Fill-Audit-Record
  bleibt im JSONL, aber die Bridge sieht keine zweite Position (idempotency).
- **Live-Mode** blockt sich durch eigene Phase-0-Gates (HOTP, server-side-SL,
  exchange-perms). Auto-Fill bleibt explizit eine Paper-Lockerung.

### Was sich ändert (Lockerung)

- **Operator-Verantwortlichkeit** wird teilweise an die Bridge-Gates delegiert.
  Operator-Auftrag akzeptiert das explizit.
- **Burst-Risk** bei Channel-Spam: 10 Signale in 10 Minuten = 10 Auto-Fill-
  Versuche. max_open_positions begrenzt die tatsächlichen Fills, aber jeder
  Auto-Fill-Versuch erzeugt einen audit-record (kein Schaden, mehr Log-Volume).
- **Approval-Audit-Stream** enthält mehr Records pro Signal (auto-fill +
  evtl. operator-click). Downstream-Konsumenten müssen `approved_by` filtern.

### Wie deaktivieren

Sofort revertable: `EXECUTION_OPERATOR_SIGNAL_PREMIUM_AUTO_FILL_ENABLED=false`
in .env → Worker fällt zurück auf manuelle-Klick-Logik. Kein Code-Revert nötig.

## Alternativen verworfen

**A: Allowlist erweitern um `telegram_premium_channel` (ohne `_approved`).**
Würde Bridge erlauben, direkt shadow-envelopes ohne approval-Re-emit zu fillen.
Verworfen weil Audit-Trail-Spur "approved_by" verloren geht — kein Unterschied
zwischen "Operator hat zugestimmt" und "Auto-Fill" im JSONL.

**B: Neuer source-Suffix `_auto`.** Würde drei Quellen unterscheiden
(approved/auto/manual). Verworfen weil orthogonal zu Approval-Mode und
zusätzliche Komplexität ohne Gegenwert — `approved_by="auto-fill"` ist
ausdrucksstark genug.

**C: Auto-Fill via Bridge-internal-Flag statt Worker-Hook.** Würde die Bridge
auto-approve Premium-Source-Tags ohne envelope-re-emit. Verworfen weil
inkonsistent mit der existierenden Approval-Pipeline und schwerer testbar.

## Aktivierungsschritte (Operator-Decision)

```bash
# Auf Pi 5 (oder lokale .env):
echo 'EXECUTION_OPERATOR_SIGNAL_PREMIUM_AUTO_FILL_ENABLED=true' >> .env
sudo systemctl restart kai-tg-listener
# Smoke: nächstes Premium-Signal sollte ohne Klick in bridge_pending_orders.jsonl
# als stage=filled erscheinen (sofern max_open_positions nicht voll).
```

## Cross-Refs

- Operator-Auftrag 2026-05-12 (Premium-Signal-Pipeline End-to-End)
- ADR 0001 (kai_light_live_phase0_spec — Live-Mode-Gates)
- ADR 0002 (cf-access-defense)
- ADR 0003 (DuckDB-Storage-Pivot)
- `app/ingestion/telegram_channel_approval.py` — `handle_signal_approval()`
- `app/ingestion/telegram_channel_worker.py` — `_auto_fill_envelope()`
- `app/core/settings.py:ExecutionSettings.operator_signal_premium_auto_fill_enabled`
