"""DocumentRepository.save_llm_audit against a real SQLite schema (NEO-P-004).

Before this file the llm_audit table had no writer test and no reader at all —
the column contents were unverified end to end.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

# Registers the llm_audit table on Base.metadata before create_all runs.
import app.storage.models.audit  # noqa: F401
from app.core.domain.document import CanonicalDocument
from app.core.enums import DocumentStatus, SourceType
from app.storage.models.audit import LLMAuditRecord
from app.storage.repositories.document_repo import DocumentRepository


async def test_save_llm_audit_persists_all_columns(session_factory: async_sessionmaker) -> None:
    doc = CanonicalDocument(
        url="https://example.com/llm-audit-001",
        title="Bitcoin ETF approved",
        raw_text="Bitcoin ETF approved by the SEC.",
        source_type=SourceType.RSS_FEED,
        source_id="audit-src",
        content_hash="hash-llm-audit-001",
        status=DocumentStatus.PERSISTED,
    )
    async with session_factory.begin() as session:
        doc_id = await DocumentRepository(session).save_document(doc)

    async with session_factory.begin() as session:
        await DocumentRepository(session).save_llm_audit(
            document_id=str(doc_id),
            provider="gemini",
            model="gemini-2.5-flash",
            prompt_text="",
            raw_response='{"sentiment_label":"bullish"}',
            prompt_tokens=311,
            completion_tokens=88,
        )

    async with session_factory.begin() as session:
        rows = (await session.execute(select(LLMAuditRecord))).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.document_id == str(doc_id)
    assert row.provider == "gemini"
    # The defect this guards: the column used to be the literal "unknown".
    assert row.model == "gemini-2.5-flash"
    assert row.prompt_text == ""
    assert row.prompt_tokens == 311
    assert row.completion_tokens == 88
    assert row.total_tokens == 399
    assert row.created_at is not None
