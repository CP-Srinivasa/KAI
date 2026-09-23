"""Strict parser for TypeSafe System One responses proxied by LiteLLM.

This module performs no network I/O.  Jev uses ``/typesafe/v1/systemone`` and
does not return an OpenAI chat completion, so the normal LiteLLM chat parser is
intentionally not reused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

type QuestionKind = Literal["noul", "choice", "score"]


class JevSchemaError(ValueError):
    """The response cannot be treated as decision evidence."""


@dataclass(frozen=True, slots=True)
class JevQuestion:
    kind: QuestionKind
    criteria: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind == "noul" and self.criteria:
            raise ValueError("noul questions do not use positional criteria")
        if self.kind in {"choice", "score"} and not self.criteria:
            raise ValueError(f"{self.kind} questions require criteria")
        if self.kind == "choice" and len(set(self.criteria)) != len(self.criteria):
            raise ValueError("choice criteria must be unique")


@dataclass(frozen=True, slots=True)
class JevAnswer:
    kind: QuestionKind
    probability: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class JevEvaluation:
    actual_model: str
    answers: dict[str, JevAnswer]
    input_tokens: int
    output_tokens: int


def _probability(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JevSchemaError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise JevSchemaError(f"{field} must be finite and within [0, 1]")
    return number


def _non_negative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JevSchemaError(f"{field} must be a non-negative integer")
    return value


def _probabilities(
    raw: object, *, expected_keys: tuple[str, ...], field: str
) -> tuple[tuple[str, float], ...]:
    if not isinstance(raw, dict) or set(raw) != set(expected_keys):
        raise JevSchemaError(f"{field} keys must exactly match the requested criteria")
    values = tuple((key, _probability(raw[key], f"{field}.{key}")) for key in expected_keys)
    if not math.isclose(sum(value for _, value in values), 1.0, abs_tol=0.02):
        raise JevSchemaError(f"{field} must sum approximately to 1")
    return values


def _answer(raw: object, question: JevQuestion, name: str) -> JevAnswer:
    if not isinstance(raw, dict) or raw.get("type") != question.kind:
        raise JevSchemaError(f"answers.{name}.type does not match the question")
    if question.kind == "noul":
        return JevAnswer(kind="noul", probability=_probability(raw.get("noul"), name))

    expected = question.criteria
    probability_keys = (
        tuple(str(index) for index in range(len(expected)))
        if question.kind == "score"
        else expected
    )
    probabilities = _probabilities(
        raw.get("probabilities"),
        expected_keys=probability_keys,
        field=f"answers.{name}.probabilities",
    )
    confidence = _probability(raw.get("confidence"), f"answers.{name}.confidence")
    probability_map = dict(probabilities)
    if question.kind == "choice":
        choice = raw.get("choice")
        if not isinstance(choice, str) or choice not in expected:
            raise JevSchemaError(f"answers.{name}.choice is not a requested criterion")
        if not math.isclose(confidence, probability_map[choice], abs_tol=0.02):
            raise JevSchemaError(f"answers.{name}.confidence disagrees with selected probability")
        if probability_map[choice] < max(probability_map.values()):
            raise JevSchemaError(f"answers.{name}.choice is not the highest-probability criterion")
        return JevAnswer(
            kind="choice",
            choice=choice,
            confidence=confidence,
            probabilities=probabilities,
        )

    legend = raw.get("legend")
    legend_keys = probability_keys
    if not isinstance(legend, dict) or tuple(legend) != legend_keys:
        raise JevSchemaError(f"answers.{name}.legend must preserve requested score levels")
    if tuple(legend[key] for key in legend_keys) != expected:
        raise JevSchemaError(f"answers.{name}.legend differs from requested criteria")
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise JevSchemaError(f"answers.{name}.score must be a number")
    score_value = float(score)
    if not math.isfinite(score_value) or not 0 <= score_value <= len(expected) - 1:
        raise JevSchemaError(f"answers.{name}.score is outside the requested scale")
    weighted = sum(int(key) * value for key, value in probabilities)
    if not math.isclose(score_value, weighted, abs_tol=0.05):
        raise JevSchemaError(f"answers.{name}.score disagrees with its probabilities")
    return JevAnswer(
        kind="score",
        score=score_value,
        confidence=confidence,
        probabilities=probabilities,
    )


def parse_systemone_response(
    body: object, *, questions: dict[str, JevQuestion]
) -> JevEvaluation:
    """Validate an entire response; partial answers are invalid evidence."""
    if not questions:
        raise ValueError("at least one question is required")
    if not isinstance(body, dict):
        raise JevSchemaError("response must be an object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise JevSchemaError("response model is missing")
    answers = body.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise JevSchemaError("answer keys must exactly match question keys")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise JevSchemaError("response usage is missing")
    parsed = {name: _answer(answers[name], question, name) for name, question in questions.items()}
    return JevEvaluation(
        actual_model=model.strip(),
        answers=parsed,
        input_tokens=_non_negative_int(usage.get("input_tokens"), "usage.input_tokens"),
        output_tokens=_non_negative_int(usage.get("output_tokens"), "usage.output_tokens"),
    )


__all__ = [
    "JevAnswer",
    "JevEvaluation",
    "JevQuestion",
    "JevSchemaError",
    "parse_systemone_response",
]
