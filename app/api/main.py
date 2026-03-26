from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.analysis.factory import create_provider
from app.analysis.keywords.engine import KeywordEngine
from app.api.middleware.request_governance import RequestGovernanceMiddleware
from app.api.routers import alerts, dashboard, health, operator, query, research, sources
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings
from app.ingestion.schedulers.rss_scheduler import RSSScheduler
from app.messaging.context_builder import make_context_provider
from app.messaging.telegram_bot import TelegramOperatorBot, TelegramPoller
from app.messaging.text_intent import TextIntentProcessor
from app.messaging.voice_transcriber import VoiceTranscriber
from app.security.auth import setup_auth
from app.security.secrets import validate_secrets
from app.storage.db.session import build_session_factory

_logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    validate_secrets(settings)  # warn/fail on missing secrets at startup
    app.state.session_factory = build_session_factory(settings.db)

    # Build analysis components for full-pipeline mode
    keyword_engine = KeywordEngine.from_monitor_dir(Path(settings.monitor_dir))
    provider = None
    if settings.pipeline_provider:
        provider = create_provider(settings.pipeline_provider, settings)
        if provider is None:
            _logger.warning(
                "pipeline_provider_unavailable",
                provider=settings.pipeline_provider,
                hint="API key missing? Falling back to rule-based analysis only.",
            )
        else:
            _logger.info(
                "pipeline_provider_ready",
                provider=settings.pipeline_provider,
                cls=type(provider).__name__,
            )

    app.state.rss_scheduler = RSSScheduler(
        app.state.session_factory,
        interval_minutes=settings.pipeline_interval_minutes,
        keyword_engine=keyword_engine,
        provider=provider,
    )
    app.state.rss_scheduler.start()

    # Telegram operator bot — receives commands and free text via long-polling
    op = settings.operator
    text_processor = None
    voice_transcriber = None
    if op.telegram_polling_enabled and settings.providers.openai_api_key:
        text_processor = TextIntentProcessor(
            api_key=settings.providers.openai_api_key,
            model=settings.providers.openai_model,
            timeout=settings.providers.openai_timeout,
        )
        voice_transcriber = VoiceTranscriber(
            bot_token=op.telegram_bot_token,
            openai_api_key=settings.providers.openai_api_key,
            timeout=settings.providers.openai_timeout,
        )
        _logger.info(
            "text_and_voice_processor_ready", model=settings.providers.openai_model
        )

    # Context provider — feeds recent analyses into LLM prompts
    context_provider = make_context_provider(app.state.session_factory)

    bot = TelegramOperatorBot(
        bot_token=op.telegram_bot_token,
        admin_chat_ids=op.admin_chat_id_list,
        audit_log_path=op.command_audit_log,
        dry_run=op.telegram_dry_run,
        text_processor=text_processor,
        voice_transcriber=voice_transcriber,
        context_provider=context_provider,
        signal_handoff_log_path=op.signal_handoff_log,
        signal_exchange_outbox_log_path=op.signal_exchange_outbox_log,
        signal_append_decision_enabled=op.signal_append_decision_enabled,
        signal_auto_run_enabled=op.signal_auto_run_enabled,
        signal_auto_run_mode=op.signal_auto_run_mode,
        signal_auto_run_provider=op.signal_auto_run_provider,
        signal_forward_to_exchange_enabled=op.signal_forward_to_exchange_enabled,
        signal_exchange_sent_log_path=op.signal_exchange_sent_log,
        signal_exchange_dead_letter_log_path=op.signal_exchange_dead_letter_log,
    )
    poller = TelegramPoller(
        bot,
        poll_interval=op.telegram_poll_interval_seconds,
        long_poll_timeout=op.telegram_long_poll_timeout_seconds,
    )
    app.state.telegram_bot = bot
    app.state.telegram_poller = poller
    if op.telegram_polling_enabled:
        poller.start()
        _logger.info(
            "telegram_poller_start_requested",
            polling_enabled=True,
            bot_configured=bot.is_configured,
            dry_run=op.telegram_dry_run,
        )
    else:
        _logger.info(
            "telegram_poller_disabled",
            polling_enabled=False,
            dry_run=op.telegram_dry_run,
        )

    try:
        yield
    finally:
        poller.stop()
        app.state.rss_scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    is_production = settings.env.lower() == "production"

    app = FastAPI(
        title="AI Analyst Trading Bot",
        description="Modular AI-powered market intelligence platform",
        version="0.1.0",
        lifespan=lifespan,
        # Disable interactive API docs in production — reduces attack surface
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
    )

    # CORS — origins controlled via APP_CORS_ALLOWED_ORIGINS (comma-separated env var)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Request-ID",
            "X-Correlation-ID",
            "Idempotency-Key",
        ],
    )
    # Request governance: request-ID propagation, body-size limit, audit JSONL.
    # Must be added after CORS (middleware stack is LIFO — governance runs first).
    app.add_middleware(
        RequestGovernanceMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )
    setup_auth(app, settings.api_key, settings.env)  # attach bearer-token middleware before startup

    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(query.router)
    app.include_router(alerts.router)
    app.include_router(research.router, prefix="/research", tags=["research"])
    app.include_router(operator.router)
    app.include_router(dashboard.router)

    return app


app = create_app()
