"""Anthropic analysis provider.

Implements BaseAnalysisProvider using the Anthropic API with tool calling
to enforce structured JSON output matching the LLMAnalysisOutput schema.

Provider name: "anthropic"
Default model: claude-3-7-sonnet-20250219 (configurable)
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from app.analysis.base.interfaces import BaseAnalysisProvider, LLMAnalysisOutput
from app.analysis.prompts import SYSTEM_PROMPT_V1, format_user_prompt

_MAX_TEXT_CHARS = 6000  # ~1500 tokens


class AnthropicAnalysisProvider(BaseAnalysisProvider):
    """Analyze documents using Anthropic Claude with tool calling.

    Args:
        api_key:    Anthropic API key (required).
        model:      Model name, default "claude-3-7-sonnet-20250219".
        timeout:    HTTP timeout in seconds.
        max_tokens: Max response tokens (default 1024).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-7-sonnet-20250219",
        timeout: int = 30,
        max_tokens: int = 1024,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str | None:
        return self._model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=15),
        reraise=True,
    )
    async def analyze(
        self,
        title: str,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> LLMAnalysisOutput:
        """Call Anthropic and return validated LLMAnalysisOutput.

        Uses tool calling to force the model to output data matching the
        LLMAnalysisOutput Pydantic schema. Retries on transient errors.

        Raises:
            anthropic.APIError: on non-retryable API errors.
            pydantic.ValidationError: if model output fails schema validation.
            ValueError: if model refuses to use the tool.
        """
        user_prompt = format_user_prompt(
            title=title,
            text=text[:_MAX_TEXT_CHARS],
            context=context,
        )

        # Generate the JSON Schema from our Pydantic model
        schema = LLMAnalysisOutput.model_json_schema()
        # Remove definition references that Anthropic might bulk at, keeping it simple
        if "$defs" in schema:
            # We assume it is mostly flat for Anthropic.
            pass # Keep it for now, Anthropic supports standard JSON schema

        tools = [
            {
                "name": "record_analysis",
                "description": "Record the structured financial market analysis.",
                "input_schema": schema,
            }
        ]

        # Provide a hint to use the tool
        user_prompt += "\n\nPlease output your analysis by calling the 'record_analysis' tool."

        response = await self._client.messages.create(
            model=self._model,
            system=SYSTEM_PROMPT_V1,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": user_prompt}],
            tools=tools,
            tool_choice={"type": "tool", "name": "record_analysis"},
        )

        for block in response.content:
            if block.type == "tool_use" and block.name == "record_analysis":
                # Validate using JSON mode so Pydantic coerces strings to Enums correctly
                return LLMAnalysisOutput.model_validate_json(json.dumps(block.input))

        raise ValueError(
            "Anthropic returned successful response but did not call record_analysis tool."
        )

    @classmethod
    def from_settings(cls, settings: Any) -> AnthropicAnalysisProvider:
        """Construct from ProviderSettings (app.core.settings.ProviderSettings)."""
        return cls(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            timeout=settings.anthropic_timeout,
        )
