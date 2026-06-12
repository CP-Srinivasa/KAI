"""Evidence-Settings (S7-Extraktion + HYPE-S1) — Default-off- + Re-Export-Vertrag.

Risiko: die Extraktion aus ``app.core.settings`` bricht bestehende Importe
oder ändert env-Verhalten; die neuen Hype-Defaults weichen vom
measure-first-Vertrag ab (enabled/dampen_only).
"""

from __future__ import annotations

import pytest

from app.core import evidence_settings as extracted
from app.core import settings as settings_module
from app.core.evidence_settings import HypeEvidenceSettings


def test_reexports_are_the_same_classes() -> None:
    # `from app.core.settings import FundingEvidenceSettings` muss nach der
    # S7-Extraktion exakt dieselbe Klasse liefern (kein Duplikat-Schema).
    for name in (
        "FundingEvidenceSettings",
        "OpenInterestEvidenceSettings",
        "LongShortRatioEvidenceSettings",
        "HypeEvidenceSettings",
    ):
        assert getattr(settings_module, name) is getattr(extracted, name)


def test_hype_defaults_are_measure_first(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("APP_HYPE_EVIDENCE_"):
            monkeypatch.delenv(key, raising=False)
    s = HypeEvidenceSettings(_env_file=None)
    assert s.enabled is False  # default-off: frisches Deployment ändert NICHTS
    assert s.dampen_only is True  # S1: nur dämpfen, nie Shorts begründen
    assert s.source_trust == 0.5  # konservativ, wie alle V5-Schichten
    assert 0.0 <= s.min_score_for_evidence <= 1.0


def test_hype_env_prefix_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_HYPE_EVIDENCE_ENABLED", "true")
    monkeypatch.setenv("APP_HYPE_EVIDENCE_MIN_SCORE_FOR_EVIDENCE", "0.55")
    s = HypeEvidenceSettings(_env_file=None)
    assert s.enabled is True
    assert s.min_score_for_evidence == 0.55


def test_app_settings_carries_hype_block() -> None:
    field = settings_module.AppSettings.model_fields["hype_evidence"]
    assert field.default_factory is HypeEvidenceSettings
