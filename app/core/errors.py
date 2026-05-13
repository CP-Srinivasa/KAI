class AppError(Exception):
    """Base application error."""


class ConfigurationError(AppError):
    """Invalid or missing configuration."""


class SourceError(AppError):
    """Source-related error."""


class IngestionError(SourceError):
    """Error during ingestion."""


class ResolutionError(SourceError):
    """Error resolving a source URL."""


class AnalysisError(AppError):
    """Error during analysis."""


class ProviderError(AnalysisError):
    """Error from an external provider (LLM, API)."""


class StorageError(AppError):
    """Error during storage operations."""


class ValidationError(AppError):
    """Schema or data validation error."""


class AlertError(AppError):
    """Error during alert delivery."""


class SecurityError(AppError):
    """Security violation — SSRF, invalid secrets, auth failure, etc."""
