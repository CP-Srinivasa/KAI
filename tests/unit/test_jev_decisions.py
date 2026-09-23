from __future__ import annotations

import pytest

from app.ai.decisions import DecisionDisposition, ProbabilityPolicy
from app.integrations.litellm.jev import (
    JevQuestion,
    JevSchemaError,
    parse_systemone_response,
)


def test_probability_policy_has_explicit_review_band() -> None:
    policy = ProbabilityPolicy(negative_below=0.2, positive_at=0.8)

    assert policy.classify(0.2) is DecisionDisposition.NEGATIVE
    assert policy.classify(0.5) is DecisionDisposition.REVIEW
    assert policy.classify(0.8) is DecisionDisposition.POSITIVE


@pytest.mark.parametrize(
    ("negative", "positive"),
    [(-0.1, 0.8), (0.8, 0.8), (0.9, 0.8), (0.2, 1.1), (float("nan"), 0.8)],
)
def test_probability_policy_rejects_invalid_thresholds(negative: float, positive: float) -> None:
    with pytest.raises(ValueError):
        ProbabilityPolicy(negative_below=negative, positive_at=positive)


def test_systemone_parser_validates_all_documented_answer_types() -> None:
    response = {
        "model": "typesafe/jev-1.13.0",
        "answers": {
            "relevant": {"type": "noul", "noul": 0.91},
            "route": {
                "type": "choice",
                "choice": "analysis",
                "confidence": 0.8,
                "probabilities": {"analysis": 0.8, "discard": 0.2},
            },
            "urgency": {
                "type": "score",
                "score": 1.7,
                "confidence": 0.9,
                "legend": {"0": "later", "1": "soon", "2": "now"},
                "probabilities": {"0": 0.1, "1": 0.1, "2": 0.8},
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }

    parsed = parse_systemone_response(
        response,
        questions={
            "relevant": JevQuestion("noul"),
            "route": JevQuestion("choice", ("analysis", "discard")),
            "urgency": JevQuestion("score", ("later", "soon", "now")),
        },
    )

    assert parsed.actual_model == "typesafe/jev-1.13.0"
    assert parsed.answers["relevant"].probability == 0.91
    assert parsed.answers["route"].choice == "analysis"
    assert parsed.answers["urgency"].score == 1.7
    assert parsed.input_tokens == 120


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["answers"].pop("relevant"),
        lambda body: body["answers"]["relevant"].update({"noul": 1.2}),
        lambda body: body["answers"]["relevant"].update({"type": "choice"}),
        lambda body: body["usage"].update({"input_tokens": -1}),
    ],
)
def test_systemone_parser_fails_closed_on_incomplete_or_invalid_evidence(mutation) -> None:
    response = {
        "model": "typesafe/jev-1.13.0",
        "answers": {"relevant": {"type": "noul", "noul": 0.91}},
        "usage": {"input_tokens": 12, "output_tokens": 1},
    }
    mutation(response)

    with pytest.raises(JevSchemaError):
        parse_systemone_response(response, questions={"relevant": JevQuestion("noul")})


def test_choice_must_be_the_highest_probability_criterion() -> None:
    response = {
        "model": "typesafe/jev-1.13.0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "analysis",
                "confidence": 0.4,
                "probabilities": {"analysis": 0.4, "discard": 0.6},
            }
        },
        "usage": {"input_tokens": 12, "output_tokens": 1},
    }

    with pytest.raises(JevSchemaError, match="highest-probability"):
        parse_systemone_response(
            response, questions={"route": JevQuestion("choice", ("analysis", "discard"))}
        )
