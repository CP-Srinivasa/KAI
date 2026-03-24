"""Research & Signal Generation module.

Public API for Sprint 4 downstream consumers (CLI, API, Antigravity workflows).

Layer contract:
- Input:  list[CanonicalDocument] — must have status=ANALYZED and is_analyzed=True
- Output: ResearchBrief | list[SignalCandidate] — in-memory, never written to DB
- WatchlistRegistry — loaded from monitor/watchlists.yml, used to scope input documents

No code outside this module may instantiate ResearchBrief or SignalCandidate directly.
All construction goes through ResearchBriefBuilder.build() and extract_signal_candidates().

Sprint 6 additions:
- EvaluationMetrics, EvaluationReport, compare_datasets(), load_jsonl() — offline JSONL evaluation
- export_training_data(teacher_only=True) — enforces teacher-eligibility at function level (I-27)

Sprint 7 additions:
- PromotionValidation, validate_promotion() — companion promotion gate (I-34–I-39)
- save_evaluation_report(), save_benchmark_artifact() — artifact persistence helpers

Sprint 8 additions:
- TuningArtifact, PromotionRecord — tuning manifest and promotion audit types
- save_tuning_artifact(), save_promotion_record() — write-once artifact helpers (I-40–I-45)
"""

from app.research.briefs import BriefDocument, ResearchBrief, ResearchBriefBuilder
from app.research.datasets import export_training_data
from app.research.evaluation import (
    EvaluationMetrics,
    EvaluationReport,
    EvaluationResult,
    PromotionValidation,
    compare_datasets,
    compare_outputs,
    load_jsonl,
    save_benchmark_artifact,
    save_evaluation_report,
    validate_promotion,
)
from app.research.signals import SignalCandidate, extract_signal_candidates
from app.research.tuning import (
    PromotionRecord,
    TuningArtifact,
    save_promotion_record,
    save_tuning_artifact,
)
from app.research.watchlists import WatchlistRegistry

__all__ = [
    "BriefDocument",
    "EvaluationMetrics",
    "EvaluationReport",
    "EvaluationResult",
    "PromotionRecord",
    "PromotionValidation",
    "ResearchBrief",
    "ResearchBriefBuilder",
    "SignalCandidate",
    "TuningArtifact",
    "WatchlistRegistry",
    "compare_datasets",
    "compare_outputs",
    "export_training_data",
    "extract_signal_candidates",
    "load_jsonl",
    "save_benchmark_artifact",
    "save_evaluation_report",
    "save_promotion_record",
    "save_tuning_artifact",
    "validate_promotion",
]
