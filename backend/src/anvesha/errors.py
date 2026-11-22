"""Base error for failures the API reports to clients as ``{"detail": ..., "code": ...}``."""


class AppError(Exception):
    code = "error"
    http_status = 500
    # Whether trying the same thing again later might work (agents use this to decide).
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        if retryable is not None:
            self.retryable = retryable


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404


class InvalidInputError(AppError):
    code = "invalid_input"
    http_status = 422


class NotConfiguredError(AppError):
    code = "not_configured"
    http_status = 409


class UpstreamError(AppError):
    """An external service (search API, website, scholarly API) failed."""

    code = "upstream_error"
    http_status = 502
    retryable = True
