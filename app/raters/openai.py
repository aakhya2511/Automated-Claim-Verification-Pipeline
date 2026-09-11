"""OpenAI adapter implementing the provider-neutral semantic-rater contract."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pydantic import ValidationError

from app.core.config import LLMSettings
from app.core.exceptions import RaterInvalidResponseError, RaterSchemaValidationError
from app.domain.models import (
    NormalizedClaim,
    RaterMetadata,
    RaterResult,
    RatingContext,
    RatingTrace,
    ReferenceEvidence,
)
from app.raters.prompts import PROMPTS_ROOT, load_prompt
from app.raters.schemas import RATING_JSON_SCHEMA, SemanticRatingPayload
from app.raters.transport import OpenAIResponsesTransport


class OpenAIRater:
    """Strict structured-output rater over the OpenAI Responses API."""

    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: OpenAIResponsesTransport | None = None,
        prompt_version: str | None = None,
        prompts_dir: Path | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or OpenAIResponsesTransport(settings)
        self._prompt_version = prompt_version or settings.rater_prompt_version
        self._prompt = load_prompt("rater", self._prompt_version, root=prompts_dir or PROMPTS_ROOT)

    @property
    def provider_name(self) -> str:
        return "openai"

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
            provider="openai",
            model=self.model_name,
            prompt_version=self.prompt_version,
            schema_version=self._settings.schema_version,
            temperature=self._settings.temperature,
        )
        body = {
            "model": self.model_name,
            "instructions": self._prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": _rating_input(claim, evidence, context),
                        }
                    ],
                }
            ],
            "temperature": self._settings.temperature,
            "max_output_tokens": self._settings.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "semantic_rating",
                    "strict": True,
                    "schema": RATING_JSON_SCHEMA,
                }
            },
        }
        provider = await self._transport.create(body)
        try:
            parsed = SemanticRatingPayload.model_validate_json(provider.text)
        except ValidationError as exc:
            error_types = {str(item["type"]) for item in exc.errors()}
            if "json_invalid" in error_types:
                raise RaterInvalidResponseError("LLM output was not a single JSON object") from exc
            raise RaterSchemaValidationError("LLM output failed rating schema validation") from exc

        trace_id = context.trace_id or uuid4().hex
        trace = RatingTrace(
            request_id=context.request_id,
            trace_id=trace_id,
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


def _rating_input(
    claim: NormalizedClaim, evidence: ReferenceEvidence, context: RatingContext
) -> str:
    """Serialize three labelled JSON values; claim text cannot change instruction roles."""
    return "\n".join(
        (
            "CLAIM_JSON:",
            claim.model_dump_json(exclude={"notes"}),
            "REFERENCE_EVIDENCE_JSON:",
            evidence.model_dump_json(),
            "EVALUATION_CONTEXT_JSON:",
            context.model_dump_json(),
        )
    )
