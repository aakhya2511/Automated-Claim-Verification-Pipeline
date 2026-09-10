"""Strict schemas at the untrusted model-output boundary."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import RATER_REASON_CODES, ReasonCode, Verdict


class SemanticRatingPayload(BaseModel):
    """The only JSON shape accepted from a semantic rater."""

    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[ReasonCode, ...]
    explanation: str = Field(min_length=1, max_length=600)

    @field_validator("reason_codes")
    @classmethod
    def _rater_codes_only(cls, value: tuple[ReasonCode, ...]) -> tuple[ReasonCode, ...]:
        invalid = set(value) - RATER_REASON_CODES
        if invalid:
            names = ", ".join(sorted(code.value for code in invalid))
            raise ValueError(f"reason codes are not permitted for the rater: {names}")
        if len(set(value)) != len(value):
            raise ValueError("reason_codes must not contain duplicates")
        return value

    @model_validator(mode="after")
    def _verdict_matches_reasons(self) -> Self:
        abstention_codes = {
            ReasonCode.REFERENCE_NOT_FOUND,
            ReasonCode.AMBIGUOUS_LANGUAGE,
            ReasonCode.INSUFFICIENT_EVIDENCE,
            ReasonCode.UNSUPPORTED_INFERENCE,
        }
        mismatch_codes = RATER_REASON_CODES - abstention_codes
        codes = set(self.reason_codes)
        if self.verdict is Verdict.SUPPORTED and codes:
            raise ValueError("SUPPORTED cannot carry mismatch or abstention reason codes")
        if self.verdict is Verdict.CONTRADICTED and not codes.intersection(mismatch_codes):
            raise ValueError("CONTRADICTED requires an explicit mismatch reason code")
        if self.verdict is Verdict.CONTRADICTED and codes.intersection(abstention_codes):
            raise ValueError("CONTRADICTED cannot carry abstention reason codes")
        if self.verdict is Verdict.INSUFFICIENT_EVIDENCE and codes.intersection(mismatch_codes):
            raise ValueError("INSUFFICIENT_EVIDENCE cannot carry mismatch reason codes")
        return self


RATING_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in Verdict],
        },
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason_codes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(code.value for code in RATER_REASON_CODES),
            },
            "uniqueItems": True,
        },
        "explanation": {"type": "string", "minLength": 1, "maxLength": 600},
    },
    "required": ["verdict", "confidence", "reason_codes", "explanation"],
}
