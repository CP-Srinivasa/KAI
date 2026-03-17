"""Query DSL Parser and Executor.

Parses string queries like 'bitcoin AND (DeFi OR NFT) NOT scam' into an AST,
and evaluates CanonicalDocuments against that AST.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

from app.core.domain.document import CanonicalDocument

# ── AST Node Definitions ───────────────────────────────────────────────────────


class QueryNode(ABC):
    @abstractmethod
    def evaluate(self, document: CanonicalDocument) -> bool:
        """Evaluate if the document matches this node's constraints."""


@dataclass(frozen=True)
class TermNode(QueryNode):
    term: str

    def evaluate(self, document: CanonicalDocument) -> bool:
        search_text = " ".join(
            (
                document.title,
                document.cleaned_text or document.raw_text or "",
                " ".join(document.tags),
                " ".join(document.categories),
            )
        ).lower()
        return self.term.lower() in search_text

    def __str__(self) -> str:
        return f"Term({self.term})"


@dataclass(frozen=True)
class PhraseNode(QueryNode):
    phrase: str

    def evaluate(self, document: CanonicalDocument) -> bool:
        search_text = f"{document.title} {document.cleaned_text or document.raw_text or ''}".lower()
        return self.phrase.lower() in search_text

    def __str__(self) -> str:
        return f'Phrase("{self.phrase}")'


@dataclass(frozen=True)
class NotNode(QueryNode):
    child: QueryNode

    def evaluate(self, document: CanonicalDocument) -> bool:
        return not self.child.evaluate(document)

    def __str__(self) -> str:
        return f"NOT({self.child})"


@dataclass(frozen=True)
class AndNode(QueryNode):
    left: QueryNode
    right: QueryNode

    def evaluate(self, document: CanonicalDocument) -> bool:
        return self.left.evaluate(document) and self.right.evaluate(document)

    def __str__(self) -> str:
        return f"AND({self.left}, {self.right})"


@dataclass(frozen=True)
class OrNode(QueryNode):
    left: QueryNode
    right: QueryNode

    def evaluate(self, document: CanonicalDocument) -> bool:
        return self.left.evaluate(document) or self.right.evaluate(document)

    def __str__(self) -> str:
        return f"OR({self.left}, {self.right})"


# ── Lexer / Tokenizer ─────────────────────────────────────────────────────────

TOKEN_REGEX = r'(\s+|AND|OR|NOT|\(|\)|"[^"]*"?|[^\s()"]+)'


def tokenize(query: str) -> Iterator[str]:
    """Tokenize a query string, preserving quotes for phrases and parentheses."""
    for match in re.finditer(TOKEN_REGEX, query):
        token = match.group(1)
        if not token.isspace():
            yield token


# ── Parser ────────────────────────────────────────────────────────────────────


class QueryParserError(ValueError):
    """Raised when query syntax is invalid."""


class QueryParser:
    """Recursive descent parser to convert query strings to an AST.

    Precedence:
    1. Parentheses
    2. NOT
    3. AND (implicit or explicit)
    4. OR
    """

    def __init__(self, query: str):
        self.tokens = list(tokenize(query))
        self.pos = 0

    def _peek(self) -> str | None:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def _consume(self) -> str:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _match(self, expected: str) -> bool:
        if self._peek() == expected:
            self._consume()
            return True
        return False

    def parse(self) -> QueryNode:
        if not self.tokens:
            raise QueryParserError("Empty query")
        node = self._parse_or_expr()
        if self.pos < len(self.tokens):
            raise QueryParserError(f"Unexpected token at end: {self._peek()}")
        return node

    def _parse_or_expr(self) -> QueryNode:
        node = self._parse_and_expr()
        while self._match("OR"):
            if self._peek() is None:
                raise QueryParserError("Expected expression after OR")
            right = self._parse_and_expr()
            node = OrNode(node, right)
        return node

    def _parse_and_expr(self) -> QueryNode:
        node = self._parse_not_expr()

        # Parse explicit AND or implicit AND (consecutive terms)
        while True:
            peek = self._peek()
            if peek is None or peek in (")", "OR"):
                break

            if peek == "AND":
                self._consume()
                if self._peek() is None:
                    raise QueryParserError("Expected expression after AND")
                right = self._parse_not_expr()
                node = AndNode(node, right)
            else:
                # Implicit AND (e.g. `bitcoin ethereum` -> `bitcoin AND ethereum`)
                right = self._parse_not_expr()
                node = AndNode(node, right)

        return node

    def _parse_not_expr(self) -> QueryNode:
        if self._match("NOT"):
            if self._peek() is None:
                raise QueryParserError("Expected expression after NOT")
            return NotNode(self._parse_not_expr())
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        token = self._peek()
        if token is None:
            raise QueryParserError("Unexpected EOF")

        if token == "(":
            self._consume()
            node = self._parse_or_expr()
            if not self._match(")"):
                raise QueryParserError("Missing closing parenthesis")
            return node

        if token in ("AND", "OR", "NOT", ")"):
            raise QueryParserError(f"Unexpected token: {token}")

        self._consume()
        if token.startswith('"'):
            # It's a phrase
            clean_phrase = token.strip('"')
            return PhraseNode(clean_phrase)
        return TermNode(token)


# ── Execution ─────────────────────────────────────────────────────────────────


def evaluate_document(query_ast: QueryNode, document: CanonicalDocument) -> bool:
    """Execute the AST against a document's content."""
    return query_ast.evaluate(document)
