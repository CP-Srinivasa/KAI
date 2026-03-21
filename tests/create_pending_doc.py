import asyncio

from app.core.domain.document import CanonicalDocument
from app.core.enums import MarketScope
from app.core.settings import get_settings
from app.storage.db.session import build_session_factory
from app.storage.repositories.document_repo import DocumentRepository


async def main():
    settings = get_settings()
    session_factory = build_session_factory(settings.db)

    async with session_factory.begin() as session:
        repo = DocumentRepository(session)
        doc = CanonicalDocument(
            url="https://example.com/test-shadow-run",
            title="Bitcoin rally creates market volatility",
            raw_text=(
                "The latest BTC price action is causing massive trading volume. "
                "Institutional investors are adopting bitcoin rapidly."
            ),
            market_scope=MarketScope.CRYPTO,
            source_id="test-source",
            source_name="Manual",
            provider="ingest",
        )
        await repo.save_document(doc)
        print(f"Created pending document: {doc.id}")


if __name__ == "__main__":
    asyncio.run(main())
