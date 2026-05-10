"""ORM models for cognitive audit trails (LLM decisions).

P0.2: Cognitive Audit Trail — Persisting prompts, raw responses, and token usage
for every LLM decision to make "Why did KAI buy this?" traceable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.db.session import Base


class LLMAuditRecord(Base):
    """Append-only DB record for an LLM cognitive decision.

    Links back to the decision_id used in TradingCycleRecord and PaperFill.
    Stores the exact prompt given, the raw response received, and token stats.
    """

    __tablename__ = "llm_audit"
    __table_args__ = (
        Index("ix_llm_audit_document_created", "document_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # The document ID that was analyzed, linking the cognitive trail to the source
    document_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Provider and model used (e.g., 'openai', 'gpt-4o')
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    # The cognitive trail
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_response: Mapped[str] = mapped_column(Text, nullable=False)

    # Token usage for cost attribution and debugging
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
