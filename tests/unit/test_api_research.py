from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routers import research as research_router
from app.core.domain.document import CanonicalDocument
from app.core.enums import SentimentLabel
from app.core.settings import AppSettings


class FakeDocumentRepository:
    def __init__(self, documents: list[CanonicalDocument]) -> None:
        self._documents = documents
        self.calls: list[dict[str, object]] = []

    async def list(self, **kwargs: object) -> list[CanonicalDocument]:
        self.calls.append(kwargs)
        return self._documents


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _override_research_dependencies(
    *,
    settings: AppSettings,
    repo: FakeDocumentRepository,
) -> None:
    app.dependency_overrides[research_router.get_settings] = lambda: settings
    app.dependency_overrides[research_router.get_document_repo] = lambda: repo


def test_api_research_brief_valid(client: TestClient, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
persons:
  - name: Gary Gensler
    aliases: [gensler]
    tags: [regulation]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository(
        [
            CanonicalDocument(
                url="https://example.com/gensler",
                title="Gensler warns on crypto regulation",
                is_analyzed=True,
                priority_score=9,
                impact_score=0.8,
                summary="Regulatory pressure remains elevated.",
                people=["Gary Gensler"],
                entities=["Gary Gensler"],
                sentiment_label=SentimentLabel.BEARISH,
            ),
            CanonicalDocument(
                url="https://example.com/vitalik",
                title="Vitalik discusses scaling",
                is_analyzed=True,
                priority_score=7,
                impact_score=0.6,
                summary="Ethereum roadmap update.",
                people=["Vitalik Buterin"],
                entities=["Vitalik Buterin"],
                sentiment_label=SentimentLabel.BULLISH,
            ),
        ]
    )
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get(
        "/research/brief",
        params={
            "watchlist": "regulation",
            "watchlist_type": "persons",
            "limit": 5,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["cluster_name"] == "regulation"
    assert data["title"] == "Research Brief: regulation"
    assert data["document_count"] == 1
    assert data["top_documents"][0]["title"] == "Gensler warns on crypto regulation"
    assert data["top_entities"][0]["name"] == "Gary Gensler"
    assert data["top_actionable_signals"][0]["priority_score"] == 9
    assert repo.calls == [{"is_analyzed": True, "limit": 25}]


def test_api_research_brief_returns_empty_brief_for_no_matches(
    client: TestClient,
    tmp_path,
) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
topics:
  - name: ETF
    aliases: [exchange traded fund]
    tags: [etf]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository(
        [
            CanonicalDocument(
                url="https://example.com/solana",
                title="Solana validator growth",
                is_analyzed=True,
                priority_score=6,
                summary="Validator count rises.",
                topics=["layer1"],
                sentiment_label=SentimentLabel.BULLISH,
            )
        ]
    )
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get(
        "/research/brief",
        params={
            "watchlist": "etf",
            "watchlist_type": "topics",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["document_count"] == 0
    assert data["top_documents"] == []
    assert data["summary"] == "No analyzed documents available for this brief."


def test_api_research_brief_empty_watchlist(client: TestClient, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
crypto:
  - symbol: BTC
    name: Bitcoin
    aliases: [bitcoin]
    tags: [majors]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository([])
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get(
        "/research/brief",
        params={
            "watchlist": "unknown",
            "watchlist_type": "assets",
        },
    )

    assert response.status_code == 404
    assert "does not exist" in response.json()["detail"]


def test_api_research_brief_invalid_type(client: TestClient, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
crypto:
  - symbol: BTC
    name: Bitcoin
    aliases: [bitcoin]
    tags: [majors]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository([])
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get(
        "/research/brief",
        params={
            "watchlist": "majors",
            "watchlist_type": "invalid_type",
        },
    )

    assert response.status_code == 400
    assert "Unsupported watchlist type" in response.json()["detail"]


def test_api_research_signals_returns_candidates(client: TestClient, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
crypto:
  - symbol: BTC
    name: Bitcoin
    aliases: [bitcoin]
    tags: [majors]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository(
        [
            CanonicalDocument(
                url="https://example.com/btc-rally",
                title="Bitcoin rally continues",
                is_analyzed=True,
                priority_score=9,
                relevance_score=0.9,
                summary="Strong upward momentum.",
                tickers=["BTC"],
                crypto_assets=["BTC"],
                sentiment_label=SentimentLabel.BULLISH,
            ),
            CanonicalDocument(
                url="https://example.com/low-pri",
                title="Minor update",
                is_analyzed=True,
                priority_score=5,
                sentiment_label=SentimentLabel.NEUTRAL,
            ),
        ]
    )
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get("/research/signals", params={"min_priority": 8})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["target_asset"] == "BTC"
    assert data[0]["direction_hint"] == "bullish"
    assert data[0]["priority"] == 9
    assert "document_id" in data[0]


def test_api_research_signals_with_watchlist_boost(client: TestClient, tmp_path) -> None:
    watchlists_path = tmp_path / "watchlists.yml"
    watchlists_path.write_text(
        """
crypto:
  - symbol: ETH
    name: Ethereum
    aliases: [ethereum]
    tags: [layer1]
""".strip(),
        encoding="utf-8",
    )

    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository(
        [
            CanonicalDocument(
                url="https://example.com/eth-update",
                title="Ethereum network update",
                is_analyzed=True,
                priority_score=7,  # below default min_priority=8
                relevance_score=0.7,
                tickers=["ETH"],
                crypto_assets=["ETH"],
                sentiment_label=SentimentLabel.BULLISH,
            ),
        ]
    )
    _override_research_dependencies(settings=settings, repo=repo)

    # Without boost — below threshold
    response = client.get("/research/signals", params={"min_priority": 8})
    assert response.status_code == 200
    assert response.json() == []

    # With watchlist boost (+2) — clears threshold
    response = client.get(
        "/research/signals",
        params={"watchlist": "layer1", "min_priority": 8},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["target_asset"] == "ETH"
    assert data[0]["priority"] == 9


def test_api_research_signals_invalid_watchlist_type(client: TestClient, tmp_path) -> None:
    settings = AppSettings()
    settings.monitor_dir = str(tmp_path)
    repo = FakeDocumentRepository([])
    _override_research_dependencies(settings=settings, repo=repo)

    response = client.get(
        "/research/signals",
        params={"watchlist": "defi", "watchlist_type": "invalid"},
    )
    assert response.status_code == 400
    assert "Unsupported watchlist type" in response.json()["detail"]
