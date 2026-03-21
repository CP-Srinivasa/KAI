# Sprint 9 — Promotion Audit Hardening

> Canonical reference for Sprint 9. Codex must read this document before touching any file.
> Full invariant list: `docs/contracts.md §20` (I-46–I-50).

---

## Purpose

Sprint 9 formalizes three properties that must hold after a companion promotion decision:

1. **I-34 ist automatisiert**: `false_actionable_rate` ist ein berechnetes 6. Gate in
   `validate_promotion()` — nicht mehr manuell, nicht mehr deferred. (I-46)
2. **Promotion Record ist selbstdokumentierend**: `PromotionRecord` beinhaltet `gates_summary` —
   einen Snapshot aller 6 Gate-Ergebnisse zum Zeitpunkt des Schreibens. (I-47/I-48)
3. **Artifact-Chain-Konsistenz**: Wenn `--tuning-artifact` angegeben wird, muss das Artifact
   auf denselben Eval-Report verweisen, der auch für die Promotion verwendet wird. (I-49)

Kein Training. Kein Routing-Wechsel. Kein neuer Provider.

---

## Context

Codex erweiterte Sprint 8 über den ursprünglichen Contract hinaus:

| Komponente | Sprint-8-Erweiterung durch Codex |
|------------|----------------------------------|
| `EvaluationMetrics` | Neu: `actionable_accuracy`, `false_actionable_rate` |
| `PromotionValidation` | Neu: `false_actionable_pass` (6. Gate, ≤ 0.05) |
| `compare_datasets()` | Berechnet `false_actionable_rate` aus Paaren |
| CLI `check-promotion` | Zeigt alle 6 Gates inkl. `false_actionable_rate` |
| CLI `benchmark-companion` | Zeigt beide neue Metriken |

Sprint 9 formalisiert diese Erweiterung, schreibt die fehlenden Tests, und vollendet
den Promotion-Record-Audit-Trail.

---

## Was sich ändert

### Was bereits implementiert ist (Sprint-8, Codex-Erweiterung)

- `false_actionable_rate` ∈ `EvaluationMetrics` ✅
- `false_actionable_pass = false_actionable_rate <= 0.05` in `validate_promotion()` ✅
- `check-promotion` zeigt G6 ✅

### Was in Sprint 9 implementiert wird

| Komponente | Änderung |
|------------|----------|
| `PromotionRecord` | + `gates_summary: dict[str, bool] \| None = None` |
| `PromotionRecord.to_json_dict()` | Gibt `gates_summary` im JSON aus |
| `save_promotion_record()` | + Parameter `gates_summary` + Tuning-Artifact-Linkage-Check |
| `record-promotion` CLI | Baut `gates_summary` aus `validate_promotion()`, übergibt es; prüft Artifact-Linkage |
| Tests | `test_tuning.py` + `test_cli.py` (oder `test_record_promotion.py`) |

---

## Gate-Tabelle (nach Sprint 9)

Alle 6 Gates müssen bestehen, damit `is_promotable = True`:

| Gate | Metrik | Schwellwert | Implementiert in |
|------|--------|-------------|-----------------|
| G1 | `sentiment_agreement` | ≥ 0.85 | `validate_promotion()` Sprint 7 |
| G2 | `priority_mae` | ≤ 1.5 | `validate_promotion()` Sprint 7 |
| G3 | `relevance_mae` | ≤ 0.15 | `validate_promotion()` Sprint 7 |
| G4 | `impact_mae` | ≤ 0.20 | `validate_promotion()` Sprint 7 |
| G5 | `tag_overlap_mean` | ≥ 0.30 | `validate_promotion()` Sprint 7 |
| G6 | `false_actionable_rate` | ≤ 0.05 | `validate_promotion()` Sprint 8 (Codex-Erweiterung), I-46 |

---

## PromotionRecord Schema (nach Sprint 9)

```json
{
  "record_type": "companion_promotion",
  "generated_at": "2026-03-19T10:00:00+00:00",
  "promoted_model": "kai-analyst-v1",
  "promoted_endpoint": "http://localhost:11434",
  "evaluation_report": "/abs/path/evaluation_report.json",
  "tuning_artifact": "/abs/path/tuning_manifest.json",
  "operator_note": "Reviewed all 6 gates. FAR = 0.02. Approved.",
  "gates_summary": {
    "sentiment_pass": true,
    "priority_pass": true,
    "relevance_pass": true,
    "impact_pass": true,
    "tag_overlap_pass": true,
    "false_actionable_pass": true
  },
  "reversal_instructions": "Set APP_LLM_PROVIDER to previous value to revert companion"
}
```

`gates_summary = null` wenn nicht übergeben (rückwärtskompatibel).

---

## app/research/tuning.py — Änderungen

### PromotionRecord (aktualisiert)

```python
@dataclass
class PromotionRecord:
    promoted_model: str
    promoted_endpoint: str
    evaluation_report: str
    operator_note: str
    tuning_artifact: str | None = None
    gates_summary: dict[str, bool] | None = None   # NEU Sprint 9

    def to_json_dict(self) -> dict[str, object]:
        return {
            "record_type": "companion_promotion",
            "generated_at": datetime.now(UTC).isoformat(),
            "promoted_model": self.promoted_model,
            "promoted_endpoint": self.promoted_endpoint,
            "evaluation_report": self.evaluation_report,
            "tuning_artifact": self.tuning_artifact,
            "operator_note": self.operator_note,
            "gates_summary": self.gates_summary,      # NEU Sprint 9
            "reversal_instructions": (
                "Set APP_LLM_PROVIDER to previous value to revert companion"
            ),
        }
```

### save_promotion_record() — aktualisierte Signatur

```python
def save_promotion_record(
    output_path: Path | str,
    *,
    promoted_model: str,
    promoted_endpoint: str,
    evaluation_report: Path | str,
    tuning_artifact: Path | str | None = None,
    operator_note: str,
    gates_summary: dict[str, bool] | None = None,   # NEU Sprint 9
) -> Path:
```

**Neue Validierung (I-49)**:
```python
if tuning_artifact is not None:
    ta_path = Path(tuning_artifact)
    if ta_path.exists():
        ta_data = json.loads(ta_path.read_text(encoding="utf-8"))
        ta_report = ta_data.get("evaluation_report")
        if ta_report is not None:
            resolved_eval = Path(evaluation_report).resolve()
            resolved_ta = Path(ta_report).resolve()
            if resolved_eval != resolved_ta:
                raise ValueError(
                    f"Tuning artifact evaluation_report mismatch: "
                    f"{resolved_ta} != {resolved_eval} (I-49)"
                )
```

**Wichtig**: Wenn `tuning_artifact` existiert aber kein `evaluation_report`-Feld hat
(ältere Manifeste), ist die Validierung ein No-op — kein Fehler, backward-compatible.

---

## CLI: record-promotion (nach Sprint 9)

```
research record-promotion <report_file> <model_id> \
  --endpoint <url> \
  --operator-note "<text>" \
  [--tuning-artifact <path>] \
  [--out promotion_record.json]
```

**Aktualisierter Ablauf**:

```
1. report_path prüfen → Exit 1 wenn nicht vorhanden
2. JSON laden + EvaluationMetrics bauen → Exit 1 bei Parse-Fehler
3. validate_promotion(metrics) → PromotionValidation
4. Wenn not is_promotable → Exit 1
5. gates_summary = {
       "sentiment_pass": validation.sentiment_pass,
       "priority_pass": validation.priority_pass,
       "relevance_pass": validation.relevance_pass,
       "impact_pass": validation.impact_pass,
       "tag_overlap_pass": validation.tag_overlap_pass,
       "false_actionable_pass": validation.false_actionable_pass,
   }
6. Wenn --tuning-artifact: load artifact, check linkage → Exit 1 bei Mismatch
7. save_promotion_record(..., gates_summary=gates_summary)
8. Ausgabe: Aktivierungs- + Reversierungshinweis
```

**Kein Behavior-Change** für bestehende Aufrufe ohne `--tuning-artifact`.

---

## Codex-Spec 9.1 — PromotionRecord + save_promotion_record

```
## Task: Sprint 9.1 — PromotionRecord.gates_summary + Artifact Linkage

Agent: Codex
Phase: Sprint 9
Modul: app/research/tuning.py, tests/unit/test_tuning.py
Typ: feature + test

Spec-Referenz: docs/sprint9_promotion_audit_contract.md §app/research/tuning.py

Änderungen in app/research/tuning.py:

1. PromotionRecord dataclass — neues Feld:
   gates_summary: dict[str, bool] | None = None

2. to_json_dict() — Feld einbetten:
   "gates_summary": self.gates_summary,    # nach "operator_note", vor "reversal_instructions"

3. save_promotion_record() — neuer Parameter:
   gates_summary: dict[str, bool] | None = None

   In record construction:
     record = PromotionRecord(
         ...,
         gates_summary=gates_summary,
     )

4. Tuning-Artifact-Linkage-Validierung (nach I-45-Checks, vor record construction):
   Import: import json (bereits vorhanden)

   if tuning_artifact is not None:
       ta_path = Path(tuning_artifact)
       if ta_path.exists():
           ta_data = json.loads(ta_path.read_text(encoding="utf-8"))
           ta_report = ta_data.get("evaluation_report")
           if ta_report is not None:
               resolved_eval = Path(evaluation_report).resolve()
               resolved_ta = Path(ta_report).resolve()
               if resolved_eval != resolved_ta:
                   raise ValueError(
                       f"Tuning artifact evaluation_report mismatch: "
                       f"{resolved_ta} != {resolved_eval} (I-49)"
                   )

Tests in tests/unit/test_tuning.py:

  test_save_promotion_record_embeds_gates_summary(tmp_path):
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}")
    gates = {"sentiment_pass": True, "priority_pass": True, "relevance_pass": True,
             "impact_pass": True, "tag_overlap_pass": True, "false_actionable_pass": True}
    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Approved",
        gates_summary=gates,
    )
    data = json.loads(path.read_text())
    assert data["gates_summary"] == gates

  test_save_promotion_record_null_gates_summary(tmp_path):
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}")
    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        operator_note="Approved",
    )
    data = json.loads(path.read_text())
    assert data["gates_summary"] is None

  test_save_promotion_record_tuning_artifact_linkage_mismatch(tmp_path):
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}")
    other_report = tmp_path / "other.json"
    other_report.write_text("{}")
    tuning_artifact = tmp_path / "manifest.json"
    tuning_artifact.write_text(
        json.dumps({"evaluation_report": str(other_report.resolve())})
    )
    with pytest.raises(ValueError, match="mismatch"):
        save_promotion_record(
            tmp_path / "promo.json",
            promoted_model="kai-v1",
            promoted_endpoint="http://localhost:11434",
            evaluation_report=eval_report,
            tuning_artifact=tuning_artifact,
            operator_note="Approved",
        )

  test_save_promotion_record_tuning_artifact_no_eval_report_field(tmp_path):
    # Backward-compatible: artifact ohne evaluation_report-Feld → kein Fehler
    eval_report = tmp_path / "report.json"
    eval_report.write_text("{}")
    tuning_artifact = tmp_path / "manifest.json"
    tuning_artifact.write_text(json.dumps({"model_base": "llama3"}))  # kein evaluation_report
    # kein ValueError erwartet
    path = save_promotion_record(
        tmp_path / "promo.json",
        promoted_model="kai-v1",
        promoted_endpoint="http://localhost:11434",
        evaluation_report=eval_report,
        tuning_artifact=tuning_artifact,
        operator_note="Approved",
    )
    assert path.exists()

Constraints:
  - NICHT: evaluation.py oder andere Module ändern
  - NICHT: bestehende Tests brechen (gates_summary=None ist Default — rückwärtskompatibel)
  - Nur tuning.py + test_tuning.py

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_tuning.py grün (alle Tests inkl. 4 neue)
  - [ ] pytest tests/unit/ grün (589 Basis, kein Rückschritt)
```

---

## Codex-Spec 9.2 — record-promotion CLI

```
## Task: Sprint 9.2 — record-promotion gates_summary + Artifact Linkage

Agent: Codex
Phase: Sprint 9
Modul: app/cli/main.py
Typ: feature (additiv)

Spec-Referenz: docs/sprint9_promotion_audit_contract.md §CLI record-promotion

Änderungen in research_record_promotion():

Nach validate_promotion(metrics):

  gates_summary = {
      "sentiment_pass": validation.sentiment_pass,
      "priority_pass": validation.priority_pass,
      "relevance_pass": validation.relevance_pass,
      "impact_pass": validation.impact_pass,
      "tag_overlap_pass": validation.tag_overlap_pass,
      "false_actionable_pass": validation.false_actionable_pass,
  }

Wenn --tuning-artifact angegeben (vor save_promotion_record()):
  (Die Linkage-Prüfung läuft nun auch in save_promotion_record() — Fehler propagiert
   als ValueError und wird im try/except-Block des CLI gefangen. Kein zusätzlicher
   CLI-seitiger Check nötig — save_promotion_record() ist kanonisch.)

save_promotion_record() Aufruf — gates_summary hinzufügen:
  record_path = save_promotion_record(
      out,
      promoted_model=model_id,
      promoted_endpoint=endpoint,
      evaluation_report=report_path,
      tuning_artifact=tuning_artifact,
      operator_note=operator_note,
      gates_summary=gates_summary,   # NEU
  )

ValueError-Handling um artifact-linkage-Fehler erweitern:
  except ValueError as e:
      console.print(f"[red]Promotion record error:[/red] {e}")
      raise typer.Exit(1) from e
  (Bereits vorhanden — kein neuer Code nötig, ValueError aus Linkage-Check wird gefangen.)

Constraints:
  - NICHT: validate_promotion() oder EvaluationMetrics ändern
  - NICHT: bestehende CLI-Flags oder bestehenden Output ändern
  - Nur app/cli/main.py

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/ grün (589 Basis)
  - [ ] Output-JSON enthält gates_summary dict
  - [ ] --tuning-artifact mit Mismatch → Exit 1 + "[red]Promotion record error:[/red]"-Zeile
  - [ ] research --help zeigt record-promotion unverändert
```

---

## Codex-Spec 9.3 — Tests für CLI check-promotion (G6) + record-promotion (gates_summary)

```
## Task: Sprint 9.3 — Tests für Sprint-9 Änderungen

Agent: Codex
Phase: Sprint 9
Modul: tests/unit/test_cli.py (erweitern)
Typ: test

Ziel: Verifikation dass:
a) check-promotion G6 korrekt anzeigt und bei FAR > 0.05 mit Exit 1 endet
b) record-promotion gates_summary korrekt in Output-JSON schreibt

Tests:

  test_research_check_promotion_g6_pass(tmp_path, runner):
    report = {
        "report_type": "dataset_evaluation",
        "metrics": {
            "sentiment_agreement": 0.90, "priority_mae": 1.0,
            "relevance_mae": 0.10, "impact_mae": 0.15,
            "tag_overlap_mean": 0.40, "actionable_accuracy": 0.85,
            "false_actionable_rate": 0.02,
            "sample_count": 50, "missing_pairs": 0,
        },
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    result = runner.invoke(app, ["research", "check-promotion", str(path)])
    assert result.exit_code == 0

  test_research_check_promotion_g6_fail(tmp_path, runner):
    report mit false_actionable_rate=0.10 (> 0.05)
    result = runner.invoke(app, ["research", "check-promotion", str(path)])
    assert result.exit_code == 1

  test_research_record_promotion_embeds_gates_summary(tmp_path, runner):
    # Erstelle report.json mit allen Gates pass inkl. FAR <= 0.05
    report = { "metrics": { ..., "false_actionable_rate": 0.02, "actionable_accuracy": 0.90 } }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    out_path = tmp_path / "promo.json"
    result = runner.invoke(app, [
        "research", "record-promotion", str(path), "kai-v1",
        "--endpoint", "http://localhost:11434",
        "--operator-note", "Approved by operator",
        "--out", str(out_path),
    ])
    assert result.exit_code == 0
    data = json.loads(out_path.read_text())
    assert "gates_summary" in data
    assert data["gates_summary"]["false_actionable_pass"] is True

Constraints:
  - NICHT: app/cli/main.py oder tuning.py ändern (Tests only)
  - Runner: from typer.testing import CliRunner, app aus app.cli.main

Akzeptanzkriterien:
  - [ ] ruff check . sauber
  - [ ] pytest tests/unit/test_cli.py grün
  - [ ] pytest tests/unit/ grün (571 Basis + neue Tests)
```

---

## Sprint-9 Abschlusskriterien

```
Sprint 9 gilt als abgeschlossen wenn:
  - [ ] 9.1: PromotionRecord.gates_summary + Artifact-Linkage implementiert + getestet
  - [ ] 9.2: record-promotion CLI gibt gates_summary weiter
  - [ ] 9.3: CLI-Tests für G6 (check-promotion) + gates_summary (record-promotion)
  - [ ] ruff check . sauber
  - [ ] pytest passing (>= 596 Tests, kein Rückschritt)
  - [ ] check-promotion, evaluate-datasets, benchmark-companion unverändert
  - [ ] docs/contracts.md §20 + I-46–I-50 ✅
  - [ ] I-34 in contracts.md aktualisiert (nicht mehr "manuell/deferred")
  - [ ] I-45 in contracts.md aktualisiert ("6 Gates")
  - [ ] TASKLIST.md Sprint-9 Tasks aktualisiert
  - [ ] AGENTS.md Test-Stand aktualisiert
  - [ ] sprint9_promotion_audit_contract.md vollständig und konsistent
```

---

## Sprint-10 Ausblick

Sprint 9 legt das Fundament für einen kontrollierten Evolutionsschritt:

- Promotion Records sind jetzt vollständige Audit-Artefakte mit Gate-Evidenz
- Artifact-Chain ist lückenlos validiert
- I-34 ist nicht mehr manuell — alle 6 Gates sind automatisiert und getestet

**Sprint 10 kann** dann den nächsten Schritt definieren:
> Companion Shadow-Run — Companion läuft parallel zum aktiven Provider auf Live-Dokumenten.
> Ergebnisse werden geloggt aber **nicht** für Analyse-Output verwendet.
> Gibt echte Online-Qualitätsdaten für Promotion-Entscheidungen ohne Routing-Risiko.

Konkrete Scope-Entscheidung für Sprint 10 trifft der Operator nach Sprint-9-Abschluss.
