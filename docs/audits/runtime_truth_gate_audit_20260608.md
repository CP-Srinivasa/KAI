# KAI Runtime / Truth-Layer / Gate Audit — 2026-06-08

Sprint: `KAI-RUNTIME-TRUTH-GATE-AUDIT` · reine Delta-Analyse gegen p7-Tip `d191aa04` · **keine Codeänderung außer diesem Dokument**. Jede Zeile ist gegen echten Code belegt. Klassifikation: **[FAKT]** = im Code/Artefakt verifiziert · **[HYPOTHESE]** = plausibel, aber nur gegen Pi-Runtime verifizierbar (Production-Read-Guard, diese Session nicht gelesen) · **[RISIKO]** · **[EMPFEHLUNG]**.

> **Wichtigster Kontext-Befund:** Die Truth-Layer-/Dashboard-Schicht ist **größtenteils bereits ehrlich gebaut** (D-191 / PR #147/#157). Die meisten vom Operator zitierten Anzeigen (`0 trusted`, `Re-Entry abgelaufen`, `Paper 1226 hist · 0/24h`, `Priority UNDERPERFORMING`, `Regime read-only`) sind **korrekte, absichtliche Labels**, keine Bugs. Der einzige potenziell sicherheitsrelevante Punkt (`Premium Runtime aktiv` trotz `entry_mode=disabled`) ist **kein Code-Bug**, sondern ein **verifikations-gated State/Posture-Verdacht**: er kann laut Code nur auftreten, wenn die Premium-Fastlane auf der Pi wieder eingeschaltet ist (Widerspruch zu D-231).

> **VERIFIKATION 2026-06-08 (read-only Pi, Operator-freigegeben) — Befund A AUFGELÖST:** Die Pi-`.env`-Flags sind **korrekt OFF**: `EXECUTION_ENTRY_MODE=disabled`, `PREMIUM_FASTLANE_ENABLED=false`, `PREMIUM_PAPER_EXECUTION_ENABLED=false`, `EXECUTION_OPERATOR_SIGNAL_BRIDGE_ENABLED=true`. Damit ist laut deterministischer Code-Logik (`premium_signals.py:438-469`: `entry_blocks=True` ⇒ `classic_can_open=False`; `fl.enabled=false` ⇒ `fastlane_overrides=False`) `can_open_paper_positions` **zwingend `False`** — der Banner rendert „Premium Execution blockiert". **Kein Safety-Incident, keine Fastlane-Reaktivierung, kein Live-API-Bug.** Der Runtime-Endpoint ist auth-geschützt (HTTP 401 ohne Token; Token bewusst nicht gelesen). **Konsequenz:** Befund A wird von „P1/P0-IF-confirmed" auf **P3 (stale Anzeige)** herabgestuft; eine etwaige „Premium Runtime aktiv"-Sichtung war eine veraltete UI-/Screenshot-Beobachtung von **vor** dem Flag-OFF (06-05 ~17:58). Der **echte** verbleibende Punkt ist rein UI-Klarheit (Befund B, 3-State-Banner).

---

## 1. Executive Summary (max. 10 Punkte)

1. **[FAKT]** Unter `entry_mode=disabled` blockiert der Bridge Premium-Paper-Entries hart (`envelope_to_paper_bridge.py:1316` `classic_entry_blocks`); der einzige Override ist die Fastlane (`premium_fastlane.py:447`). Live-Schutz ist davon **getrennt** und zusätzlich gegated.
2. **[FAKT, verifiziert 06-08]** Die Banner-Aussage „Premium Runtime aktiv" (`PremiumRuntimeBanner.tsx:270`) kann unter `entry_mode=disabled` **nur** bei `fastlane_overrides=True` erscheinen (`premium_signals.py:469`). Pi-`.env` **verifiziert OFF** (`PREMIUM_FASTLANE_ENABLED=false`, `PREMIUM_PAPER_EXECUTION_ENABLED=false`, `EXECUTION_ENTRY_MODE=disabled`) ⇒ `can_open_paper_positions=False` zwingend ⇒ Banner = „blockiert". **Kein aktiver Verstoß**; etwaige „aktiv"-Sichtung war stale.
3. **[FAKT]** Code ist hier **korrekt** (truthful, kein Fail-Open). Verbleibendes Real-Problem ist nur **UI-Klarheit**: der Banner unterscheidet nicht sauber `blocked_by_entry_mode` / `active_via_fastlane_override` / `inactive_off` (Befund B, P2).
4. **[FAKT]** Re-Entry-Status „expired" ist **fail-safe** berechnet (`dashboard.py:179`); `_reentry_status` liefert `expired`/`requires_re_evaluation`/`active`, **kein Freigabe-Pfad**. „2026-05-16" ist ein konfigurierbares Datum (`ALERT_REENTRY_TARGET_DATE`, sonst `_REENTRY_TARGET_DATE` Default). Keine Datums-Logik im `re_entry_mode.py`-Capability-Gate.
5. **[FAKT]** „0 trusted sources" ist korrekt + absichtlich: Wilson-Lower-Bound, „100% bei n=1 darf nie ‚trusted' lesen" (`dashboard.py:299`), `quality_status=critical`. Wird als **Quality-Constraint** gelabelt, nicht als Block.
6. **[RISIKO/FAKT]** Source-Reliability-Priority-Modifier ist **fail-open**: fehlende/leere/korrupte `source_reliability.json` ⇒ **kein** Modifier (`eligibility.py:182` „no modifier is safer than a wrong modifier"). Durch `entry_mode=disabled` aktuell entschärft, aber latentes Risiko bei Re-Enable.
7. **[FAKT]** Priority „UNDERPERFORMING" ist korrekt gelabelt: `priority_tier_lift_pct < 0` ⇒ `critical` + Warnung „do not present it as a validated quality label" (`dashboard.py:697-706`). `is_decision_relevant=True` aber read-only.
8. **[FAKT]** Regime „read-only" ist im Code als reines Diagnose-Feld belegt: `is_decision_relevant=False`, `is_read_only=True`, Warnung „does not gate trades or risk yet" (`dashboard.py:709-719`). **Read-only beeinflusst keine Execution** — bestätigt.
9. **[FAKT/RISIKO]** Timer-Health meldet `inactive`/`stale` (`timer_health.py:30-31,111-123`), aber **nur passiv über `GET /health/timers`** — **kein aktiver Telegram-Push** bei `has_inactive`/`stale` (kein Alert-Wiring in `telegram_bot.py`). `kai-risk-gate-audit-review.timer` existiert (`deploy/systemd/`), „inactive" = nicht enabled/gestartet auf Pi.
10. **[FAKT]** Truth-Layer v2 hat bereits `calculation_version`/`stale_status`/`reconciliation_status` (`dashboard.py:_metric_contract`, MetricRegistry-Reconciliation `dashboard.py:758-769`, Drift→Warning, „never a hard fail").

---

## 2. Befund-Tabelle

| # | Problem/Bug | Evidenz im Code/Log | Ursache | Risiko | Schweregrad | Fix | Prävention | Modul/Agent |
|---|---|---|---|---|---|---|---|---|
| A | „Premium Runtime aktiv" trotz `entry_mode=disabled` möglich | `premium_signals.py:457-469`; `premium_fastlane.py:447`; **Pi-`.env` 06-08: Fastlane+Paper=false, entry_mode=disabled** | Verdacht widerlegt — Flags OFF, `can_open_paper_positions=False` zwingend; etwaige „aktiv"-Sichtung war stale | Keiner (kein aktiver Bypass) | ~~P1~~ → **P3 (resolved/stale)** | Keine Runtime-Aktion; nur UI-Klarheit (→ Befund B). Invariant-Test trotzdem als Regressionsschutz | Invariant-Test: `entry_mode=disabled` ∧ Fastlane-OFF ⇒ `can_open_paper_positions=False` | SENTR (verifiziert) |
| B | Fastlane-Override-State sieht aus wie normaler „aktiv"-Zustand | `PremiumRuntimeBanner.tsx:257-278` ruhiger Cyan-Strip „aktiv", Bypass nur in `RuntimeFlags` | Headline unterscheidet nicht „entry_mode erlaubt" vs „Fastlane bypassed disabled" | Operator-Fehlinterpretation eines Bypass als Normalbetrieb | **P2** | Eigene laute Banner-Variante „Fastlane-Bypass aktiv trotz entry_mode=disabled" | Snapshot-Test je Runtime-Verdict-Variante | DALI |
| C | Source-Reliability-Modifier fail-open bei fehlender/korrupter Datei | `eligibility.py:177-182` „fail-open — no modifier is safer"; `:66` „Datei nicht vorhanden/leer → kein Effekt" | Bewusste fail-open-Wahl; kein Integritäts-/Freshness-Check der `source_reliability.json` | Bei Re-Enable: Signale ohne Source-Penalty durchs Gate (stille Degradation) | **P2** (durch disabled entschärft) | Freshness-/Schema-Guard: Datei älter als N Tage oder Schema-Mismatch ⇒ konservativer Default + Warnung statt no-op | Test: stale/korrupte Datei ⇒ degraded-Flag, nicht silent-pass | data-quality-inspector + Watchdog |
| D | Timer-Fehler nur passiv, kein aktiver Alert | `timer_health.py:111-156` (`has_inactive`/`stale`); `health.py:42` nur GET-Endpoint; kein Push in `telegram_bot.py` | Health-Surface ist pull-only; kein Watchdog-Push auf `has_inactive`/`stale` | `kai-risk-gate-audit-review.timer inactive` bleibt unbemerkt bis jemand das Dashboard öffnet | **P2** | Watchdog-Timer/Hook: bei `state∈{has_inactive,stale}` Telegram-Warnung (1×/Tag, dedupe) | Self-Monitoring: Timer-Health-Audit-Staleness selbst alarmieren | Watchdog |
| E | `kai-risk-gate-audit-review.timer` inaktiv = Risk-Gate-Audit-Review läuft nicht | `deploy/systemd/kai-risk-gate-audit-review.{service,timer}` vorhanden; Status „inactive" (Pi) | Timer nicht `enable --now` nach Deploy ODER nach Reboot nicht gestartet | Risk-Gate-Audit-Review-Artefakt veraltet → Audit-Lücke (read-only, kein Execution-Effekt) | **P2** | Pi: `systemctl enable --now kai-risk-gate-audit-review.timer` (separater Deploy-Auftrag) + Reaktivierungs-Check in `pi_install_systemd.sh` | Post-Deploy-Smoke: alle erwarteten Timer `enabled`+`active` | Operator + Watchdog |
| F | Timer-Health kippt komplett auf `stale` wenn Audit-Schreiber verzögert | `timer_health.py:30,103-105` (`checked_at` >2h ⇒ `stale`) | Single-Writer-Audit; verzögert dieser, ist die ganze Health-Sicht blind | „verzögert >2h"-Zustand maskiert echte Einzel-Timer-Stati | **P3** | Pro-Timer-Last-Run statt nur Audit-`checked_at`; getrennte Staleness je Unit | Test: ein verzögerter Writer darf gesunde Timer nicht als unbekannt zeigen | Watchdog |
| G | Re-Entry-Gate hat kein aktuelles Freigabe-Modell (nur „expired"-Evidenz) | `dashboard.py:200-211` `status=expired`; `re_entry_mode.py` = Capability-Switches ohne Datums-Bezug | Historisches TV-Pivot-Ziel (2026-05-16) abgelaufen, keine neue Gate-Definition | „expired" wird evtl. als „erledigt/frei" missverstanden | **P2** | Neue Gate-Definition: `expired` ⇒ explizit `no_current_authorization` + Verweis auf echten Execution-Gate (`entry_mode`); Re-Entry-Fortschritt als Evidenz, nicht Freigabe | Test: `expired` darf in keinem Pfad zu „can_execute" mappen | Architect |
| H | `unknown`/legacy-Source-Bucket im 90d-Fenster vermischt mit aktiver Evidenz | `eligibility.py:43-55` (`unknown` 17.5% precision, legacy pre-attribution); `source_reliability.py:56` `_DEFAULT_WINDOW_DAYS=90` | Pre-Attribution-Records (vor Source-Wiring) liegen im selben Rolling-Fenster | Verzerrte Source-Health-Sicht; „critical" evtl. teils Legacy-Artefakt | **P3** | Recalc: Pre-Attribution/`unknown` aus aktivem Trusted-Denominator ausschließen, separat ausweisen | Schema: `bucket=active|legacy` Pflichtfeld im Recalc-Output | data-quality-inspector |

---

## 3. Falsche oder missverständliche Dashboard-Darstellungen

- **Befund B (P2):** „Premium Runtime aktiv" (ruhiger Cyan-Strip) macht einen **Fastlane-Bypass** optisch ununterscheidbar von Normalbetrieb. Korrekt wäre: Bypass-Zustand laut + eigenständig kennzeichnen.
- **[FAKT, kein Bug] „Entry disabled" + „Premium Runtime aktiv" gleichzeitig:** Zwei verschiedene Widgets berechnen „Wahrheit" aus verschiedenen Feldern desselben `/runtime`-Payloads (`entry_mode_blocks_premium_paper` vs `can_open_paper_positions`). Beide sind je für sich korrekt, aber die **Koexistenz** ist erklärungsbedürftig → konsolidierte Headline nötig (Befund A/B).
- **[FAKT, kein Bug]** „Paper: 1226 hist · 0/24h", „Re-Entry: abgelaufen", „Priority: UNDERPERFORMING", „Regime: read-only", „Sources: 0 trusted" sind **korrekte Truth-Layer-Labels** mit `quality_status`/`scope`/`stale_status`. **Keine Aktion** außer ggf. Microcopy.

## 4. Echte Backend-/Runtime-Probleme

- **C (P2):** Fail-open Source-Modifier ohne Freshness/Schema-Guard.
- **D (P2):** Kein aktiver Alert auf Timer-Fehler (pull-only Health).
- **E (P2):** `kai-risk-gate-audit-review.timer` inaktiv ⇒ Audit-Review-Lücke.
- **F (P3):** Timer-Health-Single-Writer-Staleness maskiert Einzel-Stati.
- **[HYPOTHESE, A]:** Fastlane evtl. auf Pi aktiv (P1, Pi-Verifikation).

## 5. Blockierte / unbewiesene Zustände

- **Execution:** `entry_mode=disabled` = globaler Stop **[FAKT]**. Premium-Paper nur via Fastlane-Override offen.
- **Re-Entry:** `expired` = **keine** aktuelle Freigabe **[FAKT]**; neue Gate-Definition offen (G).
- **Source-Trust:** 0 trusted, Evidenz **unbewiesen/critical** **[FAKT]**; legacy/active-Trennung unscharf (H).
- **Priority/Signal-Q:** UNDERPERFORMING / Low-P insufficient = **unbewiesen**, read-only, decision-relevant-aber-nicht-validiert **[FAKT]**.
- **Regime:** read-only, **gated keine Trades** **[FAKT]**.

## 6. Notwendige Tests

1. Invariant: `entry_mode=disabled` ∧ `premium_fastlane.enabled=False` ⇒ `runtime.can_open_paper_positions=False` ∧ Banner=„blockiert" (Befund A).
2. Invariant: `entry_mode=disabled` ∧ Fastlane-Override ⇒ Banner zeigt **Bypass**-Variante, nicht „aktiv" (Befund B).
3. Eligibility: fehlende/stale/korrupte `source_reliability.json` ⇒ `degraded`-Flag + Warnung, **kein** silent no-op (Befund C).
4. Timer: `state∈{has_inactive,stale}` ⇒ Alert-Emission (dedupe) (Befund D).
5. Re-Entry: `status=expired|requires_re_evaluation` mappt in **keinem** Pfad auf „can_execute"/Freigabe (Befund G).
6. Source-Recalc: `unknown`/legacy nicht im aktiven Trusted-Denominator (Befund H).
7. Truth-Layer-Reconciliation: `within_tolerance=False` ⇒ Warning geloggt + `reconciliation_status` im Payload (Regressionsschutz `dashboard.py:758-769`).

## 7. Präventive Maßnahmen

- **Fail-closed Default für Safety-nahe Anzeigen:** „aktiv/grün" nur bei positiv bewiesenem, nicht-bypassten Zustand; Bypässe immer laut.
- **Push statt Pull für Health:** Timer-/Source-/Truth-Staleness als aktive Warnungen (Watchdog), nicht nur Endpoint.
- **Integritäts-Guards für Artefakt-Inputs:** Freshness + Schema-Version + Reconciliation für jedes decision-relevante Artefakt (`source_reliability.json`, Timer-Audit, Regime-Snapshot).
- **State vs Posture trennen:** „truthful aktiv" (Code) ≠ „policy-konform" (D-231). Banner/Trail sollten Policy-Verstöße (Bypass trotz disabled) explizit markieren.
- **Date-anchored Gates ablösen:** Re-Entry nicht an ein fixes Datum, sondern an den echten Execution-Gate (`entry_mode`) + Capability-Invarianten (`re_entry_mode.py`) binden.

---

## 7a. Spec — PremiumRuntimeBanner 3-State-Wahrheit (Befund B, für FS-1)

**Problem:** `PremiumRuntimeBanner.tsx` kennt heute nur 2 visuelle Zustände (ruhig „aktiv" vs laut „blockiert") plus Fastlane-Sonderfälle. Ein Fastlane-**Override** (Paper offen *trotz* `entry_mode=disabled`) sieht aus wie Normalbetrieb.

**Ziel-Zustände (drei, exklusiv, aus `/runtime`-Payload ableitbar, KEINE neuen Backend-Felder nötig):**

| State | Bedingung (vorhandene Felder) | Visuell | Headline |
|---|---|---|---|
| `inactive_off` | `!can_open_paper_positions` ∧ `!premium_fastlane_enabled` | neutral/grau | „Premium Paper: AUS (entry_mode=`{entry_mode}`)" + `blocking_reasons` |
| `blocked_by_entry_mode` | `!can_open_paper_positions` ∧ `entry_mode_blocks_premium_paper` | rot, role=alert | „Premium Execution blockiert — entry_mode=`{entry_mode}`" (heutiger Block-Banner) |
| `active_via_fastlane_override` | `can_open_paper_positions` ∧ `entry_mode_bypassed_for_fastlane_paper` | **laut amber/Bypass-Stil, nicht ruhig-cyan** | „⚠ Fastlane-Bypass aktiv — Paper offen TROTZ entry_mode=`{entry_mode}` (Live geschützt)" |
| (Normalfall) `active_clean` | `can_open_paper_positions` ∧ `!entry_mode_bypassed_for_fastlane_paper` | ruhig cyan (heutiger „aktiv") | „Premium Runtime aktiv" |

**Regel:** „aktiv" (ruhig) nur bei `active_clean`. Jeder Bypass-Zustand ist optisch von Normalbetrieb getrennt und nennt explizit den umgangenen Kill-Switch. Reiner Frontend-Fix (Felder existieren: `can_open_paper_positions`, `entry_mode_blocks_premium_paper`, `entry_mode_bypassed_for_fastlane_paper`, `premium_fastlane_enabled`, `entry_mode`). Snapshot-Test je State.

## 8. Follow-up Fix-Sprints (kein Code in diesem Sprint)

- **FS-1 (P2, DALI+Neo) — Runtime-Safety-Teil ERLEDIGT 06-08:** Pi-Flags verifiziert OFF (kein Bypass, kein Incident → Befund A auf P3 herabgestuft). Verbleibt rein UI: PremiumRuntimeBanner 3-State-Wahrheit (§7a) + Invariant-Test (`entry_mode=disabled` ∧ Fastlane-OFF ⇒ `can_open_paper_positions=False`) als Regressionsschutz. **Kein P0/P1 mehr.**
- **FS-2 (P2, Watchdog):** Active Health-Alerting — Timer `has_inactive`/`stale` + `source_reliability.json`-Staleness → Telegram-Push (D, F) + `kai-risk-gate-audit-review.timer` reaktivieren (E, separater Deploy-Auftrag).
- **FS-3 (P2, data-quality-inspector):** Source-Reliability-Härtung — Freshness/Schema-Guard gegen fail-open (C) + active/legacy-Trennung im Recalc (H).
- **FS-4 (P2, Architect):** Re-Entry-Gate-Redefinition — `expired` ⇒ `no_current_authorization`, an `entry_mode` gebunden (G).

**Schweregrad-Disziplin:** Kein Zielbild-Feature als P0. Der zunächst als Live-Safety-Verdacht eingestufte Punkt (A) ist **06-08 read-only verifiziert: Fastlane+Paper OFF, entry_mode=disabled ⇒ kein Bypass, kein Incident** → A auf P3 (stale Anzeige) herabgestuft. Es verbleibt **kein P0/P1**; höchste offene Items sind P2 (UI-3-State, Source-fail-closed, Timer-Taxonomie+Alerting, Re-Entry-Redefinition). Alles ist durch `entry_mode=disabled` execution-seitig entschärft.
