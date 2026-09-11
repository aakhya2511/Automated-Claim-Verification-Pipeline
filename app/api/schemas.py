"""Strict public HTTP models, intentionally narrower than internal audit models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator
from pydantic_core import to_jsonable_python

from app.domain.enums import (
    Attribute,
    ClaimType,
    EscalationReason,
    ReasonCode,
    Verdict,
    VerificationPath,
)
from app.domain.models import VerificationFailure, VerificationResult


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerifyClaimRequest(APIModel):
    """One commercial assertion and an optional authoritative record selector."""

    claim: str = Field(
        min_length=1,
        max_length=1000,
        strict=True,
        description="Commercial assertion to verify; maximum 1,000 characters.",
        examples=["Offer BACK159 costs $127.49."],
    )
    reference_id: str | None = Field(default=None, min_length=1, max_length=64, strict=True)
    sku: str | None = Field(default=None, min_length=1, max_length=120, strict=True)
    region: str | None = Field(default=None, min_length=2, max_length=8, strict=True)
    as_of: date | None = None


class BatchVerifyRequest(APIModel):
    claims: list[VerifyClaimRequest] = Field(
        min_length=1,
        description="Ordered claims. Runtime maximum is configured by ACV_SERVER__MAX_BATCH_ITEMS.",
    )


class ClaimSummary(APIModel):
    type: ClaimType
    attribute: Attribute
    parse_confidence: float


class EvidenceSummary(APIModel):
    reference_id: str | None
    fields: dict[str, JsonValue]
    reference_updated_at: datetime | None
    reference_version: str | None


class EscalationSummary(APIModel):
    required: bool
    reason: EscalationReason | None


class LatencySummary(APIModel):
    normalization: float
    extraction: float
    retrieval: float
    rules: float
    rating: float
    decision: float
    total: float


class VerifyClaimResponse(APIModel):
    request_id: str
    verdict: Verdict
    confidence: float
    reason_codes: tuple[ReasonCode, ...]
    explanation: str
    verification_path: VerificationPath
    escalation: EscalationSummary
    claim: ClaimSummary
    evidence: EvidenceSummary
    latency_ms: LatencySummary

    @classmethod
    def from_domain(cls, result: VerificationResult) -> VerifyClaimResponse:
        if result.normalized_claim is None:
            raise ValueError("verification result has no normalized claim")
        claim = result.normalized_claim
        public_fields = dict(result.evidence.fields)
        if claim.claim_type is not ClaimType.SUBSCRIPTION_TERMS:
            public_fields.pop(Attribute.TERMS.value, None)
        return cls(
            request_id=result.request_id,
            verdict=result.verdict,
            confidence=result.confidence,
            reason_codes=result.reason_codes,
            explanation=result.explanation,
            verification_path=result.verification_path,
            escalation=EscalationSummary(
                required=result.escalation.required,
                reason=result.escalation.reason,
            ),
            claim=ClaimSummary(
                type=claim.claim_type,
                attribute=claim.attribute,
                parse_confidence=claim.parse_confidence,
            ),
            evidence=EvidenceSummary(
                reference_id=result.evidence.record_id,
                fields=cast(dict[str, JsonValue], to_jsonable_python(public_fields)),
                reference_updated_at=result.evidence.record_updated_at,
                reference_version=result.evidence.reference_version,
            ),
            latency_ms=LatencySummary.model_validate(result.latency_ms.model_dump()),
        )


class ErrorDetail(APIModel):
    code: str
    message: str
    details: dict[str, JsonValue] | None = None


class BatchItemResponse(APIModel):
    request_id: str
    latency_ms: float
    result: VerifyClaimResponse | None = None
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> Self:
        if (self.result is None) == (self.error is None):
            raise ValueError("batch item must contain exactly one of result or error")
        return self

    @classmethod
    def from_domain(cls, item: VerificationResult | VerificationFailure) -> BatchItemResponse:
        if isinstance(item, VerificationResult):
            response = VerifyClaimResponse.from_domain(item)
            return cls(
                request_id=item.request_id,
                latency_ms=item.latency_ms.total,
                result=response,
            )
        return cls(
            request_id=item.request_id,
            latency_ms=item.latency_ms,
            error=ErrorDetail(code=item.code, message=item.message),
        )


class BatchVerifyResponse(APIModel):
    batch_request_id: str
    items: list[BatchItemResponse]


class ErrorEnvelope(APIModel):
    error: ErrorDetail
    request_id: str | None = None
