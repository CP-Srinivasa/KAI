# Sprint 13 Contract — Evaluation Comparison und Regression Guard

> **Canonical reference** für Sprint-13 Pre/Post-Evaluation-Vergleich, Regression Guard,
> persistierbares `EvaluationComparisonReport`-Artefakt und Promotion-Continuity.
>
> Upstream contracts: `docs/contracts.md §24`, invariants I-70–I-74.
> Upstream Sprint-12: `docs/sprint12_training_job_contract.md`, I-63–I-69.
> Upstream Sprint-7/8/9: `docs/benchmark_promotion_contract.md`, `docs/tuning_promotion_contract.md`.

---

## Sprint 13C — Architektur-Entscheidung (2026-03-19)

**Kanonischer Ort für Comparison-Logik: `app/research/evaluation.py`**

Beim Sprint-13-Abschluss-Review wurde festgestellt:

1. `evaluation.py` enthält bereits produktiv: `EvaluationComparisonReport`, `compare_evaluation_reports()`,
   `save_evaluation_comparison_report()`, `RegressionSummary`, `CountComparison`, `EvaluationMetricDeltas`,
   `PromotionGateChanges`.
2. Alle Tests (CLI + upgrade_cycle) importieren ausschließlich aus `evaluation.py`.
3. Ein separates `comparison.py`-Modul würde Parallelarchitektur und Namenskollisionen erzeugen.

**Entscheidung**: `comparison.py` als separates Modul wird **nicht erstellt**.
`evaluation.py` ist und bleibt der kanonische Ort für alle Comparison-Typen und -Funktionen.

**Hard-Regression-Thresholds (R1–R6) und `RegressionGuard`**:
Diese werden für Sprint 13C **nicht** hinzugefügt. Die bestehende `RegressionSummary` mit
`has_regression` und `regressed_metrics` bietet ausreichende Regression-Sichtbarkeit.
Explizite Per-Metrik-Schwellen können in einem späteren Sprint additiv zu `evaluation.py`
hinzugefügt werden — ohne bestehende Contracts zu brechen.

**Was Sprint 13C tatsächlich geschlossen hat**:
- `PromotionRecord.comparison_report_path: str | None = None` in `tuning.py`
- `record-promotion --comparison PATH` CLI-Flag in `main.py`
- Upgrade-cycle liest den optionalen Comparison-Link als Audit-Kontext zurück
- Comparison-Report ist als optionaler Audit-Link mit dem Promotion-Record verbunden

**Was comparison.py-Contract-Abschnitte bedeuten**:
Die Codex-Spec 13.1 (comparison.py) ist **SUPERSEDED** — sie war die ursprüngliche Planung.
Die Codex-Spec 13.2 ist **reduziert** auf PromotionRecord + CLI-Flag (siehe aktualisierte Spec unten).

---

## Purpose

Sprint 7–12 etablierten: evaluation gates (G1–G6), promote-after-gate-check, training job
manifest, post-training eval link. Eine kritische Lücke bleibt:

**Ein neues Modellartefakt wird nur gegen absolute Schwellwerte (G1–G6) geprüft — nicht
gegen den Stand des vorherigen Modells.** Es ist bisher unsichtbar, ob ein trainiertes
Modell *besser* oder *schlechter* als die Baseline ist, solange es die Gates besteht.

Sprint 13 schließt diese Lücke mit:

1. **`EvaluationComparisonReport`** — persistierbares Vergleichsartefakt mit Regression Guard
2. **`RegressionGuard`** — pro Metrik: baseline vs. candidate + soft/hard Regression-Klassifikation
3. **`compare_evaluation_reports()`** — liest zwei `evaluation_report.json` Dateien, erzeugt Bericht
4. **`save_comparison_report()`** — schreibt Vergleichsbericht als JSON
5. **CLI-Extension**: `compare-evaluations --out` + `record-promotion --comparison`
6. **`PromotionRecord.comparison_report_path`** — optionaler Audit-Link (additiv)

---

## Bestehende Bausteine (wiederverwenden, NICHT duplizieren)

Sprint 13 baut auf bereits implementierten Primitiven aus `evaluation.py`:

| Symbol | Modul | Rolle in Sprint 13 |
|--------|-------|---------------------|
| `ComparisonMetrics` | `evaluation.py` | Delta-Container (alle 7 Metriken) |
| `compare_metrics(baseline, candidate)` | `evaluation.py` | Berechnet Deltas |
| `EvaluationMetrics` | `evaluation.py` | Metrik-Container für Einzelbericht |
| `validate_promotion(metrics)` | `evaluation.py` | G1–G6 Gate-Check |
| `compare-evaluations` CLI | `main.py` | Bestehender Command — wird erweitert |

Sprint 13 importiert diese direkt — kein Duplikat, kein Wrapper-Antipattern.

---

## Core Separation — Nicht verhandelbar

| Konzept | Was es ist | Was es NICHT ist |
|---------|-----------|------------------|
| **EvaluationComparisonReport** | Vergleich zweier Evaluationsberichte + Regression Guard | Kein Promotion-Trigger, kein Gate-Bypass |
| **RegressionGuard** | Per-Metrik soft/hard Regression-Klassifikation | Kein absoluter Promotion-Threshold (das sind G1–G6) |
| **Hard Regression** | Delta überschreitet definierte Schwelle — operator muss kenntlich werden | Kein automatischer Block — Operator entscheidet |
| **Promotion Gate** | G1–G6 via `validate_promotion()` — unverändert | Nicht durch Sprint 13 ersetzt oder erweitert |

**Zwei komplementäre Checks — beide sind Pflicht:**

| Check | Mechanismus | Zweck |
|-------|-------------|-------|
| Absolute Gates | `check-promotion` → G1–G6 | Kandidat besteht Minimalstandards |
| Relative Regression Guard | `compare-evaluations` → R1–R6 | Kandidat nicht schlechter als Baseline |

---

## Sprint-13 Scope (aktualisiert Sprint 13C)

### Was Sprint 13 lieferte / liefert

**✅ Bereits implementiert (evaluation.py als kanonischer Ort):**

- `EvaluationComparisonReport` — Vergleich zweier Evaluationsberichte mit Gate-Delta und Regression-Summary
- `compare_evaluation_reports(baseline_report, candidate_report)` — nimmt `EvaluationReport`-Objekte (pure computation)
- `save_evaluation_comparison_report(report, path, *, baseline_report, candidate_report)` — schreibt JSON mit `report_type="evaluation_report_comparison"`
- `RegressionSummary` — `has_regression`, `regressed_metrics`, `improved_metrics`, `regressed_gates`, `improved_gates`
- `compare-evaluations --out PATH` CLI (Existenzprüfung, Datei-Loading, Regression-Anzeige)
- `upgrade_cycle.py` + `upgrade-cycle-status` CLI (Sprint 13 Part 2)

**✅ Zusätzlich implementiert (Task 13.2, jetzt geschlossen):**

1. **`app/research/tuning.py` Extension (additiv)**:
   - `PromotionRecord.comparison_report_path: str | None = None`
   - `to_json_dict()`: `"comparison_report_path": self.comparison_report_path`
   - `save_promotion_record()`: neuer Parameter `comparison_report_path=None`
   - Wenn gesetzt: Pfad muss existieren → `FileNotFoundError`

2. **`record-promotion --comparison PATH` CLI-Flag**:
   - Optionales Flag
   - Pfad-Existenzprüfung → Exit 1 wenn nicht gefunden
   - JSON laden → `regression_summary.has_regression` auslesen
   - Wenn `True`: prominenter WARNING ausgeben (kein Block — Operator entscheidet, I-72)
   - `comparison_report_path` an `save_promotion_record()` übergeben

3. **CLI-Tests** in `tests/unit/test_cli.py`:
   - `test_research_record_promotion_with_comparison_flag`
   - `test_research_record_promotion_comparison_missing_file_exits_1`
   - `test_research_record_promotion_comparison_has_regression_prints_warning`

**❌ NICHT erstellt (superseded):**

- Kein `comparison.py` — `evaluation.py` ist kanonischer Ort
- Kein `test_comparison.py` — Tests in `test_cli.py` und `test_evaluation.py`
- Kein `RegressionGuard` / `HARD_REGRESSION_THRESHOLDS` — deferred, `RegressionSummary` reicht

### Was Sprint 13 NICHT liefert

- Keinen automatischen Promotion-Block bei Regressionen — operator decides (I-72)
- Keine neuen Provider, keine Analysis-Tier-Änderung
- Keine DB-Migration
- Kein Auto-Routing, kein Auto-Deploy
- Keine Änderung an G1–G6 Promotion Gates

---

## Regression Thresholds

### Soft Regression (sichtbar, kein Block)
Jede metrische Verschlechterung > ε (epsilon = 0.001) gilt als soft regression.

### Hard Regression (sichtbar, prominenter Warn-Output)
Überschreitung der folgenden Schwellen erzeugt `is_hard_regression=True`:

| Metrik | Richtung | Hard-Regression-Schwelle | Bedeutung |
|--------|---------|--------------------------|-----------|
| `sentiment_agreement` | ↑ höher = besser | Δ < -0.05 | Drop ≥ 5 Prozentpunkte |
| `priority_mae` | ↓ niedriger = besser | Δ > +0.5 | Verschlechterung ≥ 0.5 MAE-Punkte |
| `relevance_mae` | ↓ niedriger = besser | Δ > +0.05 | Verschlechterung ≥ 5pp |
| `impact_mae` | ↓ niedriger = besser | Δ > +0.05 | Verschlechterung ≥ 5pp |
| `tag_overlap_mean` | ↑ höher = besser | Δ < -0.05 | Drop ≥ 5pp |
| `false_actionable_rate` | ↓ niedriger = besser | Δ > +0.02 | Verschlechterung ≥ 2pp |

**Rational**: Hard-Regression-Schwellen sind operatorell bedeutsam. G1–G6 (absolute Gates) bleiben
die Pflichtschranken — die Regression Guard ist eine *zusätzliche* Sichtbarkeitsschicht.

Schwellen sind in `comparison.py` als `HARD_REGRESSION_THRESHOLDS` Konstante definiert
(nicht konfigurierbar per CLI — einfach und deterministisch).

---

## Datenmodelle

### `RegressionGuard`

```python
@dataclass
class RegressionGuard:
    """Per-metric regression classification for a single metric."""
    metric: str               # metric name (e.g. "sentiment_agreement")
    baseline: float           # baseline report value
    candidate: float          # candidate report value
    delta: float              # candidate - baseline (positive = candidate better for ↑ metrics)
    direction: str            # "higher_better" | "lower_better"
    is_regression: bool       # any worsening > epsilon (0.001)
    is_hard_regression: bool  # delta crosses HARD_REGRESSION_THRESHOLDS

    def to_json_dict(self) -> dict[str, object]:
        return {
            "metric": self.metric,
            "baseline": round(self.baseline, 6),
            "candidate": round(self.candidate, 6),
            "delta": round(self.delta, 6),
            "direction": self.direction,
            "is_regression": self.is_regression,
            "is_hard_regression": self.is_hard_regression,
        }
```

### `EvaluationComparisonReport`

```python
@dataclass
class EvaluationComparisonReport:
    """Comparison of two evaluation reports with regression guard.

    Wraps ComparisonMetrics (from evaluation.py) with explicit regression
    classification, persistence support, and promotion readiness context.

    Contract reference: docs/sprint13_comparison_contract.md
    Invariants: I-70, I-71, I-74
    """
    baseline_report_path: str            # absolute path to baseline evaluation_report.json
    candidate_report_path: str           # absolute path to candidate evaluation_report.json
    baseline_dataset_type: str           # dataset_type from baseline report
    candidate_dataset_type: str          # dataset_type from candidate report (must match)
    regression_guard: list[RegressionGuard]  # one entry per metric
    has_hard_regression: bool            # True if any metric crosses hard threshold
    soft_regression_count: int           # count of soft regressions (any worsening > epsilon)
    hard_regression_count: int           # count of hard regressions
    improvement_count: int               # count of metrics that improved
    candidate_passes_gates: bool         # True if candidate passes G1–G6
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "evaluation_comparison",
            "generated_at": datetime.now(UTC).isoformat(),
            "baseline_report_path": self.baseline_report_path,
            "candidate_report_path": self.candidate_report_path,
            "baseline_dataset_type": self.baseline_dataset_type,
            "candidate_dataset_type": self.candidate_dataset_type,
            "regression_guard": [r.to_json_dict() for r in self.regression_guard],
            "has_hard_regression": self.has_hard_regression,
            "soft_regression_count": self.soft_regression_count,
            "hard_regression_count": self.hard_regression_count,
            "improvement_count": self.improvement_count,
            "candidate_passes_gates": self.candidate_passes_gates,
            "notes": list(self.notes),
        }
```

### `PromotionRecord` — Sprint-13 Extension (additiv)

```python
@dataclass
class PromotionRecord:
    # ... alle bisherigen Felder (Sprint 8/9/12) unverändert ...
    training_job_record: str | None = None     # Sprint 12
    comparison_report_path: str | None = None  # NEU Sprint 13
```

---

## Harte Regression-Schwellen als Konstante

```python
# app/research/comparison.py
_EPSILON = 0.001  # below this delta is treated as "stable" (not a soft regression)

HARD_REGRESSION_THRESHOLDS: dict[str, float] = {
    # metric_name: threshold delta value that triggers hard regression
    # For higher_better metrics: negative delta crosses threshold
    # For lower_better metrics: positive delta crosses threshold
    "sentiment_agreement": -0.05,  # drop >= 5pp → hard regression
    "priority_mae": +0.5,          # increase >= 0.5 → hard regression
    "relevance_mae": +0.05,        # increase >= 5pp → hard regression
    "impact_mae": +0.05,           # increase >= 5pp → hard regression
    "tag_overlap_mean": -0.05,     # drop >= 5pp → hard regression
    "false_actionable_rate": +0.02, # increase >= 2pp → hard regression
}
```

---

## Funktionen — `app/research/comparison.py`

### `compare_evaluation_reports()`

```python
def compare_evaluation_reports(
    baseline_path: Path | str,
    candidate_path: Path | str,
    notes: list[str] | None = None,
) -> EvaluationComparisonReport:
    """Compare two evaluation report JSON files metric by metric.

    Builds on compare_metrics() and validate_promotion() from evaluation.py.
    Pure computation: reads two JSON files only. No DB, no LLM, no network.

    Raises:
        FileNotFoundError: if either path doesn't exist.
        ValueError: if dataset_types differ (I-74).
        json.JSONDecodeError / KeyError: if reports are malformed.

    Contract reference: docs/sprint13_comparison_contract.md §compare_evaluation_reports
    Invariants: I-70, I-71, I-74
    """
```

**Interner Ablauf:**
1. Beide JSON-Dateien laden: `json.loads(path.read_text(encoding="utf-8"))`
2. `dataset_type` aus beiden extrahieren → `ValueError` wenn verschieden (I-74)
3. `EvaluationMetrics` für baseline und candidate konstruieren
4. `compare_metrics(baseline_metrics, candidate_metrics)` → `ComparisonMetrics`
5. `validate_promotion(candidate_metrics)` → `PromotionValidation` → `candidate_passes_gates`
6. Pro Metrik: `RegressionGuard` erstellen (soft + hard Klassifikation)
7. Aggregate berechnen: `has_hard_regression`, `soft_regression_count`, `hard_regression_count`, `improvement_count`
8. `EvaluationComparisonReport` zurückgeben

**Metrik-Mapping** (delta → RegressionGuard):

| Metrik | delta = candidate - baseline | direction | soft regression | hard regression |
|--------|------------------------------|-----------|-----------------|-----------------|
| sentiment_agreement | Δ = cand - base | higher_better | Δ < -ε | Δ < -0.05 |
| priority_mae | Δ = cand - base | lower_better | Δ > +ε | Δ > +0.5 |
| relevance_mae | Δ = cand - base | lower_better | Δ > +ε | Δ > +0.05 |
| impact_mae | Δ = cand - base | lower_better | Δ > +ε | Δ > +0.05 |
| tag_overlap_mean | Δ = cand - base | higher_better | Δ < -ε | Δ < -0.05 |
| false_actionable_rate | Δ = cand - base | lower_better | Δ > +ε | Δ > +0.02 |

### `save_comparison_report()`

```python
def save_comparison_report(
    report: EvaluationComparisonReport,
    output_path: Path | str,
) -> Path:
    """Persist EvaluationComparisonReport as JSON. Write-once (I-38 extends).

    Contract reference: docs/sprint13_comparison_contract.md §save_comparison_report
    """
```

---

## CLI Contract — Sprint 13

### `compare-evaluations` — Extension (bestehender Command)

Der bestehende `research_compare_evaluations()` Command erhält:

```python
out: str | None = typer.Option(
    None, "--out",
    help="Path to persist EvaluationComparisonReport as JSON (audit trail)",
),
```

**Erweiterte Output-Struktur:**

1. **Per-Metrik-Tabelle** (bereits implementiert) — Baseline | Candidate | Delta (farbig)
2. **Regression Summary** — (NEU Sprint 13):
   ```
   Soft regressions: N  |  Hard regressions: N  |  Improvements: N
   ```
3. **Hard-Regression-Warnung** wenn `has_hard_regression=True`:
   ```
   [bold red]⚠ HARD REGRESSIONS DETECTED[/bold red]: N metric(s) crossed threshold.
   Operator acknowledgement required before promotion.
   ```
4. **Candidate Gates Status** (bereits implementiert) — PASSES / FAILS G1–G6
5. **Comparison saved** (nur wenn `--out`):
   ```
   [dim]Comparison report saved: <path>[/dim]
   ```

**Exit-Codes:**
- Exit 1: Datei nicht gefunden, JSON-Parsing-Fehler, `dataset_type` mismatch (I-74)
- Exit 0: immer sonst (reine Sichtbarkeit, kein Gate)

**Wichtig**: Der bestehende CLI-Code macht bereits das Kernlayout. Sprint 13 Codex:
- `compare_evaluation_reports()` aus `evaluation.py` aufrufen statt Inline-Berechnung
- `save_comparison_report()` aufrufen wenn `--out` gesetzt
- Hard-Regression-Warnung explizit trennen von Soft-Regression-Count

---

### `record-promotion` — Sprint-13 Extension

Neues optionales Flag:

```python
comparison: str | None = typer.Option(
    None, "--comparison",
    help="Optional path to evaluation_comparison_report.json (Sprint 13 audit trail)",
),
```

**Behavior bei `--comparison`:**
- Pfad muss existieren → Exit 1 wenn nicht
- JSON laden: `has_hard_regression` auslesen
- Wenn `has_hard_regression=True`: prominenter RED-Warning ausgeben:
  ```
  [bold red]WARNING: Comparison report shows hard regressions.[/bold red]
  Review comparison report before promoting. Proceeding only on explicit operator decision.
  ```
- Promotion wird NICHT geblockt — Operator muss explizit entscheiden (I-72)
- `comparison_report_path` an `save_promotion_record()` übergeben
- Im `PromotionRecord.to_json_dict()` erscheint `"comparison_report_path": "<path>"`

---

## Pre-Written Tests — Interface Alignment

Codex hat in `tests/unit/test_cli.py` drei Sprint-13-Antizipationstests geschrieben
(aktuell failing). Diese Tests geben bindende Hinweise auf erwartete CLI-Schnittstelle:

| Erwartung aus Tests | Contract-Anpassung |
|---------------------|-------------------|
| `--out PATH` Flag (nicht `--save-comparison`) | CLI Flag heißt `--out` |
| `report_type == "evaluation_report_comparison"` | `to_json_dict()` Key entsprechend |
| `regression_summary: {"has_regression": bool, "regressed_metrics": list[str]}` | Flach in `to_json_dict()` als `regression_summary` |
| `paired_count: {"baseline": N, "candidate": M, "delta": D}` | `to_json_dict()` enthält paired_count comparison |
| Fehlermeldung: `"Evaluation report not found"` | CLI-Fehlertext |
| Exit 1 wenn `dataset_type` Key fehlt im Report | `compare_evaluation_reports()` → `KeyError`/`ValueError` |

**Canonical Interface für `EvaluationComparisonReport.to_json_dict()`:**

```json
{
  "report_type": "evaluation_report_comparison",
  "generated_at": "...",
  "baseline_report_path": "...",
  "candidate_report_path": "...",
  "baseline_dataset_type": "internal_benchmark",
  "candidate_dataset_type": "internal_benchmark",
  "paired_count": {
    "baseline": 2,
    "candidate": 1,
    "delta": -1
  },
  "regression_guard": [ ... ],
  "regression_summary": {
    "has_regression": true,
    "regressed_metrics": ["priority_mae", "sentiment_agreement"]
  },
  "has_hard_regression": true,
  "soft_regression_count": 2,
  "hard_regression_count": 1,
  "improvement_count": 3,
  "candidate_passes_gates": true,
  "notes": []
}
```

**CLI Flag**: `--out PATH` (test-kompatibel).
**Error message**: `"Evaluation report not found: <path>"` bei fehlender Datei.

---

## ~~Codex-Spec 13.1 — `app/research/comparison.py` + Tests~~ (SUPERSEDED)

> **SUPERSEDED by Sprint 13C architecture decision (2026-03-19)**
>
> `comparison.py` wird nicht erstellt. `evaluation.py` ist kanonischer Ort.
> Alle Comparison-Typen und -Funktionen leben in `app/research/evaluation.py`.
>
> Begründung: Die Codex-Vorarbeit hatte `compare_evaluation_reports()`,
> `save_evaluation_comparison_report()`, `EvaluationComparisonReport`, `RegressionSummary`
> bereits vollständig in `evaluation.py` implementiert. Ein separates `comparison.py`
> würde Parallelarchitektur und Namenskollisionen erzeugen.
>
> Kanonische Symbole in `evaluation.py`:
> - `EvaluationComparisonReport` (bestehend)
> - `compare_evaluation_reports(baseline_report, candidate_report)` (bestehend)
> - `save_evaluation_comparison_report(report, path, ...)` (bestehend)
> - `RegressionSummary` mit `has_regression`, `regressed_metrics` (bestehend)
> - `CountComparison`, `EvaluationMetricDeltas`, `PromotionGateChanges` (bestehend)
>
> Hard-regression-Thresholds (R1–R6) und `RegressionGuard` deferred.
> `regression_summary.has_regression` ist operatives Regression-Flag.

---

## Codex-Spec 13.2 — PromotionRecord Extension + record-promotion --comparison CLI (Sprint 13C)

> **Aktualisiert Sprint 13C (2026-03-19)**: Scope reduziert.
> `compare-evaluations` ist vollständig implementiert (--out, Regression-Anzeige, Exit-Codes).
> Geschlossener Rest-Scope: `PromotionRecord.comparison_report_path` + `record-promotion --comparison`.

```
Module: app/cli/main.py (ERWEITERN), app/research/tuning.py (ERWEITERN)
Testmodul: tests/unit/test_cli.py (ERWEITERN)

ACHTUNG: compare-evaluations ist bereits vollstaendig implementiert.
  - compare_evaluation_reports() aus evaluation.py (nimmt EvaluationReport-Objekte)
  - save_evaluation_comparison_report() mit --out PATH Flag
  - Regression-Anzeige via regression_summary.has_regression
  - Exit 1 bei fehlender Datei, JSON-Fehler, dataset_type-Mismatch
  KEINE AENDERUNG an compare-evaluations noetig.

1. tuning.py PromotionRecord Extension (additiv):
   - Neues Feld: comparison_report_path: str | None = None
   - to_json_dict(): "comparison_report_path": self.comparison_report_path
     (immer enthalten, auch wenn None)
   - save_promotion_record(): neuer Keyword-Parameter comparison_report_path: Path | str | None = None
   - Wenn nicht None: Pfad muss existieren → FileNotFoundError
   - Wenn nicht None: str(path.resolve()) speichern (wie training_job_record)
   - Rueckwaertskompatibel: bestehende Tests bleiben unveraendert gruen

2. record-promotion --comparison Extension:
   - Neues Option: --comparison PATH (optional, default None)
   - Wenn gesetzt:
     a. Pfad-Existenzprüfung → Exit 1 wenn nicht gefunden
     b. JSON laden (json.loads)
     c. regression_summary.has_regression auslesen (None-safe: fehlender Key = False)
     d. Wenn True: prominenter WARNING ausgeben (nicht Exit 1, kein Block — I-72):
        "[bold yellow]WARNING:[/bold yellow] Comparison report shows regressions."
        "Review before promoting. Promotion proceeds on explicit operator decision."
     e. comparison_report_path an save_promotion_record() uebergeben
   - Bestehende record-promotion Tests bleiben unveraendert gruen

CLI-Tests (>= 3, neue Tests in test_cli.py):
  test_research_record_promotion_with_comparison_no_regression
    - comparison report ohne regression: kein Warning, comparison_report_path gesetzt
  test_research_record_promotion_with_comparison_has_regression_prints_warning
    - comparison report mit has_regression=True: Warning ausgegeben, Promotion NICHT geblockt
  test_research_record_promotion_comparison_missing_file_exits_1
    - --comparison auf nicht-existierende Datei: Exit 1

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ gruen (>= 691 + neue Tests, kein Rueckschritt)
  - [ ] record-promotion bestehende Tests (ohne --comparison) unveraendert gruen
  - [ ] PromotionRecord.comparison_report_path = None wenn --comparison nicht gesetzt
  - [ ] PromotionRecord.to_json_dict() enthaelt "comparison_report_path" Key immer
  - [ ] Warning ausgegeben aber kein Exit-Block bei regression
  - [ ] comparison_report_path in geschriebener promotion_record.json vorhanden
  - [ ] Exit 1 nur bei Datei-nicht-gefunden
```

---

## Sprint-13 Abschlusskriterien (aktualisiert Sprint 13C)

```
Sprint 13 gilt als abgeschlossen wenn:
  - [x] 13.1: SUPERSEDED — comparison.py nicht erstellt; evaluation.py ist kanonisch
  - [x] 13.2: PromotionRecord.comparison_report_path + record-promotion --comparison + Tests
  - [x] 13.3: sprint13_comparison_contract.md + contracts.md §24 + I-70–I-74
  - [x] 13.4: intelligence_architecture.md + AGENTS.md + TASKLIST.md Sprint-13 Update
  - [x] 13.5: sprint13_comparison_contract.md Part 2 + contracts.md §25 + I-75–I-79
  - [x] 13.6: upgrade_cycle.py + test_upgrade_cycle.py (12 Tests) + upgrade-cycle-status CLI
  - [x] ruff check . sauber
  - [x] pytest passing (>= 691 + neue 13.2-Tests, kein Rueckschritt)
  - [x] PromotionRecord rueckwaertskompatibel (comparison_report_path=None default)
  - [x] Kein Auto-Block bei Regressionen — nur Warning (I-72)
  - [x] G1–G6 Gates unveraendert
  - [x] compare-evaluations --out funktioniert, Regression-Anzeige vorhanden
```

---

## Vollständiger Promotion-Audit-Trail (Sprint 13C-Stand)

Nach Sprint 13C enthält ein vollständiger `PromotionRecord` optionale Links zu:

```json
{
  "record_type": "companion_promotion",
  "generated_at": "...",
  "promoted_model": "kai-analyst-v1",
  "evaluation_report": "/artifacts/post_train_eval.json",
  "tuning_artifact": "/artifacts/tuning_manifest.json",
  "training_job_record": "/artifacts/training_job_record.json",
  "comparison_report_path": "/artifacts/evaluation_comparison_report.json",
  "gates_summary": { "sentiment_pass": true, ... },
  "operator_note": "Sprint-13C — Regression-Check grün, Comparison vorhanden",
  "reversal_instructions": "Set APP_LLM_PROVIDER to previous value to revert companion"
}
```

Das Comparison-Report JSON (erzeugt von `compare-evaluations --out`) hat die Struktur:

```json
{
  "report_type": "evaluation_report_comparison",
  "generated_at": "...",
  "inputs": {
    "baseline_report": "/artifacts/baseline_eval.json",
    "candidate_report": "/artifacts/post_train_eval.json"
  },
  "baseline_dataset_type": "internal_benchmark",
  "candidate_dataset_type": "internal_benchmark",
  "paired_count": { "baseline": 5, "candidate": 5, "delta": 0 },
  "metric_deltas": { "sentiment_agreement": { "baseline": 0.85, "candidate": 0.90, "delta": 0.05 }, ... },
  "pass_fail_changes": { "baseline_promotable": true, "candidate_promotable": true, ... },
  "regression_summary": {
    "has_regression": false,
    "regressed_metrics": [],
    "improved_metrics": ["sentiment_agreement"],
    "regressed_gates": [],
    "improved_gates": []
  },
  "notes": []
}
```

**Regression-Semantik:**
- `regression_summary.has_regression = true` → mindestens eine Metrik verschlechterte sich (> ε)
- `regression_summary.regressed_metrics` → welche Metriken
- Dies ist **Audit-Kontext, kein Auto-Blocker** — Operator entscheidet (I-72)

Alle Felder außer `evaluation_report`, `operator_note`, `gates_summary` sind optional.
Die vollständige Kette (`tuning_artifact` → `training_job_record` → `comparison_report_path`)
ist freiwillig — sie macht Promotion-Entscheidungen nachvollziehbar und reproduzierbar.

---

## Security Notes

- `compare_evaluation_reports()` liest nur JSON-Dateien — kein `eval()`, kein Pickle
- `save_comparison_report()` schreibt nur JSON — kein Shell-Aufruf, keine Credentials
- Hard-Regression-Warning blockiert nicht — Operator muss aktiv weitermachen
- `comparison_report_path` in `PromotionRecord` enthält nur Dateipfad — keine Inhalte
- Keine externen API-Calls in allen Sprint-13 Code-Pfaden

---

## Invariant Summary (I-70 bis I-74)

Volltext in `docs/contracts.md §24`.

| ID | Regel |
|----|-------|
| I-70 | `EvaluationComparisonReport` ist ein Vergleichsartefakt nur. Kein Routing, kein Promotion-Trigger, kein Gate-Bypass. |
| I-71 | `compare_evaluation_reports()` ist pure computation — liest zwei JSON-Dateien. Kein DB, kein LLM, kein Netzwerk (I-62 gilt analog). |
| I-72 | Wenn `EvaluationComparisonReport.has_hard_regression=True` und `--comparison` an `record-promotion` übergeben, wird ein prominenter RED-Warning ausgegeben. Die Promotion wird NICHT automatisch geblockt — der Operator muss explizit entscheiden. Der `PromotionRecord` enthält `comparison_report_path` als Audit-Trail. |
| I-73 | `compare-evaluations` Exit 0 impliziert NICHT Promotionsfähigkeit. `check-promotion` auf dem Candidate-Report bleibt Pflicht (I-36, I-65). Comparison ist zusätzlicher Audit-Kontext. |
| I-74 | Baseline- und Candidate-Evaluationsbericht MÜSSEN denselben `dataset_type` haben. Verschiedene `dataset_type`-Werte → `ValueError` in `compare_evaluation_reports()`. |

---

# Part 2: Companion Upgrade Cycle Report

> **Sprint-13 Extension — Operator-geführter Upgrade Cycle Orchestrator**
>
> Upstream contracts: `docs/contracts.md §25`, invariants I-75–I-79.
> Upstream: Part 1 (EvaluationComparisonReport, I-70–I-74).

---

## Purpose

Sprint 7–13 Part 1 liefern einzelne kontrollierte Schritte:
`dataset-export` → `prepare-training-job` → `link-training-evaluation` → `compare-evaluations` → `check-promotion` → `record-promotion`.

Jeder Schritt ist unabhängig und auditierbar. Aber: **Es gibt kein zentrales Artefakt, das den aktuellen Stand eines Upgrade-Zyklus sichtbar macht.** Der Operator muss sich den Fortschritt aus mehreren JSON-Dateien zusammensuchen.

Sprint-13 Part 2 schließt diese Lücke:

1. **`UpgradeCycleReport`** — persistierbares Statusartefakt für einen kompletten Upgrade-Zyklus
2. **`build_upgrade_cycle_report()`** — liest existierende Artefakte, leitet Status ab
3. **`save_upgrade_cycle_report()`** — schreibt Bericht als JSON
4. **`upgrade-cycle-status` CLI** — zeigt aktuellen Zyklusstatus + nächste Schritte

**Der Orchestrator ersetzt keine Einzelkommandos.** Er liest, verketten und fasst zusammen.
**Kein Auto-Routing, kein Auto-Promote, keine Auto-Progression.**

---

## Core Separation — Nicht verhandelbar

| Was der Orchestrator tut | Was er NICHT tut |
|--------------------------|------------------|
| Existierende Artefakt-Pfade lesen | Neue Artefakte erzeugen (kein Training, keine Evaluation) |
| Status aus Artefakt-Präsenz ableiten | Status auto-voranschreiten |
| `validate_promotion()` auf vorhandenem Report aufrufen | Provider-Routing ändern |
| `promotion_readiness=True` setzen (informational) | `record-promotion` automatisch aufrufen |
| Nächste Schritte für Operator ausgeben | Einzelkommandos ersetzen |

---

## Statusmodell

```
prepared → training_recorded → evaluated → (compared →) promotable → promoted_manual
```

| Status | Bedingung |
|--------|-----------|
| `prepared` | `teacher_dataset_path` gesetzt und Datei vorhanden |
| `training_recorded` | zusätzlich `training_job_record_path` vorhanden |
| `evaluated` | zusätzlich `evaluation_report_path` vorhanden |
| `compared` | zusätzlich `comparison_report_path` vorhanden (optionaler Schritt) |
| `promotable` | `evaluated` + candidate besteht G1–G6 (`validate_promotion()` = True) |
| `promoted_manual` | zusätzlich `promotion_record_path` vorhanden |

**Wichtig**: `compared` und `promotable` sind orthogonal.
- `promotable` wird aus `evaluation_report.json` + `validate_promotion()` abgeleitet — nicht aus dem Comparison-Bericht.
- Der Comparison-Bericht ist empfohlen, aber nicht verpflichtend für `promotable`.
- Status-Reihenfolge: `promoted_manual` > `promotable` > `compared` > `evaluated` > `training_recorded` > `prepared`.

---

## Datenmodell

### `UpgradeCycleReport`

```python
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class UpgradeCycleReport:
    """Operator-facing status summary of a companion upgrade cycle.

    Records which artifacts exist, what the current phase is, and
    whether the candidate is ready for manual promotion.

    INVARIANT (I-75): Pure read/summarize artifact only.
    No training, no evaluation, no routing changes, no auto-promotion.

    Contract reference: docs/sprint13_comparison_contract.md Part 2
    Invariants: I-75–I-79
    """

    teacher_dataset_path: str              # required — teacher JSONL path
    training_job_record_path: str | None = None   # written by prepare-training-job
    evaluation_report_path: str | None = None     # written by post-training eval
    comparison_report_path: str | None = None     # written by compare-evaluations
    promotion_readiness: bool = False             # True = candidate passes G1–G6
    promotion_record_path: str | None = None      # set after record-promotion
    status: str = "prepared"                      # current lifecycle phase (see status model)
    notes: list[str] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, object]:
        return {
            "report_type": "upgrade_cycle_report",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": self.status,
            "teacher_dataset_path": self.teacher_dataset_path,
            "training_job_record_path": self.training_job_record_path,
            "evaluation_report_path": self.evaluation_report_path,
            "comparison_report_path": self.comparison_report_path,
            "promotion_readiness": self.promotion_readiness,
            "promotion_record_path": self.promotion_record_path,
            "notes": list(self.notes),
        }
```

---

## Funktionen — `app/research/upgrade_cycle.py`

### `derive_cycle_status()`

```python
def derive_cycle_status(
    teacher_dataset_path: str,
    training_job_record_path: str | None,
    evaluation_report_path: str | None,
    comparison_report_path: str | None,
    promotion_record_path: str | None,
    promotion_readiness: bool,
) -> str:
    """Derive current lifecycle status from artifact presence.

    Status priority (highest wins):
    promoted_manual > promotable > compared > evaluated > training_recorded > prepared
    """
```

**Logik**:
1. `promotion_record_path` nicht None und Datei existiert → `"promoted_manual"`
2. `promotion_readiness=True` → `"promotable"`
3. `comparison_report_path` nicht None und Datei existiert → `"compared"`
4. `evaluation_report_path` nicht None und Datei existiert → `"evaluated"`
5. `training_job_record_path` nicht None und Datei existiert → `"training_recorded"`
6. Sonst → `"prepared"`

### `build_upgrade_cycle_report()`

```python
def build_upgrade_cycle_report(
    teacher_dataset_path: str | Path,
    *,
    training_job_record_path: str | Path | None = None,
    evaluation_report_path: str | Path | None = None,
    comparison_report_path: str | Path | None = None,
    promotion_record_path: str | Path | None = None,
    notes: list[str] | None = None,
) -> UpgradeCycleReport:
    """Build an UpgradeCycleReport from existing artifact paths.

    Reads evaluation_report.json (if present) to derive promotion_readiness
    via validate_promotion(). All other paths are recorded as-is.

    Pure computation: reads JSON files only.
    No DB reads, no LLM calls, no network. (I-75, I-62 extends here.)

    Raises:
        FileNotFoundError: if teacher_dataset_path does not exist.
    """
```

**Interner Ablauf**:
1. `teacher_dataset_path` muss existieren → `FileNotFoundError` wenn nicht
2. `promotion_readiness = False`
3. Wenn `evaluation_report_path` existiert:
   - `json.loads(path.read_text())` → `EvaluationMetrics` konstruieren
   - `validate_promotion(metrics)` → `promotion_readiness = validation.is_promotable`
4. `derive_cycle_status(...)` aufrufen → `status`
5. `UpgradeCycleReport` zurückgeben

### `save_upgrade_cycle_report()`

```python
def save_upgrade_cycle_report(
    report: UpgradeCycleReport,
    output_path: Path | str,
) -> Path:
    """Persist UpgradeCycleReport as JSON. Creates parent dirs."""
```

---

## CLI Contract — `upgrade-cycle-status`

```
research upgrade-cycle-status TEACHER_FILE
  [--training-job PATH]
  [--eval-report PATH]
  [--comparison PATH]
  [--promotion-record PATH]
  [--out PATH]          default: upgrade_cycle_report.json
  [--notes TEXT]        optional note to attach
```

**Output-Struktur**:
1. `Upgrade Cycle Status` Tabelle mit allen Artefakt-Pfaden + Status (vorhanden / fehlt)
2. Aktueller Status: `[bold green]PROMOTABLE[/bold green]` / `[yellow]EVALUATED[/yellow]` / etc.
3. `promotion_readiness` Anzeige: `✓ Candidate passes G1–G6` oder `✗ Not yet evaluated`
4. `Next Step` Hinweis (welcher Befehl als nächstes ausgeführt werden soll)
5. `[dim]Cycle report saved: <path>[/dim]` wenn `--out` gesetzt

**Next Step Hinweise**:

| Status | Empfohlener nächster Schritt |
|--------|------------------------------|
| `prepared` | `research prepare-training-job <teacher> <base> <target-id>` |
| `training_recorded` | Externes Training ausführen, dann `research link-training-evaluation` |
| `evaluated` | `research compare-evaluations <baseline> <candidate>` (empfohlen) ODER `research check-promotion <eval-report>` |
| `compared` | `research check-promotion <eval-report>` |
| `promotable` | `research record-promotion <eval-report> <model-id> --endpoint ...` |
| `promoted_manual` | Upgrade Cycle abgeschlossen. APP_LLM_PROVIDER konfigurieren. |

**Exit-Codes**:
- Exit 1: `teacher_dataset_path` nicht gefunden
- Exit 0: alles andere (reine Anzeige + Artefakt-Erstellung)

---

## Codex-Spec 13.5 — `app/research/upgrade_cycle.py` + Tests + CLI

```
Modul: app/research/upgrade_cycle.py (NEU)
Testmodul: tests/unit/test_upgrade_cycle.py (NEU)
CLI: app/cli/main.py — research_app.command("upgrade-cycle-status") (NEU)

Imports (DIREKT verwenden, NICHT duplizieren):
  from app.research.evaluation import EvaluationMetrics, validate_promotion

Datenklassen:
  UpgradeCycleReport: teacher_dataset_path, training_job_record_path,
    evaluation_report_path, comparison_report_path, promotion_readiness,
    promotion_record_path, status, notes
    to_json_dict() → report_type="upgrade_cycle_report"

Funktionen:
  derive_cycle_status(...) → str
    - Reihenfolge: promoted_manual > promotable > compared > evaluated
                   > training_recorded > prepared
    - Nur Datei-Existenz prüfen (Path.exists()) — kein JSON lesen

  build_upgrade_cycle_report(teacher_dataset_path, *, ...) → UpgradeCycleReport
    - Raises FileNotFoundError wenn teacher_dataset_path nicht existiert
    - Liest evaluation_report.json wenn vorhanden → validate_promotion()
    - Ruft derive_cycle_status() auf
    - KEINE DB-Calls, LLM-Calls, Netzwerk (I-75, I-62)

  save_upgrade_cycle_report(report, output_path) → Path
    - JSON indent=2 sort_keys=True, parent.mkdir(parents=True, exist_ok=True)

CLI:
  @research_app.command("upgrade-cycle-status")
  def research_upgrade_cycle_status(
      teacher_file: str = typer.Argument(...),
      training_job: str | None = typer.Option(None, "--training-job"),
      eval_report: str | None = typer.Option(None, "--eval-report"),
      comparison: str | None = typer.Option(None, "--comparison"),
      promotion_record: str | None = typer.Option(None, "--promotion-record"),
      out: str = typer.Option("upgrade_cycle_report.json", "--out"),
  ) -> None

Tests (>= 10):
  test_build_upgrade_cycle_report_prepared_status
  test_build_upgrade_cycle_report_training_recorded_status
  test_build_upgrade_cycle_report_evaluated_status
  test_build_upgrade_cycle_report_compared_status
  test_build_upgrade_cycle_report_promotable_status
  test_build_upgrade_cycle_report_promoted_manual_status
  test_build_upgrade_cycle_report_raises_on_missing_teacher
  test_build_upgrade_cycle_report_promotion_readiness_from_eval_report
  test_save_upgrade_cycle_report_creates_valid_json
  test_save_upgrade_cycle_report_creates_parent_dirs
  test_derive_cycle_status_priority_order
  test_upgrade_cycle_report_to_json_dict_structure

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_upgrade_cycle.py gruen (>= 10 neue Tests)
  - [ ] pytest tests/unit/ gruen (>= vorher + neue Tests, kein Rueckschritt)
  - [ ] KEINE Duplikation von validate_promotion() oder EvaluationMetrics
  - [ ] build_upgrade_cycle_report() macht KEINE DB-Calls, LLM-Calls, Netzwerk
  - [ ] derive_cycle_status() benutzt nur Path.exists() — kein JSON-Parsen
  - [ ] promotion_readiness korrekt aus validate_promotion() abgeleitet
  - [ ] status priority: promoted_manual > promotable > compared > evaluated > ...
```

---

## Vollständiger Upgrade Cycle — Operator Workflow

Nach Sprint 13 kann ein vollständiger Companion-Upgrade-Zyklus wie folgt aussehen:

```bash
# Schritt 1: Teacher-Dataset exportieren
research dataset-export --teacher-only --out teacher_v2.jsonl

# Schritt 2: Training-Job-Manifest erstellen
research prepare-training-job teacher_v2.jsonl llama3.2:3b kai-v2 --out training_job.json

# [EXTERN] Training durchführen (Ollama, OpenAI Fine-Tuning, etc.)

# Schritt 3: Post-Training Evaluation verknüpfen
research link-training-evaluation training_job.json eval_v2.json kai-v2 http://localhost:11434

# Schritt 4: Regression-Vergleich mit Baseline
research compare-evaluations baseline_eval.json eval_v2.json --out comparison.json

# Schritt 5: Gate-Check
research check-promotion eval_v2.json

# Schritt 6: Cycle-Status zusammenfassen
research upgrade-cycle-status teacher_v2.jsonl \
  --training-job training_job.json \
  --eval-report eval_v2.json \
  --comparison comparison.json \
  --out upgrade_cycle.json

# Schritt 7: Manuelle Promotion (nach Operator-Entscheidung)
research record-promotion eval_v2.json kai-v2 \
  --endpoint http://localhost:11434 \
  --training-job training_job.json \
  --comparison comparison.json \
  --operator-note "Sprint-13 Post-Training — alle Gates grün, kein hard regression"
```

**Jeder Schritt ist unabhängig aufrufbar.** `upgrade-cycle-status` ist nur eine Zusammenfassung.

---

## Security Notes (Part 2)

- `build_upgrade_cycle_report()` liest nur JSON-Dateien — kein `eval()`, kein Pickle
- `save_upgrade_cycle_report()` schreibt nur JSON — kein Shell-Aufruf, keine Credentials
- `upgrade-cycle-status` CLI macht keine Netzwerk-Calls
- `promotion_readiness=True` ist rein informativ — kein Platform-Code ändert Routing (I-77)
- Keine externen API-Calls in allen Sprint-13-Part-2-Code-Pfaden

---

## Invariant Summary (I-75 bis I-79)

Volltext in `docs/contracts.md §25`.

| ID | Regel |
|----|-------|
| I-75 | `UpgradeCycleReport` ist ein reines Read/Summarize-Artefakt. `build_upgrade_cycle_report()` führt kein Training, keine Evaluation, keine Promotion und keine Routing-Änderung durch. Einzige I/O: JSON-Dateien lesen via `json.loads()`. |
| I-76 | `UpgradeCycleReport.status` wird ausschließlich aus Artefakt-Präsenz (`Path.exists()`) abgeleitet — nie von der Plattform auto-vorangeschritten. Kein Code setzt `status` implizit auf einen höheren Wert, ohne dass der Operator einen neuen Artefakt-Pfad übergeben hat. |
| I-77 | `UpgradeCycleReport.promotion_readiness=True` ist informational. Kein Platform-Code ruft `record-promotion` oder ändert `APP_LLM_PROVIDER` auf Basis dieses Felds. Der Operator muss `record-promotion` explizit ausführen (I-36, I-68 gelten analog). |
| I-78 | `UpgradeCycleReport.promotion_record_path` wird NUR gesetzt, wenn der Operator diesen Pfad explizit an `build_upgrade_cycle_report()` oder die CLI übergibt. Er wird nie aus env vars oder Settings auto-befüllt. |
| I-79 | Jeder `UpgradeCycleReport` repräsentiert EINEN Upgrade-Versuch. Parallele oder sequenzielle Zyklen (v1→v2, v2→v3) produzieren separate Dateien. Ein Upgrade-Bericht wird niemals in-place überschrieben (I-38 gilt analog). |
