"""Shared pytest fixtures and configuration."""
from __future__ import annotations
import os

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key-not-real")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/ai_analyst_bot_test")
