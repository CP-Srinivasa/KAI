"""Offline LiteLLM shadow-evidence evaluation harness."""

from scripts.litellm_shadow_eval.engine import evaluate
from scripts.litellm_shadow_eval.models import EvaluationReport

__all__ = ["EvaluationReport", "evaluate"]
