"""Health check endpoint."""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "ai-analyst-trading-bot",
        "version": "0.1.0",
    }
