"""Async OpenAI Responses API transport hidden behind provider-neutral outputs."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.config import LLMSettings
from app.core.exceptions import (
    RaterAuthenticationError,
    RaterInvalidResponseError,
    RaterRateLimitedError,
    RaterTimeoutError,
    RaterUnavailableError,
)
from app.domain.models import TokenUsage

Sleeper = Callable[[float], Awaitable[None]]
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_RATE_LIMITED = 429
HTTP_SERVER_ERROR = 500


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    request_id: str | None
    attempts: int
    latency_ms: float
    usage: TokenUsage | None


class OpenAIResponsesTransport:
    """Small maintained wire adapter with bounded transient retries."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if settings.api_key is None:
            raise RaterAuthenticationError("OpenAI API credentials are not configured")
        self._settings = settings
        self._sleeper = sleeper
        self._random = random_value
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._owns_client = client is None
        base_url = (settings.base_url or "https://api.openai.com/v1").rstrip("/")
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                settings.timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            limits=httpx.Limits(
                max_connections=settings.max_connections,
                max_keepalive_connections=settings.max_keepalive_connections,
            ),
            headers={"Authorization": f"Bearer {settings.api_key.get_secret_value()}"},
        )

    async def create(self, body: dict[str, Any]) -> ProviderResponse:
        async with self._semaphore:
            return await self._create(body)

    async def _create(self, body: dict[str, Any]) -> ProviderResponse:
        started = perf_counter()
        for attempt in range(1, self._settings.max_retries + 2):
            try:
                response = await self._client.post("responses", json=body)
            except httpx.TimeoutException:
                error: Exception = RaterTimeoutError("LLM provider request timed out")
            except httpx.TransportError:
                error = RaterUnavailableError("LLM provider transport failed")
            else:
                provider_error = self._http_error(response)
                if provider_error is None:
                    provider_latency = (perf_counter() - started) * 1000
                    return self._parse(response, attempt, provider_latency)
                error = provider_error

            if not getattr(error, "retryable", False) or attempt > self._settings.max_retries:
                raise error
            delay = self._settings.retry_base_delay_seconds * (2 ** (attempt - 1))
            await self._sleeper(delay * (0.5 + self._random()))

        elapsed = (perf_counter() - started) * 1000
        raise RaterUnavailableError(f"LLM provider failed after {elapsed:.1f}ms")

    @staticmethod
    def _http_error(response: httpx.Response) -> Exception | None:
        if response.status_code < HTTP_BAD_REQUEST:
            return None
        if response.status_code in {HTTP_UNAUTHORIZED, HTTP_FORBIDDEN}:
            return RaterAuthenticationError("LLM provider rejected credentials")
        if response.status_code == HTTP_RATE_LIMITED:
            return RaterRateLimitedError("LLM provider rate limit exceeded")
        if response.status_code >= HTTP_SERVER_ERROR:
            return RaterUnavailableError("LLM provider is unavailable")
        return RaterInvalidResponseError(
            f"LLM provider rejected the request with status {response.status_code}"
        )

    @staticmethod
    def _parse(response: httpx.Response, attempts: int, latency_ms: float) -> ProviderResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RaterInvalidResponseError("LLM provider returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise RaterInvalidResponseError("LLM provider response was not an object")
        text = payload.get("output_text")
        if not isinstance(text, str) or not text.strip():
            text = OpenAIResponsesTransport._nested_output_text(payload)
        if not text:
            raise RaterInvalidResponseError("LLM provider returned no structured output")
        usage_raw = payload.get("usage")
        usage = None
        if isinstance(usage_raw, dict):
            try:
                usage = TokenUsage(
                    input_tokens=usage_raw.get("input_tokens"),
                    output_tokens=usage_raw.get("output_tokens"),
                    total_tokens=usage_raw.get("total_tokens"),
                )
            except ValidationError as exc:
                raise RaterInvalidResponseError(
                    "LLM provider returned invalid usage metadata"
                ) from exc
        request_id = payload.get("id")
        return ProviderResponse(
            text=text,
            request_id=request_id if isinstance(request_id, str) else None,
            attempts=attempts,
            latency_ms=latency_ms,
            usage=usage,
        )

    @staticmethod
    def _nested_output_text(payload: dict[str, Any]) -> str | None:
        output = payload.get("output")
        if not isinstance(output, list):
            return None
        texts: list[str] = []
        for item in output:
            if not isinstance(item, dict) or not isinstance(item.get("content"), list):
                continue
            for content in item["content"]:
                if isinstance(content, dict) and content.get("type") == "output_text":
                    value = content.get("text")
                    if isinstance(value, str):
                        texts.append(value)
        combined = "".join(texts).strip()
        return combined or None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
