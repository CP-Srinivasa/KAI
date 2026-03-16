"""
Query DSL Parser
================
Parses query strings into AST nodes.

Supported syntax:
    apple                        → simple keyword
    "Tim Cook"                   → exact phrase
    cryptocurrency -bitcoin      → exclude term
    crypto AND (bitcoin OR eth)  → boolean groups
    NOT ethereum                 → negation
    qintitle:"nft marketplace"   → field-specific search
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as _dc_field
from enum import Enum

from app.core.errors import QueryParseError


class NodeType(str, Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    TERM = "TERM"
    PHRASE = "PHRASE"
    FIELD = "FIELD"
    GROUP = "GROUP"


@dataclass
class QueryNode:
    node_type: NodeType
    value: str = ""
    field: str = ""
    children: list[QueryNode] = _dc_field(default_factory=list)
    negated: bool = False

    def __repr__(self) -> str:
        if self.node_type in (NodeType.TERM, NodeType.PHRASE):
            neg = "NOT " if self.negated else ""
            fp = f"{self.field}:" if self.field else ""
            return f"{neg}{fp}{self.node_type.value}({self.value!r})"
        return f"{self.node_type.value}({', '.join(repr(c) for c in self.children)})"


class QueryParser:
    """
    Recursive-descent parser for the query DSL.

    Usage:
        ast = QueryParser().parse('bitcoin AND (DeFi OR "smart contract") NOT scam')
    """

    _TOKEN_PATTERNS = [
        ("FIELD",   r'(?:qintitle|intitle|inmeta|inurl|site):'),
        ("PHRASE",  r'"[^"]*"'),
        ("LPAREN",  r'\('),
        ("RPAREN",  r'\)'),
        ("AND",     r'\bAND\b'),
        ("OR",      r'\bOR\b'),
        ("NOT",     r'\bNOT\b'),
        ("EXCLUDE", r'-(?=\S)'),
        ("TERM",    r'[^\s()"\-]+'),
        ("WS",      r'\s+'),
    ]

    def __init__(self) -> None:
        self._re = re.compile(
            "|".join(f"(?P<{n}>{p})" for n, p in self._TOKEN_PATTERNS)
        )

    def _tokenize(self, query: str) -> list[tuple[str, str]]:
        return [
            (m.lastgroup or "", m.group())
            for m in self._re.finditer(query)
            if m.lastgroup != "WS"
        ]

    def parse(self, query: str) -> QueryNode:
        if not query.strip():
            return QueryNode(node_type=NodeType.GROUP)
        self._tokens = self._tokenize(query)
        self._pos = 0
        if not self._tokens:
            return QueryNode(node_type=NodeType.GROUP)
        try:
            ast = self._parse_or()
        except IndexError as e:
            raise QueryParseError(f"Unexpected end of query: {query}", query=query) from e
        if self._pos < len(self._tokens):
            remaining = " ".join(v for _, v in self._tokens[self._pos:])
            raise QueryParseError(f"Unexpected token: {remaining!r}", query=query)
        return ast

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _consume(self, expected: str | None = None) -> tuple[str, str]:
        token = self._tokens[self._pos]
        if expected and token[0] != expected:
            raise QueryParseError(f"Expected {expected}, got {token[0]}")
        self._pos += 1
        return token

    def _parse_or(self) -> QueryNode:
        left = self._parse_and()
        while self._peek() and self._peek()[0] == "OR":
            self._consume("OR")
            right = self._parse_and()
            left = QueryNode(node_type=NodeType.OR, children=[left, right])
        return left

    def _parse_and(self) -> QueryNode:
        left = self._parse_unary()
        while self._peek() and self._peek()[0] not in ("OR", "RPAREN"):
            if self._peek()[0] == "AND":
                self._consume("AND")
            right = self._parse_unary()
            left = QueryNode(node_type=NodeType.AND, children=[left, right])
        return left

    def _parse_unary(self) -> QueryNode:
        token = self._peek()
        if token is None:
            raise QueryParseError("Unexpected end of query")
        if token[0] == "NOT":
            self._consume("NOT")
            child = self._parse_primary()
            child.negated = True
            return child
        if token[0] == "EXCLUDE":
            self._consume("EXCLUDE")
            child = self._parse_primary()
            child.negated = True
            return child
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        token = self._peek()
        if token is None:
            raise QueryParseError("Unexpected end of query")
        if token[0] == "LPAREN":
            self._consume("LPAREN")
            node = self._parse_or()
            self._consume("RPAREN")
            return QueryNode(node_type=NodeType.GROUP, children=[node])
        if token[0] == "FIELD":
            field_name = self._consume("FIELD")[1].rstrip(":")
            val = self._peek()
            if val and val[0] == "PHRASE":
                value = self._consume("PHRASE")[1].strip('"')
                return QueryNode(node_type=NodeType.FIELD, field=field_name, value=value)
            elif val and val[0] == "TERM":
                value = self._consume("TERM")[1]
                return QueryNode(node_type=NodeType.FIELD, field=field_name, value=value)
            raise QueryParseError(f"Expected value after field '{field_name}:'")
        if token[0] == "PHRASE":
            return QueryNode(node_type=NodeType.PHRASE, value=self._consume("PHRASE")[1].strip('"'))
        if token[0] == "TERM":
            return QueryNode(node_type=NodeType.TERM, value=self._consume("TERM")[1])
        raise QueryParseError(f"Unexpected token: {token[1]!r}")
