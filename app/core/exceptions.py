"""Typed domain exceptions.

Each exception carries an HTTP status and a stable machine-readable ``code`` so
the API layer can translate failures into safe responses without leaking
internals. Stack traces never reach a client; they go to structured logs keyed
by ``request_id``.

``retryable`` marks failures where a retry is genuinely justified (transient
provider errors, timeouts, rate limits) as opposed to failures that will fail
identically on retry (malformed input, unknown reference id, schema violation).
"""

from __future__ import annotations

from typing import Any


class ClaimVerificationError(Exception):
    """Base class for all domain failures."""

    code: str = "internal_error"
    status_code: int = 500
    retryable: bool = False

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        """Client-safe error body."""
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --------------------------------------------------------------------------- #
# Client / input errors
# --------------------------------------------------------------------------- #
class InvalidClaimError(ClaimVerificationError):
    """Input is not a verifiable commercial claim (empty, too long, no assertion)."""

    code = "invalid_claim"
    status_code = 422


class ReferenceNotFoundError(ClaimVerificationError):
    """A reference id or SKU was supplied explicitly but does not exist."""

    code = "reference_not_found"
    status_code = 404

    def __init__(self, identifier: str, *, field: str = "reference_id") -> None:
        super().__init__(
            f"No reference record for {field}={identifier!r}",
            details={field: identifier},
        )


class BatchTooLargeError(ClaimVerificationError):
    """Batch exceeded the configured item limit."""

    code = "batch_too_large"
    status_code = 413


class VerificationTimeoutError(ClaimVerificationError):
    """The overall per-item execution budget was exhausted."""

    code = "verification_timeout"
    status_code = 504
    retryable = True


class LLMRequiredUnavailableError(ClaimVerificationError):
    """Semantic interpretation was necessary but no rater was available."""

    code = "llm_unavailable"
    status_code = 503
    retryable = True


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class ConfigurationError(ClaimVerificationError):
    """Invalid or incomplete configuration, raised at startup rather than per request."""

    code = "configuration_error"
    status_code = 500


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class RepositoryError(ClaimVerificationError):
    """The reference store is unavailable or corrupt."""

    code = "reference_repository_unavailable"
    status_code = 503
    retryable = True


# --------------------------------------------------------------------------- #
# LLM provider
# --------------------------------------------------------------------------- #
class RaterError(ClaimVerificationError):
    """Base class for semantic-rater failures.

    A rater failure degrades the pipeline to its deterministic verdict where one
    exists; it does not fail the request outright.
    """

    code = "rater_error"
    status_code = 502
    retryable = False


class OllamaModelNotFoundError(RaterError):
    """Configured Ollama model is absent from the local model registry."""

    code = "ollama_model_not_found"
    status_code = 503
    retryable = False


class RaterTimeoutError(RaterError):
    code = "llm_timeout"
    retryable = True


class RaterRateLimitedError(RaterError):
    code = "rate_limited"
    status_code = 429
    retryable = True


class RaterUnavailableError(RaterError):
    """Provider returned 5xx or the transport failed."""

    code = "llm_unavailable"
    status_code = 503
    retryable = True


class RaterInvalidResponseError(RaterError):
    """Provider replied, but the payload was not valid against our schema.

    Not retryable by default: a deterministic prompt plus temperature 0 will
    usually reproduce the same malformed output, so we fall back instead of
    burning latency budget.
    """

    code = "llm_invalid_response"
    status_code = 502
    retryable = False


class RaterAuthenticationError(RaterError):
    """Credentials are missing, rejected, or lack permission."""

    code = "llm_authentication_error"
    status_code = 401


class RaterSchemaValidationError(RaterError):
    """JSON was syntactically valid but violated the domain output schema."""

    code = "llm_schema_validation_error"
    status_code = 502


# Public Phase 3 names. Existing Rater* names remain compatible with prior code.
LLMTimeoutError = RaterTimeoutError
LLMRateLimitError = RaterRateLimitedError
LLMAuthenticationError = RaterAuthenticationError
LLMProviderUnavailableError = RaterUnavailableError
LLMInvalidResponseError = RaterInvalidResponseError
LLMSchemaValidationError = RaterSchemaValidationError
