"""Evaluation module for comparing models against a baseline."""

from dataclasses import dataclass

from app.core.domain.document import CanonicalDocument


@dataclass
class EvaluationResult:
    document_count: int
    matched_sentiments: int
    matched_actionable: int
    sentiment_accuracy: float
    actionable_accuracy: float
    priority_mse: float
    relevance_mse: float
    impact_mse: float
    novelty_mse: float


def compare_outputs(
    teacher_docs: list[CanonicalDocument],
    companion_docs: list[CanonicalDocument],
) -> EvaluationResult:
    """Compare companion model analysis results against teacher outputs.

    Both lists must contain the same documents in the same order (matched by ID).
    """
    if not teacher_docs or len(teacher_docs) != len(companion_docs):
        raise ValueError("Teacher and Companion doc lists must be non-empty and equally sized.")

    count = len(teacher_docs)
    matched_sents = 0
    matched_acts = 0

    p_err_sq = 0.0
    r_err_sq = 0.0
    i_err_sq = 0.0
    n_err_sq = 0.0

    for t_doc, c_doc in zip(teacher_docs, companion_docs, strict=True):
        if t_doc.id != c_doc.id:
            raise ValueError(f"Document ID mismatch at index: {t_doc.id} != {c_doc.id}")

        t_sent = t_doc.sentiment_label
        c_sent = c_doc.sentiment_label
        if t_sent == c_sent:
            matched_sents += 1

        # Actionable isn't explicitly on doc, but priority >= 7 is the threshold (see alerts app)
        t_act = (t_doc.priority_score or 0) >= 7
        c_act = (c_doc.priority_score or 0) >= 7
        if t_act == c_act:
            matched_acts += 1

        p_err = (float(t_doc.priority_score or 1) - float(c_doc.priority_score or 1))
        p_err_sq += p_err * p_err

        r_err = (t_doc.relevance_score or 0.0) - (c_doc.relevance_score or 0.0)
        r_err_sq += r_err * r_err

        i_err = (t_doc.impact_score or 0.0) - (c_doc.impact_score or 0.0)
        i_err_sq += i_err * i_err

        n_err = (t_doc.novelty_score or 0.0) - (c_doc.novelty_score or 0.0)
        n_err_sq += n_err * n_err

    return EvaluationResult(
        document_count=count,
        matched_sentiments=matched_sents,
        matched_actionable=matched_acts,
        sentiment_accuracy=matched_sents / count,
        actionable_accuracy=matched_acts / count,
        priority_mse=p_err_sq / count,
        relevance_mse=r_err_sq / count,
        impact_mse=i_err_sq / count,
        novelty_mse=n_err_sq / count,
    )
