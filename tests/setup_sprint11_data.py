import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from app.core.domain.document import CanonicalDocument
from app.core.enums import AnalysisSource, MarketScope, SentimentLabel
from app.core.settings import get_settings
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository


async def setup():
    settings = get_settings()
    session_factory = build_session_factory(settings.db)

    docs = [
        CanonicalDocument(
            id=uuid4(),
            url="https://example.com/teacher-doc-1",
            title="S&P 500 breaks record highs on tech earnings",
            raw_text="The market rallied strongly today. Tech stocks are leading the charge.",
            market_scope=MarketScope.EQUITIES,
            source_id="test-source",
            source_name="Market News",
            provider="openai",
            status="analyzed",
            is_analyzed=True,
            fetched_at=datetime.now(UTC),
            priority_score=9,
            relevance_score=0.95,
            impact_score=0.85,
            sentiment_label=SentimentLabel.BULLISH,
            analysis_source=AnalysisSource.EXTERNAL_LLM,
        ),
        CanonicalDocument(
            id=uuid4(),
            url="https://example.com/teacher-doc-2",
            title="Federal reserve holds rates steady amid inflation fears",
            raw_text=(
                "Powell indicates rates will remain higher for longer, spooking some investors."
            ),
            market_scope=MarketScope.MACRO,
            source_id="test-source",
            source_name="Market News",
            provider="openai",
            status="analyzed",
            is_analyzed=True,
            fetched_at=datetime.now(UTC),
            priority_score=6,
            relevance_score=0.88,
            impact_score=0.60,
            sentiment_label=SentimentLabel.BEARISH,
            analysis_source=AnalysisSource.EXTERNAL_LLM,
        )
    ]

    async with session_factory.begin() as session:
        # Clear specific tables or just push these
        repo = DocumentRepository(session)
        for d in docs:
            await repo.save_document(d)
        print("Created Teacher Documents (AnalysisSource.EXTERNAL_LLM)")
        return [d.id for d in docs]

async def update_to_candidate(doc_ids):
    settings = get_settings()
    session_factory = build_session_factory(settings.db)

    async with session_factory.begin() as session:
        # We will slightly perturb the scores to simulate a candidate model
        for idx, doc_id in enumerate(doc_ids):
            # First doc: exact match. Second doc: slight impact diff
            new_prom = 9 if idx == 0 else 7
            new_impact = 0.85 if idx == 0 else 0.50
            await session.execute(
                text("""
                UPDATE canonical_documents
                SET analysis_source = 'internal',
                    provider = 'companion',
                    priority_score = :p,
                    impact_score = :i
                WHERE id = :id
                """),
                {"p": new_prom, "i": new_impact, "id": doc_id}
            )
        print("Updated Documents to Candidate (AnalysisSource.INTERNAL)")

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else 'setup'
    if mode == 'setup':
        doc_ids = asyncio.run(setup())
        with open('.test_doc_ids', 'w') as f:
            for d in doc_ids:
                f.write(f"{d}\n")
    elif mode == 'candidate':
        with open('.test_doc_ids') as f:
            doc_ids = [line.strip() for line in f if line.strip()]
        asyncio.run(update_to_candidate(doc_ids))
