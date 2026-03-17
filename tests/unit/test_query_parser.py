from uuid import uuid4

import pytest

from app.core.domain.document import CanonicalDocument
from app.core.query import (
    AndNode,
    NotNode,
    OrNode,
    PhraseNode,
    QueryParser,
    QueryParserError,
    TermNode,
    evaluate_document,
)


@pytest.fixture
def doc_crypto() -> CanonicalDocument:
    return CanonicalDocument(
        id=uuid4(),
        url="https://example.com/crypto",
        title="Bitcoin hits new high",
        cleaned_text="The crypto market is booming as bitcoin surges past the previous resistance.",
        tags=["finance", "crypto"],
        categories=["news"],
    )


@pytest.fixture
def doc_nft() -> CanonicalDocument:
    return CanonicalDocument(
        id=uuid4(),
        url="https://example.com/nft",
        title="Are NFTs dead?",
        cleaned_text="The NFT market has cooled down significantly, many think it is a scam.",
        tags=["crypto", "art"],
        categories=["opinion"],
    )


def test_parse_single_term():
    ast = QueryParser("bitcoin").parse()
    assert isinstance(ast, TermNode)
    assert ast.term == "bitcoin"


def test_parse_phrase():
    ast = QueryParser('"crypto market"').parse()
    assert isinstance(ast, PhraseNode)
    assert ast.phrase == "crypto market"


def test_parse_implicit_and():
    ast = QueryParser("bitcoin ethereum").parse()
    assert isinstance(ast, AndNode)
    assert isinstance(ast.left, TermNode)
    assert isinstance(ast.right, TermNode)


def test_parse_explicit_and():
    ast = QueryParser("bitcoin AND ethereum").parse()
    assert isinstance(ast, AndNode)
    assert isinstance(ast.left, TermNode)
    assert isinstance(ast.right, TermNode)


def test_parse_or():
    ast = QueryParser("bitcoin OR ethereum").parse()
    assert isinstance(ast, OrNode)
    assert isinstance(ast.left, TermNode)
    assert isinstance(ast.right, TermNode)


def test_parse_not():
    ast = QueryParser("NOT scam").parse()
    assert isinstance(ast, NotNode)
    assert isinstance(ast.child, TermNode)


def test_parse_complex_query():
    ast = QueryParser("bitcoin AND (DeFi OR NFT) NOT scam").parse()
    # Should be parsed as: AND( AND(Term(bitcoin), OR(Term(DeFi), Term(NFT))), NOT(Term(scam)) )
    assert isinstance(ast, AndNode)

    # Left side of the top AND
    assert isinstance(ast.left, AndNode)
    # Left of that
    assert isinstance(ast.left.left, TermNode)
    assert ast.left.left.term == "bitcoin"
    # Right of that
    assert isinstance(ast.left.right, OrNode)

    # Right side of the top AND
    assert isinstance(ast.right, NotNode)
    assert isinstance(ast.right.child, TermNode)
    assert ast.right.child.term == "scam"


def test_parse_errors():
    with pytest.raises(QueryParserError):
        QueryParser("").parse()

    with pytest.raises(QueryParserError):
        QueryParser("AND").parse()

    with pytest.raises(QueryParserError):
        QueryParser("(bitcoin OR)").parse()


def test_evaluate_term(doc_crypto, doc_nft):
    ast_btc = QueryParser("bitcoin").parse()
    assert evaluate_document(ast_btc, doc_crypto) is True
    assert evaluate_document(ast_btc, doc_nft) is False


def test_evaluate_phrase(doc_crypto):
    ast_phrase = QueryParser('"crypto market"').parse()
    assert evaluate_document(ast_phrase, doc_crypto) is True

    ast_wrong = QueryParser('"booming crypto"').parse()
    assert evaluate_document(ast_wrong, doc_crypto) is False


def test_evaluate_and(doc_crypto):
    ast = QueryParser("bitcoin AND booming").parse()
    assert evaluate_document(ast, doc_crypto) is True

    ast_false = QueryParser("bitcoin AND scam").parse()
    assert evaluate_document(ast_false, doc_crypto) is False


def test_evaluate_or(doc_crypto, doc_nft):
    ast = QueryParser("boom OR dead").parse()
    assert evaluate_document(ast, doc_crypto) is True
    assert evaluate_document(ast, doc_nft) is True

    ast_false = QueryParser("apple OR orange").parse()
    assert evaluate_document(ast_false, doc_crypto) is False


def test_evaluate_not(doc_nft):
    ast = QueryParser("NOT scam").parse()
    # doc_nft HAS scam
    assert evaluate_document(ast, doc_nft) is False


def test_evaluate_complex(doc_crypto, doc_nft):
    query = "crypto AND (bitcoin OR NFT) NOT scam"
    ast = QueryParser(query).parse()

    # doc_crypto has crypto, bitcoin, but NOT scam -> True
    assert evaluate_document(ast, doc_crypto) is True

    # doc_nft has crypto, NFT, but HAS scam -> False
    assert evaluate_document(ast, doc_nft) is False
