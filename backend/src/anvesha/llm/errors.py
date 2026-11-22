"""Errors raised by the model layer. ``http_status`` drives the API's error responses."""

from anvesha.errors import AppError


class LLMError(AppError):
    code = "llm_error"
    http_status = 502
    retryable = False


class ModelNotConfiguredError(LLMError):
    code = "model_not_configured"
    http_status = 409


class BudgetExceededError(LLMError):
    code = "budget_exceeded"
    http_status = 402


class LLMAuthError(LLMError):
    code = "provider_auth_failed"


class LLMRateLimitError(LLMError):
    code = "provider_rate_limited"
    http_status = 429
    retryable = True


class LLMTimeoutError(LLMError):
    code = "provider_timeout"
    http_status = 504
    retryable = True


class LLMConnectionError(LLMError):
    code = "provider_unreachable"
    retryable = True


class LLMBadRequestError(LLMError):
    """The provider rejected the request (unknown model, bad parameters, context too long)."""

    code = "provider_rejected_request"


class LLMProviderError(LLMError):
    code = "provider_error"
    retryable = True


class LLMInvalidResponseError(LLMError):
    code = "provider_invalid_response"
