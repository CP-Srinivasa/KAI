"""
Application Errors
==================
Custom exception hierarchy. All domain errors extend AppError.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class IngestionError(AppError): pass
class SourceNotFoundError(IngestionError): pass


class FetchError(IngestionError):
    def __init__(self, message: str, status_code: int | None = None, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.status_code = status_code


class RateLimitError(FetchError):
    def __init__(self, message: str, retry_after_seconds: int | None = None, **kw: Any) -> None:
        super().__init__(message, status_code=429, **kw)
        self.retry_after_seconds = retry_after_seconds


class ParseError(IngestionError): pass
class NormalizationError(AppError): pass
class AnalysisError(AppError): pass
class LLMError(AnalysisError): pass
class LLMTimeoutError(LLMError): pass
class LLMCostLimitError(LLMError): pass


class LLMOutputValidationError(LLMError):
    def __init__(self, message: str, raw_output: str = "", **kw: Any) -> None:
        super().__init__(message, **kw)
        self.raw_output = raw_output


class StorageError(AppError): pass
class DocumentNotFoundError(StorageError): pass
class DuplicateDocumentError(StorageError): pass
class AlertError(AppError): pass
class TelegramError(AlertError): pass
class EmailError(AlertError): pass


class QueryError(AppError): pass


class QueryParseError(QueryError):
    def __init__(self, message: str, query: str = "", position: int = -1, **kw: Any) -> None:
        super().__init__(message, **kw)
        self.query = query
        self.position = position


class QueryValidationError(QueryError): pass
class ConfigurationError(AppError): pass
