"""Tests for QuerySpec schema."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.core.query.schema import QuerySpec


class TestQuerySpec:
    def test_empty_query(self) -> None:
        assert QuerySpec().is_empty()

    def test_with_query_text(self) -> None:
        q = QuerySpec(query_text="bitcoin")
        assert not q.is_empty()
        assert "bitcoin" in q.to_display()

    def test_max_five_regions(self) -> None:
        assert len(QuerySpec(regions=["r1", "r2", "r3", "r4", "r5"]).regions) == 5

    def test_more_than_five_regions_raises(self) -> None:
        with pytest.raises(ValidationError):
            QuerySpec(regions=["r1", "r2", "r3", "r4", "r5", "r6"])

    def test_invalid_date_range(self) -> None:
        with pytest.raises(ValidationError):
            QuerySpec(from_date=datetime(2024, 6, 1), to_date=datetime(2024, 1, 1))

    def test_score_bounds(self) -> None:
        with pytest.raises(ValidationError):
            QuerySpec(min_credibility=1.5)

    def test_pagination_bounds(self) -> None:
        with pytest.raises(ValidationError):
            QuerySpec(limit=0)
        with pytest.raises(ValidationError):
            QuerySpec(limit=501)

    def test_to_display_empty(self) -> None:
        assert "(empty query)" in QuerySpec().to_display()
