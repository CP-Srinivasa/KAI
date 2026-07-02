# Auditable Edge-Validation Verdict — Design

**Datum:** 2026-06-29 · **Status:** approved (Operator /goal) · **Kontext:** NORTH_STAR-Pivot (ADR 0012) — KAI = auditierbare Markt-Signal-Falsifikations-Plattform. Diese Aufgabe härtet die Truth-Infra (KEIN neuer Generator).

## Problem

Das 14-Punkte-Edge-Validierungs-Gate (`app/observability/edge_validation_gate.py::evaluate_edge_validation(trials: int, ...)`) bekommt den `trials`-Count **handeingegeben** (`trading edge-validation --trials`, derzeit `required`). `trials` treibt die Deflated-Sharpe-Ratio-Deflation (López de Prado) — den Kern der „rigorosen" Validierung. Eine handeingegebene Zahl ist menschlich fälschbar: zu niedrig gewählt ⇒ DSR künstlich aufgebläht ⇒ ein totes Signal sieht „belastbar" aus. Zudem ist das Verdikt selbst nirgends manipulationssicher festgehalten.

Gleichzeitig existiert bereits ein **Hypothesis-Ledger** (`app/research/ledger.py::HypothesisLedger`, Pfad `artifacts/research/hypothesis_ledger.jsonl`) mit `tested_count()` = Anzahl distinkter je getesteter Hypothesen-Konfigurationen — exakt der ehrliche „garden of forking paths"-N. Er wird heute NICHT ans Gate verdrahtet.

## Komponente 1 — Ehrlicher Auto-Trial-Count (Ledger → Gate)

- `trading edge-validation` liest den Trial-Count **standardmäßig** aus `HypothesisLedger(LEDGER_PATH).tested_count()`.
- `--trials` wird **optional** und zu einem **Override-Floor**: es darf den Ledger-Count nur **erhöhen** (für untracked ad-hoc/Notebook-Suchen, die der Ledger nicht kennt), nie senken. Effektiv: `trials = max(ledger_count, override or 0)`. Ein Override `< ledger_count` ⇒ clamp auf `ledger_count` + sichtbare Warnung.
- **Fail-closed:** `ledger_count == 0` UND kein Override ⇒ Abbruch (Exit ≠ 0) mit klarer Meldung. KEIN stiller Default auf 1 (das wäre die schlimmste Variante der Lücke).
- Output (Tabelle + JSON) weist aus, **welcher** Count benutzt wurde und seine **Quelle** (`ledger` / `override` / `override_above_ledger`).
- Das Gate (`evaluate_edge_validation`) bleibt unverändert (Signatur, Mathe). Geändert wird nur die CLI-Ableitung von `trials`.

## Komponente 2 — Verdikt verankern (OTS, tamper-evident)

- Jeder Lauf schreibt einen append-only **Verdikt-Record** nach `artifacts/research/falsification_verdicts.jsonl`: `{schema, recorded_at_utc, exec_audit_path, venue, n, trials_used, trials_source, ledger_count, ledger_path, net_bps_sha256, criteria[], passed}`.
- Der **kanonische SHA256 des Records** wird als Digest über die **vorhandene** Anchor-Schicht (`app/integrity/anchor.py`) verankert → `monitor/integrity/verdict-<digest16>.ots`. Der bereits laufende Upgrade-Timer (globt `*.ots` im proofs_dir, prefix-agnostisch) hebt ihn auf Bitcoin-Attestierung.
- Respektiert `IntegritySettings` (default-off): `enabled=False` ⇒ Verdikt-Record wird trotzdem geschrieben, aber NICHT verankert (`disabled`); `enabled+stamper=null` ⇒ Digest recorded; `enabled+opentimestamps` (Pi) ⇒ echter `.ots`-Proof. Anchoring darf den CLI-Lauf NIE crashen (bestehende never-raises-Garantie).
- **Ehrliche Grenze (Code + Doc + Output):** der Proof beweist **Existenz-Zeitpunkt + Unveränderlichkeit** des Verdikts, **nicht Prä-Registrierung** (dass die Hypothese vor den Daten feststand — das ist eine separate, hier NICHT gebaute Option).

## Wiederverwendung & Schnittstellen

- `ledger.HypothesisLedger.tested_count()` — vorhanden, wird nur konsumiert.
- `anchor.py`: neue, kleine **public** Funktion `anchor_record_digest(digest_hex, *, settings, prefix="verdict") -> AnchorResult`, die `_make_stamper(settings.stamper).stamp(digest_hex, out_dir, prefix=...)` kapselt (never-raises, schreibt `<prefix>-<digest16>.json`-Record). `Stamper.stamp()` bekommt einen **rückwärtskompatiblen** `prefix="audit"`-Parameter, damit Verdikt-Proofs `verdict-*.ots` heißen und der Upgrade-Timer sie im selben proofs_dir findet.
- KEINE zweite Kosten-Formel (CostModel-SSOT bleibt), KEIN zweiter Anchor, KEIN Dashboard-Panel (GTM-deferred), KEINE Pre-Registration-Erzwingung.

## Datenfluss

`hypothesis_ledger.jsonl → tested_count() → edge-validation Gate → Verdikt-Record (falsification_verdicts.jsonl) → SHA256 → anchor_record_digest → verdict-<digest>.ots → Upgrade-Timer → Bitcoin`

## Tests (TDD, vorher rot)

1. `tested_count` ist bereits getestet; neu: CLI/Gate-Wiring nutzt ihn ohne Override.
2. Override **über** Ledger-Count ⇒ Override gewinnt (`trials_source=override_above_ledger`).
3. Override **unter** Ledger-Count ⇒ clamp auf Ledger-Count + Warnung (`trials_source=ledger`).
4. Leerer/fehlender Ledger ohne Override ⇒ fail-closed (Exit ≠ 0), kein Default-1.
5. Verdikt-Record trägt die **tatsächlich** benutzten `trials_used` + `net_bps_sha256` + `criteria`.
6. `anchor_record_digest`: `enabled=False` ⇒ `disabled` (kein Schreiben in proofs_dir); `stamper=null` ⇒ `recorded` (Record-JSON geschrieben, kein `.ots`); Anchor-Fehler ⇒ `error`, nie Exception.
7. `stamp(prefix=...)` schreibt `verdict-<digest16>.ots` (Stamper gemockt im Unit); Pi-Smoke real.

## Scope / Out

Nicht in dieser Aufgabe: Pre-Registration-Gate (Forking-Paths-Beweis), Dashboard-Panel, GTM/Traffic, neue Edge-Generatoren. Genau zwei Komponenten, ein PR.
