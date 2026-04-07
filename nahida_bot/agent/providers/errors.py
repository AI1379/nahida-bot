"""Provider error hierarchy and normalized error codes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ProviderError(Exception):
    """Base provider exception with normalized error code."""

    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class ProviderTimeoutError(ProviderError):
    """Raised when provider call exceeds timeout limit."""

    def __init__(self, message: str = "Provider request timed out") -> None:
        super().__init__(code="provider_timeout", message=message, retryable=True)


class ProviderRateLimitError(ProviderError):
    """Raised when provider rejects request due to throttling."""

    def __init__(self, message: str = "Provider rate limit reached") -> None:
        super().__init__(code="provider_rate_limited", message=message, retryable=True)


class ProviderAuthError(ProviderError):
    """Raised when provider authentication fails."""

    def __init__(self, message: str = "Provider authentication failed") -> None:
        super().__init__(code="provider_auth_failed", message=message, retryable=False)


class ProviderBadResponseError(ProviderError):
    """Raised when provider response does not follow expected schema."""

    def __init__(self, message: str = "Provider response format is invalid") -> None:
        super().__init__(code="provider_bad_response", message=message, retryable=False)


class ProviderTransportError(ProviderError):
    """Raised for upstream transport-level errors."""

    def __init__(self, message: str = "Provider transport request failed") -> None:
        super().__init__(
            code="provider_transport_error", message=message, retryable=True
        )
