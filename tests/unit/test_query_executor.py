"""Tests for the Query Executor."""

from __future__ import annotations

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.enums import SourceType
from app.core.query.executor import QueryExecutor
from app.core.query.parser import QueryParser


def make_doc(title: str, text: str = "", url: str = "https://example.com/test") -> CanonicalDocument:
    return CanonicalDocument(
        source_id="test", source_name="Test", source_type=SourceType.RSS_FEED,
        url=url, title=title, cleaned_text=text,
    )


@pytest.fixture
def parser() -> QueryParser:
    return QueryParser()


class TestQueryExecutor:
    def test_simple_match(self, parser: QueryParser) -> None:
        doc = make_doc("Bitcoin hits new ATH")
        assert QueryExecutor(parser.parse("bitcoin")).matches(doc)

    def test_simple_no_match(self, parser: QueryParser) -> None:
        doc = make_doc("Bitcoin hits new ATH")
        assert not QueryExecutor(parser.parse("ethereum")).matches(doc)

    def test_exclude_term(self, parser: QueryParser) -> None:
        ast = parser.parse("cryptocurrency -bitcoin")
        executor = QueryExecutor(ast)
        assert executor.matches(make_doc("Ethereum cryptocurrency news"))
        assert not executor.matches(make_doc("Bitcoin cryptocurrency news"))

    def test_exact_phrase(self, parser: QueryParser) -> None:
        ast = parser.parse('"smart contract"')
        executor = QueryExecutor(ast)
        assert executor.matches(make_doc("Smart contract vulnerability found"))
        assert not executor.matches(make_doc("Smart code contract issues"))

    def test_and_operator(self, parser: QueryParser) -> None:
        ast = parser.parse("bitcoin AND defi")
        executor = QueryExecutor(ast)
        assert executor.matches(make_doc("Bitcoin DeFi integration", "bitcoin defi protocol"))
        assert not executor.matches(make_doc("Bitcoin price update"))

    def test_or_operator(self, parser: QueryParser) -> None:
        ast = parser.parse("ethereum OR bitcoin")
        executor = QueryExecutor(ast)
        assert executor.matches(make_doc("Ethereum upgrade"))
        assert executor.matches(make_doc("Bitcoin halving"))
        assert not executor.matches(make_doc("Dogecoin news"))

    def test_title_field_search(self, parser: QueryParser) -> None:
        ast = parser.parse('qintitle:"nft marketplace"')
        executor = QueryExecutor(ast)
        assert executor.matches(make_doc("NFT Marketplace Launches Today"))
        assert not executor.matches(make_doc("General news", "nft marketplace gains"))

    def test_filter_batch(self, parser: QueryParser) -> None:
        executor = QueryExecutor(parser.parse("bitcoin"))
        docs = [
            make_doc("Bitcoin update", url="https://ex.com/1"),
            make_doc("Ethereum news", url="https://ex.com/2"),
            make_doc("Bitcoin mining", url="https://ex.com/3"),
        ]
        assert len(executor.filter(docs)) == 2
