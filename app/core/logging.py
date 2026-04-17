import logging

import structlog


def configure_logging(log_level: str = "INFO") -> None:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        level=level,
    )

    # httpx's default INFO-level log prints "HTTP Request: POST <full_url>",
    # and Telegram bot URLs carry the bot token as a path segment
    # (https://api.telegram.org/bot<TOKEN>/…). At INFO that token ends up
    # in logs/server.log in plaintext. Lift httpx/httpcore to WARNING so
    # tokens never reach the log sink while real errors still surface.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
