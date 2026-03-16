"""Query DSL Schema — QuerySpec for all document search operations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.enums import Language, MarketScope, SourceType


class QueryMode(str, Enum):
    SIMPLE = "simple"
    BOOLEAN = "boolean"
    ADVANCED = "advanced"


class SortBy(str, Enum):
    RELEVANCE = "relevance"
    PUBLISHED_AT = "published_at"
    IMPACT_SCORE = "impact_score"
    SENTIMENT_SCORE = "sentiment_score"
    CREDIBILITY_SCORE = "credibility_score"
    ENGAGEMENT = "engagement"
    FETCHED_AT = "fetched_at"


class QuerySpec(BaseModel):
    """
    Complete query specification.
    Supports keyword, boolean, field-specific search, date/region/language filters.
    """
    query_text: str = ""
    query_mode: QueryMode = QueryMode.SIMPLE
    include_terms: list[str] = Field(default_factory=list)
    exclude_terms: list[str] = Field(default_factory=list)
    any_terms: list[str] = Field(default_factory=list)
    all_terms: list[str] = Field(default_factory=list)
    exact_phrases: list[str] = Field(default_factory=list)
    title_terms: list[str] = Field(default_factory=list)
    meta_terms: list[str] = Field(default_factory=list)
    from_date: datetime | None = None
    to_date: datetime | None = None
    countries: list[str] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    source_types: list[SourceType] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    market_scopes: list[MarketScope] = Field(default_factory=list)
    min_credibility: float = Field(default=0.0, ge=0.0, le=1.0)
    min_impact: float = Field(default=0.0, ge=0.0, le=1.0)
    min_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    min_sentiment_abs: float = Field(default=0.0, ge=0.0, le=1.0)
    min_views: int = Field(default=0, ge=0)
    min_clicks: int = Field(default=0, ge=0)
    deduplicate: bool = True
    sort_by: SortBy = SortBy.PUBLISHED_AT
    sort_descending: bool = True
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @field_validator("regions")
    @classmethod
    def max_five_regions(cls, v: list[str]) -> list[str]:
        if len(v) > 5:
            raise ValueError("Maximum 5 regions allowed per query")
        return v

    @model_validator(mode="after")
    def validate_date_range(self) -> QuerySpec:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("from_date must be before to_date")
        return self

    def is_empty(self) -> bool:
        return not any([
            self.query_text, self.include_terms, self.any_terms,
            self.all_terms, self.exact_phrases, self.title_terms, self.exclude_terms,
        ])

    def to_display(self) -> str:
        parts = []
        if self.query_text:
            parts.append(f'q="{self.query_text}"')
        if self.include_terms:
            parts.append(f"include={self.include_terms}")
        if self.exclude_terms:
            parts.append(f"exclude={self.exclude_terms}")
        if self.from_date:
            parts.append(f"from={self.from_date.date()}")
        if self.to_date:
            parts.append(f"to={self.to_date.date()}")
        if self.countries:
            parts.append(f"countries={self.countries}")
        if self.languages:
            parts.append(f"lang={[l.value for l in self.languages]}")
        return " | ".join(parts) or "(empty query)"
