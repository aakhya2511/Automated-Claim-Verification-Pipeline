"""LLM structured extraction adapter, separate from truth rating."""

from __future__ import annotations

import json

from pydantic import ValidationError

from app.core.config import LLMSettings
from app.core.exceptions import RaterInvalidResponseError, RaterSchemaValidationError
from app.domain.enums import Attribute, ClaimType, Operator, Qualifier
from app.domain.models import ClaimExtraction, ExtractionContext
from app.raters.prompts import load_prompt
from app.raters.transport import OpenAIResponsesTransport

EXTRACTION_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "claim_type": {"type": "string", "enum": [item.value for item in ClaimType]},
        "attribute": {"type": "string", "enum": [item.value for item in Attribute]},
        "operator": {"type": "string", "enum": [item.value for item in Operator]},
        "qualifier": {"type": "string", "enum": [item.value for item in Qualifier]},
        "value": {"type": ["number", "boolean", "string", "null"]},
        "unit": {"type": ["string", "null"]},
        "feature": {"type": ["string", "null"]},
        "negated": {"type": "boolean"},
        "entity_name": {"type": ["string", "null"]},
        "region": {"type": ["string", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "claim_type",
        "attribute",
        "operator",
        "qualifier",
        "value",
        "unit",
        "feature",
        "negated",
        "entity_name",
        "region",
        "confidence",
    ],
}


class OpenAIClaimExtractor:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        transport: OpenAIResponsesTransport | None = None,
        prompt_version: str | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport or OpenAIResponsesTransport(settings)
        self._prompt_version = prompt_version or settings.extraction_prompt_version
        self._prompt = load_prompt("extractor", self._prompt_version)

    async def extract(self, raw_claim: str, *, context: ExtractionContext) -> ClaimExtraction:
        body = {
            "model": self._settings.model,
            "instructions": self._prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"CLAIM_JSON:\n{_json_string(raw_claim)}\n"
                                f"CONTEXT_JSON:\n{context.model_dump_json()}"
                            ),
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
                    "name": "claim_extraction",
                    "strict": True,
                    "schema": EXTRACTION_JSON_SCHEMA,
                }
            },
        }
        response = await self._transport.create(body)
        try:
            return ClaimExtraction.model_validate_json(response.text)
        except ValidationError as exc:
            error_types = {str(item["type"]) for item in exc.errors()}
            if "json_invalid" in error_types:
                raise RaterInvalidResponseError("LLM extraction was not JSON") from exc
            raise RaterSchemaValidationError("LLM extraction failed schema validation") from exc

    async def aclose(self) -> None:
        await self._transport.aclose()


def _json_string(value: str) -> str:
    # Pydantic's JSON serializer is already in the dependency graph; this tiny
    # model-free form keeps untrusted text encoded as one JSON string.
    return json.dumps(value, ensure_ascii=False)
