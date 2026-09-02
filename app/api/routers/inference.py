"""Read-only operational inference status endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.core.settings import get_settings
from app.inference.status import build_inference_status

router = APIRouter(prefix="/dashboard/api/inference", tags=["inference"])


@router.get("")
async def inference_status() -> dict[str, Any]:
    return await build_inference_status(get_settings())
