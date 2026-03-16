"""Tests for the Query DSL Parser."""

from __future__ import annotations

import pytest

from app.core.errors import QueryParseError
from app.core.query.parser import NodeType, QueryParser


@pytest.fixture
def parser() -> QueryParser:
    return QueryParser()


class TestSimpleTerms:
    def test_single_keyword(self, parser: QueryParser) -> None:
        ast = parser.parse("apple")
        assert ast.node_type == NodeType.TERM
        assert ast.value == "apple"
        assert not ast.negated

    def test_exact_phrase(self, parser: QueryParser) -> None:
        ast = parser.parse('"Tim Cook"')
        assert ast.node_type == NodeType.PHRASE
        assert ast.value == "Tim Cook"

    def test_exclude_term(self, parser: QueryParser) -> None:
        ast = parser.parse("cryptocurrency -bitcoin")
        assert ast.node_type == NodeType.AND
        assert ast.children[0].value == "cryptocurrency"
        assert ast.children[1].value == "bitcoin"
        assert ast.children[1].negated

    def test_not_keyword(self, parser: QueryParser) -> None:
        ast = parser.parse("NOT ethereum")
        assert ast.node_type == NodeType.TERM
        assert ast.negated


class TestBooleanOperators:
    def test_and_operator(self, parser: QueryParser) -> None:
        ast = parser.parse("blockchain AND crypto")
        assert ast.node_type == NodeType.AND
        assert ast.children[0].value == "blockchain"
        assert ast.children[1].value == "crypto"

    def test_or_operator(self, parser: QueryParser) -> None:
        ast = parser.parse("ethereum OR bitcoin OR tether")
        assert ast.node_type == NodeType.OR

    def test_complex_boolean(self, parser: QueryParser) -> None:
        ast = parser.parse("(gamestop AND wallstreetbets) NOT wallstreet")
        assert ast is not None

    def test_not_group(self, parser: QueryParser) -> None:
        ast = parser.parse("cryptocurrency NOT (ethereum OR bitcoin OR tether)")
        assert ast is not None


class TestFieldSearch:
    def test_intitle_phrase(self, parser: QueryParser) -> None:
        ast = parser.parse('qintitle:"nft marketplace"')
        assert ast.node_type == NodeType.FIELD
        assert ast.field == "qintitle"
        assert ast.value == "nft marketplace"

    def test_intitle_term(self, parser: QueryParser) -> None:
        ast = parser.parse("intitle:bitcoin")
        assert ast.node_type == NodeType.FIELD
        assert ast.field == "intitle"
        assert ast.value == "bitcoin"


class TestEdgeCases:
    def test_empty_query(self, parser: QueryParser) -> None:
        ast = parser.parse("")
        assert ast.node_type == NodeType.GROUP
        assert ast.children == []

    def test_whitespace_only(self, parser: QueryParser) -> None:
        ast = parser.parse("   ")
        assert ast.node_type == NodeType.GROUP

    def test_nested_parens(self, parser: QueryParser) -> None:
        ast = parser.parse("((bitcoin))")
        assert ast is not None

    def test_mismatched_parens_raises(self, parser: QueryParser) -> None:
        with pytest.raises(QueryParseError):
            parser.parse("(bitcoin AND eth")
