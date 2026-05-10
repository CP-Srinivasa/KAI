"""Verdrahtungs-Helfer für die Bayesian Confidence Engine.

Eine Stelle, an der Settings → SignalGenerator-Kwargs übersetzt werden.
Das hält ``build_trading_loop`` schlank und macht den Schalterzustand
testbar, ohne die ganze Loop hochzufahren.

Vertrag:
  - Engine = ``None`` wenn ``settings.risk.bayes_confidence_enabled`` False.
    Generator verhält sich dann exakt wie vor der Bayes-Integration.
  - Engine ≠ ``None`` und Audit-Pfad gesetzt sobald der Flag True ist.
    Der Operator kann zusätzlich ``bayes_confidence_shadow_only=False``
    setzen, um harte Gates scharf zu schalten — der Helper reicht alle
    drei Schwellwerte unverändert durch.

Adaptive-Learning Wiring (Schritt 1)
------------------------------------
Wenn ``learning_settings`` mitgegeben wird UND
``learning_settings.adaptive_learning_enabled`` True ist, lädt der Helper
zusätzlich:

  - ``ActiveCalibrator``  — bayes-posterior calibration aus YAML-Snapshot
    (parameter_path = "bayes.calibrator.regime_bundle"). Kein Snapshot
    ⇒ Identity, kein Verhaltenswechsel.
  - ``ActiveThreshold``   — operator-approved min-bayes-confidence
    (parameter_path = "signal.thresholds.min_bayes_confidence").
    Kein Snapshot ⇒ default_value aus risk_settings.min_bayes_confidence.
  - ``ReasoningJournal``  — append-only structured-reasoning Journal
    für Audit-Spur der pre/post-Calibration confidence-Veränderungen.

Default ist ``adaptive_learning_enabled=False`` — ein frisches Deployment
ist immer verhalten-erhaltend. Der Operator dreht den Schalter erst nach
einer signed-off Calibration-Approval.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.settings import LearningSettings, RiskSettings
from app.market_data.regime_detection import RegimeDetectionEngine
from app.signals.bayes_journal import DEFAULT_BAYES_AUDIT_PATH
from app.signals.bayesian_confidence import (
    BayesianConfidenceEngine,
    build_default_engine,
)
from app.signals.generator import ExtraEvidencesProvider


def build_bayes_signal_kwargs(
    risk_settings: RiskSettings,
    *,
    audit_path: Path | str | None = None,
    extra_evidences_provider: ExtraEvidencesProvider | None = None,
    engine: BayesianConfidenceEngine | None = None,
    regime_engine: RegimeDetectionEngine | None = None,
    learning_settings: LearningSettings | None = None,
) -> Mapping[str, Any]:
    """Liefere die Bayes-spezifischen Kwargs für ``SignalGenerator``.

    Wenn ``risk_settings.bayes_confidence_enabled`` False ist, ist das
    Ergebnis ein leeres Mapping — ``SignalGenerator(**kwargs)`` läuft
    dann mit den Legacy-Defaults und behält das exakt vorherige Verhalten.

    Wenn der Flag an ist:
      - ``bayes_engine`` wird gesetzt (Default: ``build_default_engine()``).
      - ``bayes_audit_path`` wird gesetzt (Default: ``DEFAULT_BAYES_AUDIT_PATH``).
      - ``bayes_shadow_only`` / ``min_bayes_confidence`` /
        ``max_bayes_uncertainty`` werden 1:1 aus den Settings übernommen.
      - ``bayes_extra_evidences_provider`` wird angefügt, wenn gesetzt.

    Wenn zusätzlich ``learning_settings`` gegeben ist UND
    ``learning_settings.adaptive_learning_enabled`` True:
      - ``active_calibrator``           — geladen aus snapshot_dir
      - ``active_min_bayes_confidence`` — geladen aus snapshot_dir
      - ``reasoning_journal``           — schreibt nach reasoning_journal_path

    Default-Verhalten bleibt unverändert: ``learning_settings=None`` oder
    ``adaptive_learning_enabled=False`` ⇒ keine Learning-Loaders in kwargs.
    """
    if not risk_settings.bayes_confidence_enabled:
        return {}

    resolved_engine = engine if engine is not None else build_default_engine()
    resolved_path = Path(audit_path) if audit_path is not None else DEFAULT_BAYES_AUDIT_PATH

    kwargs: dict[str, Any] = {
        "bayes_engine": resolved_engine,
        "bayes_shadow_only": risk_settings.bayes_confidence_shadow_only,
        "min_bayes_confidence": risk_settings.min_bayes_confidence,
        "max_bayes_uncertainty": risk_settings.max_bayes_uncertainty,
        "bayes_audit_path": resolved_path,
    }
    if extra_evidences_provider is not None:
        kwargs["bayes_extra_evidences_provider"] = extra_evidences_provider
    if regime_engine is not None:
        kwargs["regime_engine"] = regime_engine

    if learning_settings is not None and learning_settings.adaptive_learning_enabled:
        # Local imports keep the legacy code path import-graph unchanged when
        # the master flag is off (default). Nothing in app.audit.* or
        # app.learning.active_* is loaded unless the operator opts in.
        from app.audit.structured_reasoning import ReasoningJournal
        from app.learning.active_calibrator import (
            DEFAULT_BAYES_CALIBRATOR_PATH,
            ActiveCalibrator,
        )
        from app.learning.active_threshold import ActiveThreshold

        snapshot_dir = learning_settings.snapshot_dir
        kwargs["active_calibrator"] = ActiveCalibrator.load(
            parameter_path=DEFAULT_BAYES_CALIBRATOR_PATH,
            snapshot_dir=snapshot_dir,
        )
        kwargs["active_min_bayes_confidence"] = ActiveThreshold.load(
            parameter_path="signal.thresholds.min_bayes_confidence",
            default_value=risk_settings.min_bayes_confidence,
            snapshot_dir=snapshot_dir,
        )
        kwargs["reasoning_journal"] = ReasoningJournal(
            path=learning_settings.reasoning_journal_path,
        )
    return kwargs


__all__ = ["build_bayes_signal_kwargs"]
