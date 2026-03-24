"""Tests for Phase 4 alert dispatch in the analyze-pending CLI command."""

from __future__ import annotations

from typer.testing import CliRunner

from app.cli.main import app
from app.core.domain.document import CanonicalDocument

runner = CliRunner()


def _make_session_factory():
    class _FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _FakeSessionFactory:
        def begin(self):
            return _FakeSessionContext()

    return _FakeSessionFactory()


def _patch_analysis_infra(monkeypatch, docs, *, priority_score: int = 9):
    """Patch DB + pipeline so analyze-pending succeeds with `docs` as pending."""
    from app.analysis.keywords import engine as kw_engine
    from app.analysis.pipeline import PipelineResult
    from app.storage.db import session as db_session
    from app.storage.repositories import document_repo
    from tests.unit.factories import make_analysis_result, make_llm_output

    async def fake_get_pending(self, limit: int = 50):
        return docs

    async def fake_update_analysis(
        self, document_id, result, *, provider_name=None, metadata_updates=None
    ):
        pass

    async def fake_run_batch(self, batch):
        results = []
        for doc in batch:
            ar = make_analysis_result(document_id=doc.id, recommended_priority=priority_score)
            results.append(
                PipelineResult(
                    document=doc,
                    llm_output=make_llm_output(),
                    analysis_result=ar,
                )
            )
        return results

    monkeypatch.setattr(db_session, "build_session_factory", lambda _s: _make_session_factory())
    monkeypatch.setattr(document_repo.DocumentRepository, "get_pending_documents", fake_get_pending)
    monkeypatch.setattr(document_repo.DocumentRepository, "update_analysis", fake_update_analysis)
    from app.analysis import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod.AnalysisPipeline, "run_batch", fake_run_batch)
    monkeypatch.setattr(kw_engine.KeywordEngine, "from_monitor_dir", lambda _p: object())


def test_analyze_pending_dispatches_alerts_for_high_priority_docs(monkeypatch) -> None:
    """Phase 4: AlertService.process_document is called for each successful result."""
    doc = CanonicalDocument(
        url="https://example.com/hot-news", title="BTC Moon", spam_probability=0.05
    )
    _patch_analysis_infra(monkeypatch, [doc], priority_score=9)

    dispatched: list = []

    async def fake_process_document(self, doc, result, spam_probability=0.0):
        dispatched.append(doc.id)
        return [object()]  # non-empty → alert fired

    from app.alerts import service as alert_service_mod

    monkeypatch.setattr(alert_service_mod.AlertService, "process_document", fake_process_document)

    result = runner.invoke(app, ["query", "analyze-pending", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "Analysis complete! 1 success" in result.output
    assert len(dispatched) == 1
    assert "Alerts dispatched: 1" in result.output


def test_analyze_pending_no_alerts_flag_suppresses_dispatch(monkeypatch) -> None:
    """--no-alerts must skip Phase 4 entirely."""
    doc = CanonicalDocument(url="https://example.com/hot-news", title="BTC Moon")
    _patch_analysis_infra(monkeypatch, [doc], priority_score=9)

    dispatched: list = []

    async def fake_process_document(self, doc, result, spam_probability=0.0):
        dispatched.append(doc.id)
        return [object()]

    from app.alerts import service as alert_service_mod

    monkeypatch.setattr(alert_service_mod.AlertService, "process_document", fake_process_document)

    result = runner.invoke(app, ["query", "analyze-pending", "--no-alerts", "--limit", "1"])

    assert result.exit_code == 0, result.output
    assert "Analysis complete! 1 success" in result.output
    assert len(dispatched) == 0
    assert "Alerts dispatched" not in result.output


def test_analyze_pending_alert_failure_does_not_abort_analysis(monkeypatch) -> None:
    """Alert dispatch errors must be fail-open: analysis succeeds even if alerts crash."""
    docs = [
        CanonicalDocument(url="https://example.com/doc-1", title="News 1"),
        CanonicalDocument(url="https://example.com/doc-2", title="News 2"),
    ]
    _patch_analysis_infra(monkeypatch, docs, priority_score=9)

    async def exploding_process_document(self, doc, result, spam_probability=0.0):
        raise RuntimeError("Telegram timeout")

    from app.alerts import service as alert_service_mod

    monkeypatch.setattr(
        alert_service_mod.AlertService, "process_document", exploding_process_document
    )

    result = runner.invoke(app, ["query", "analyze-pending", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "Analysis complete! 2 success" in result.output
    assert "Alerts dispatched" not in result.output
