from fastapi import APIRouter
from pydantic import BaseModel

from app.core.domain.document import QuerySpec

router = APIRouter(prefix="/query", tags=["query"])


class ValidateResponse(BaseModel):
    valid: bool
    message: str


@router.post("/validate", response_model=ValidateResponse)
async def validate_query(spec: QuerySpec) -> ValidateResponse:
    return ValidateResponse(valid=True, message="Query spec is valid")
