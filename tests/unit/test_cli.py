from typer.testing import CliRunner

from app.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "trading-bot" in result.output.lower() or "Usage" in result.output


def test_sources_classify_rss() -> None:
    result = runner.invoke(app, ["sources", "classify", "https://cointelegraph.com/rss"])
    assert result.exit_code == 0
    assert "rss_feed" in result.output


def test_sources_classify_youtube() -> None:
    result = runner.invoke(app, ["sources", "classify", "https://www.youtube.com/@Bankless"])
    assert result.exit_code == 0
    assert "youtube_channel" in result.output


def test_sources_classify_apple_podcast() -> None:
    result = runner.invoke(
        app,
        ["sources", "classify", "https://podcasts.apple.com/de/podcast/x/id123"],
    )
    assert result.exit_code == 0
    assert "requires_api" in result.output


def test_podcasts_resolve() -> None:
    result = runner.invoke(app, ["podcasts", "resolve"])
    assert result.exit_code == 0
    assert "Resolved" in result.output
    assert "Unresolved" in result.output


def test_youtube_resolve() -> None:
    result = runner.invoke(app, ["youtube", "resolve"])
    assert result.exit_code == 0
    assert "channels" in result.output.lower() or "handle" in result.output.lower()


def test_query_validate() -> None:
    result = runner.invoke(app, ["query", "validate", "bitcoin AND ethereum"])
    assert result.exit_code == 0
    assert "bitcoin AND ethereum" in result.output
