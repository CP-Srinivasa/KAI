"""
Query Executor
==============
Evaluates a QueryNode AST against a CanonicalDocument in memory.
For DB queries, translate to SQL WHERE clauses separately.
"""

from __future__ import annotations

from app.core.domain.document import CanonicalDocument
from app.core.query.parser import NodeType, QueryNode


def _get_field_text(document: CanonicalDocument, field: str) -> str:
    return {
        "qintitle": document.title,
        "intitle": document.title,
        "inmeta": " ".join(f"{k} {v}" for k, v in document.metadata.items()),
        "inurl": document.url,
        "site": document.url,
    }.get(field, "")


def evaluate_node(node: QueryNode, document: CanonicalDocument) -> bool:
    full_text = f"{document.title} {document.cleaned_text or document.raw_text} {document.summary}"

    if node.node_type == NodeType.TERM:
        result = node.value.lower() in full_text.lower()
        return not result if node.negated else result

    if node.node_type == NodeType.PHRASE:
        result = node.value.lower() in full_text.lower()
        return not result if node.negated else result

    if node.node_type == NodeType.FIELD:
        field_text = _get_field_text(document, node.field)
        result = node.value.lower() in field_text.lower()
        return not result if node.negated else result

    if node.node_type == NodeType.NOT:
        return not evaluate_node(node.children[0], document)

    if node.node_type == NodeType.AND:
        return all(evaluate_node(c, document) for c in node.children)

    if node.node_type == NodeType.OR:
        return any(evaluate_node(c, document) for c in node.children)

    if node.node_type == NodeType.GROUP:
        return evaluate_node(node.children[0], document) if node.children else True

    return True


class QueryExecutor:
    def __init__(self, ast: QueryNode) -> None:
        self.ast = ast

    def matches(self, document: CanonicalDocument) -> bool:
        return evaluate_node(self.ast, document)

    def filter(self, documents: list[CanonicalDocument]) -> list[CanonicalDocument]:
        return [doc for doc in documents if self.matches(doc)]
