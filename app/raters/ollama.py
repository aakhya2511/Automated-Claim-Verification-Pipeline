"""Ollama adapters implementing provider-neutral rating and extraction contracts."""

from __future__ import annotations

import asyncio
import json
import random
import re
from collections.abc import Callable
from datetime import date
from time import perf_counter
from typing import Any, Never
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import LLMSettings
from app.core.exceptions import (
    OllamaModelNotFoundError,
    RaterInvalidResponseError,
    RaterRateLimitedError,
    RaterSchemaValidationError,
    RaterTimeoutError,
    RaterUnavailableError,
)
from app.domain.enums import RATER_REASON_CODES, Attribute, ClaimType, ReasonCode, Verdict
from app.domain.models import (
    ClaimExtraction,
    ExtractionContext,
    NormalizedClaim,
    RaterMetadata,
    RaterResult,
    RatingContext,
    RatingTrace,
    ReferenceEvidence,
    TokenUsage,
)
from app.raters.extractor import EXTRACTION_JSON_SCHEMA
from app.raters.openai import _rating_input
from app.raters.prompts import load_prompt
from app.raters.schemas import SemanticRatingPayload
from app.raters.transport import ProviderResponse, Sleeper

OLLAMA_DEFAULT_BASE_URL = "http://127.0.0.1:11434/api"
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_RATE_LIMITED = 429
HTTP_SERVER_ERROR = 500

_ABSTENTION_CODES = {
    ReasonCode.REFERENCE_NOT_FOUND,
    ReasonCode.AMBIGUOUS_LANGUAGE,
    ReasonCode.INSUFFICIENT_EVIDENCE,
    ReasonCode.UNSUPPORTED_INFERENCE,
}
_MISMATCH_CODES = RATER_REASON_CODES - _ABSTENTION_CODES


def _rating_branch(
    verdict: Verdict,
    *,
    reason_codes: set[ReasonCode],
    minimum_reasons: int = 0,
    maximum_reasons: int | None = None,
) -> dict[str, object]:
    item_schema: dict[str, object] = {"type": "string"}
    if reason_codes:
        item_schema["enum"] = sorted(code.value for code in reason_codes)
    reasons: dict[str, object] = {
        "type": "array",
        "items": item_schema,
        "uniqueItems": True,
        "minItems": minimum_reasons,
    }
    if maximum_reasons is not None:
        reasons["maxItems"] = maximum_reasons
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "const": verdict.value},
            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "reason_codes": reasons,
            "explanation": {"type": "string", "minLength": 1, "maxLength": 600},
        },
        "required": ["verdict", "confidence", "reason_codes", "explanation"],
    }


OLLAMA_RATING_JSON_SCHEMA: dict[str, object] = {
    "oneOf": [
        _rating_branch(
            Verdict.SUPPORTED,
            reason_codes=set(),
            maximum_reasons=0,
        ),
        _rating_branch(
            Verdict.CONTRADICTED,
            reason_codes=set(_MISMATCH_CODES),
            minimum_reasons=1,
        ),
        _rating_branch(
            Verdict.INSUFFICIENT_EVIDENCE,
            reason_codes=set(_ABSTENTION_CODES),
        ),
        _rating_branch(
            Verdict.INVALID_CLAIM,
            reason_codes=set(RATER_REASON_CODES),
        ),
    ]
}


class OllamaIdentity(BaseModel):
    """Safe, reproducible local-provider identity returned by preflight."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = "ollama"
    endpoint: str
    configured_model: str
    resolved_model: str
    model_digest: str = Field(min_length=1)
    ollama_version: str = Field(min_length=1)
    structured_output_validated: bool


class OllamaTransport:
    """Bounded async transport for Ollama's native local HTTP API."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = perf_counter,
    ) -> None:
        self._settings = settings
        self._sleeper = sleeper
        self._random = random_value
        self._monotonic = monotonic
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._owns_client = client is None
        self.base_url = (settings.base_url or OLLAMA_DEFAULT_BASE_URL).rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/",
            timeout=httpx.Timeout(
                settings.timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.max_connections,
                max_keepalive_connections=settings.max_keepalive_connections,
            ),
        )

    async def chat(
        self, *, messages: list[dict[str, str]], schema: dict[str, object]
    ) -> ProviderResponse:
        deadline = self._monotonic() + self._settings.timeout_seconds
        body = {
            "model": self._settings.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": {
                "temperature": self._settings.temperature,
                "num_predict": self._settings.max_output_tokens,
            },
        }
        async with self._semaphore:
            response, attempts, latency_ms = await self._request(
                "POST", "chat", json_body=body, deadline=deadline
            )
        return self._parse_chat(response, attempts=attempts, latency_ms=latency_ms)

    async def get_json(self, path: str) -> dict[str, Any]:
        response, _attempts, _latency = await self._request("GET", path)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RaterInvalidResponseError("Ollama returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise RaterInvalidResponseError("Ollama response was not an object")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        deadline: float | None = None,
    ) -> tuple[httpx.Response, int, float]:
        started = perf_counter()
        operation_deadline = deadline or self._monotonic() + self._settings.timeout_seconds
        for attempt in range(1, self._settings.max_retries + 2):
            remaining = operation_deadline - self._monotonic()
            if remaining <= 0:
                raise RaterTimeoutError("Ollama operation exceeded its total timeout budget")
            request_timeout = httpx.Timeout(
                remaining,
                connect=min(self._settings.connect_timeout_seconds, remaining),
            )
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json_body,
                    timeout=request_timeout,
                )
            except httpx.TimeoutException:
                error: Exception = RaterTimeoutError("Ollama request timed out")
            except httpx.TransportError:
                error = RaterUnavailableError("Ollama local server is unavailable")
            else:
                provider_error = (
                    RaterTimeoutError("Ollama operation exceeded its total timeout budget")
                    if self._monotonic() >= operation_deadline
                    else self._http_error(response)
                )
                if provider_error is None:
                    return response, attempt, (perf_counter() - started) * 1000
                error = provider_error
            if not getattr(error, "retryable", False) or attempt > self._settings.max_retries:
                raise error
            delay = self._settings.retry_base_delay_seconds * (2 ** (attempt - 1))
            jittered_delay = delay * (0.5 + self._random())
            if jittered_delay >= operation_deadline - self._monotonic():
                raise RaterTimeoutError("Ollama operation exceeded its total timeout budget")
            await self._sleeper(jittered_delay)
        raise RaterUnavailableError("Ollama request failed after retries")

    @staticmethod
    def _http_error(response: httpx.Response) -> Exception | None:
        if response.status_code < HTTP_BAD_REQUEST:
            return None
        if response.status_code == HTTP_NOT_FOUND:
            return OllamaModelNotFoundError("Ollama model or endpoint was not found")
        if response.status_code == HTTP_RATE_LIMITED:
            return RaterRateLimitedError("Ollama rate limit exceeded")
        if response.status_code >= HTTP_SERVER_ERROR:
            return RaterUnavailableError("Ollama local server returned an error")
        return RaterInvalidResponseError(
            f"Ollama rejected the request with status {response.status_code}"
        )

    @staticmethod
    def _parse_chat(
        response: httpx.Response, *, attempts: int, latency_ms: float
    ) -> ProviderResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RaterInvalidResponseError("Ollama returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise RaterInvalidResponseError("Ollama response was not an object")
        message = payload.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RaterInvalidResponseError("Ollama returned no structured output")
        prompt_tokens = _optional_nonnegative_int(payload.get("prompt_eval_count"))
        output_tokens = _optional_nonnegative_int(payload.get("eval_count"))
        total_tokens = (
            prompt_tokens + output_tokens
            if prompt_tokens is not None and output_tokens is not None
            else None
        )
        return ProviderResponse(
            text=text,
            request_id=None,
            attempts=attempts,
            latency_ms=latency_ms,
            usage=TokenUsage(
                input_tokens=prompt_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            ),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class OllamaRater:
    """Strict V1 semantic rater over Ollama structured output."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: OllamaTransport | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or OllamaTransport(settings)
        self._prompt_version = prompt_version or settings.rater_prompt_version
        self._prompt = load_prompt("rater", self._prompt_version)

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult:
        started = perf_counter()
        metadata = RaterMetadata(
            provider="ollama",
            model=self.model_name,
            prompt_version=self.prompt_version,
            schema_version=self._settings.schema_version,
            temperature=self._settings.temperature,
        )
        provider = await self._transport.chat(
            messages=[
                {"role": "system", "content": self._prompt},
                {"role": "user", "content": _rating_input(claim, evidence, context)},
            ],
            schema=OLLAMA_RATING_JSON_SCHEMA,
        )
        try:
            parsed = SemanticRatingPayload.model_validate_json(provider.text)
        except ValidationError as exc:
            _raise_validation(exc, operation="rating")
        trace = RatingTrace(
            request_id=context.request_id,
            trace_id=context.trace_id or uuid4().hex,
            metadata=metadata,
            attempt_count=provider.attempts,
            provider_latency_ms=provider.latency_ms,
            total_rater_latency_ms=(perf_counter() - started) * 1000,
            verdict=parsed.verdict,
            confidence=parsed.confidence,
            reason_codes=parsed.reason_codes,
            usage=provider.usage,
        )
        return RaterResult(
            verdict=parsed.verdict,
            confidence=parsed.confidence,
            reason_codes=parsed.reason_codes,
            explanation=parsed.explanation,
            metadata=metadata,
            trace=trace,
        )

    async def aclose(self) -> None:
        await self._transport.aclose()


class OllamaClaimExtractor:
    """Strict V1 claim extractor over Ollama structured output."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: OllamaTransport | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or OllamaTransport(settings)
        self._prompt_version = prompt_version or settings.extraction_prompt_version
        self._prompt = load_prompt("extractor", self._prompt_version)

    async def extract(self, raw_claim: str, *, context: ExtractionContext) -> ClaimExtraction:
        provider = await self._transport.chat(
            messages=[
                {"role": "system", "content": self._prompt},
                {
                    "role": "user",
                    "content": (
                        f"CLAIM_JSON:\n{json.dumps(raw_claim, ensure_ascii=False)}\n"
                        f"CONTEXT_JSON:\n{context.model_dump_json()}"
                    ),
                },
            ],
            schema=EXTRACTION_JSON_SCHEMA,
        )
        try:
            return ClaimExtraction.model_validate_json(provider.text)
        except ValidationError as exc:
            _raise_validation(exc, operation="extraction")

    async def aclose(self) -> None:
        await self._transport.aclose()


async def preflight_ollama(
    settings: LLMSettings, *, transport: OllamaTransport | None = None
) -> OllamaIdentity:
    """Verify server, pinned model digest, and normal-schema structured output."""
    resolved_transport = transport or OllamaTransport(settings)
    try:
        version_payload = await resolved_transport.get_json("version")
        version = version_payload.get("version")
        if not isinstance(version, str) or not version:
            raise RaterInvalidResponseError("Ollama version response is invalid")
        tags = await resolved_transport.get_json("tags")
        model = _find_model(tags, settings.model)
        digest = model.get("digest")
        resolved_name = model.get("name") or model.get("model")
        if not isinstance(digest, str) or not re.fullmatch(r"(?:sha256:)?[0-9a-fA-F]{64}", digest):
            raise RaterInvalidResponseError("Ollama model has no stable digest")
        if not isinstance(resolved_name, str) or not resolved_name:
            raise RaterInvalidResponseError("Ollama model has no resolved name")
        rater = OllamaRater(settings, transport=resolved_transport)
        result = await rater.rate(
            NormalizedClaim(
                raw_text="Shipping is free.",
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.FREE_SHIPPING,
                value=True,
            ),
            ReferenceEvidence(record_id="preflight", fields={"free_shipping": True}),
            context=RatingContext(evaluation_date=date(2026, 9, 9), request_id="preflight"),
        )
        return OllamaIdentity(
            endpoint=resolved_transport.base_url,
            configured_model=settings.model,
            resolved_model=resolved_name,
            model_digest=digest,
            ollama_version=version,
            structured_output_validated=result.metadata is not None,
        )
    finally:
        await resolved_transport.aclose()


def _find_model(payload: dict[str, Any], configured: str) -> dict[str, Any]:
    models = payload.get("models")
    if not isinstance(models, list):
        raise RaterInvalidResponseError("Ollama tags response has no model list")
    accepted = {configured, f"{configured}:latest"} if ":" not in configured else {configured}
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("name") in accepted or model.get("model") in accepted:
            return model
    raise OllamaModelNotFoundError(
        f"Ollama model {configured!r} is not installed; run: ollama pull {configured}"
    )


def _raise_validation(exc: ValidationError, *, operation: str) -> Never:
    error_types = {str(item["type"]) for item in exc.errors()}
    if "json_invalid" in error_types:
        raise RaterInvalidResponseError(f"Ollama {operation} was not JSON") from exc
    raise RaterSchemaValidationError(f"Ollama {operation} failed schema validation") from exc


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RaterInvalidResponseError("Ollama returned invalid token usage")
    return value
