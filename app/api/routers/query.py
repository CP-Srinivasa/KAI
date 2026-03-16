"""Query validation endpoint."""
from __future__ import annotations
from fastapi import APIRouter
from app.core.query.parser import QueryParser
from app.core.query.schema import QuerySpec

router = APIRouter()


@router.post("/validate")
async def validate_query(query: QuerySpec) -> dict:
    result: dict = {
        "valid": True,
        "query_display": query.to_display(),
        "is_empty": query.is_empty(),
    }
    if query.query_text:
        try:
            ast = QueryParser().parse(query.query_text)
            result["ast"] = repr(ast)
        except Exception as e:
            result["valid"] = False
            result["parse_error"] = str(e)
    return result
