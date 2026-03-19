"""Backward-compatibility re-export. Prompts live in app/analysis/prompts.py."""

from app.analysis.prompts import SYSTEM_PROMPT_V1, USER_PROMPT_V1, format_user_prompt

__all__ = ["SYSTEM_PROMPT_V1", "USER_PROMPT_V1", "format_user_prompt"]
