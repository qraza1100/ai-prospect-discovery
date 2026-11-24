from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas import PlatformStatus


class AppError(Exception):
    code = "internal_error"
    status_code = 500
    public_message = "An unexpected error occurred."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.public_message)
        self.message = message or self.public_message


class InvalidQueryError(AppError):
    code = "invalid_query"
    status_code = 400
    public_message = "The query does not describe a set of people or businesses to find."


class DiscoveryUnavailableError(AppError):
    code = "discovery_unavailable"
    status_code = 503
    public_message = "No discovery source is available right now."

    def __init__(self, message: str | None = None, platforms: list[PlatformStatus] | None = None) -> None:
        super().__init__(message)
        self.platforms = platforms or []


class UpstreamError(Exception):
    """A third-party API failed after retries. The message is safe to show to clients."""

    def __init__(self, service: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{service}: {message}")
        self.service = service
        self.status = status
        self.safe_message = message


class LLMError(Exception):
    pass


class UnsafeURLError(ValueError):
    pass
