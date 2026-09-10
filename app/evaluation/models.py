"""Typed, evaluation-only records and freeze metadata."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.enums import ClaimType, Verdict
from app.domain.models import VerificationRequest


class EvaluationModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceKind(StrEnum):
    CLEAN = "CLEAN"
    MUTATED = "MUTATED"
    INSUFFICIENT = "INSUFFICIENT"
    INVALID = "INVALID"


class Difficulty(StrEnum):
    EASY = "EASY"
    MODERATE = "MODERATE"
    HARD = "HARD"


class MutationType(StrEnum):
    PRICE_MISMATCH = "PRICE_MISMATCH"
    DISCOUNT_MISMATCH = "DISCOUNT_MISMATCH"
    SHIPPING_MISMATCH = "SHIPPING_MISMATCH"
    AVAILABILITY_MISMATCH = "AVAILABILITY_MISMATCH"
    FEATURE_INCLUSION_MISMATCH = "FEATURE_INCLUSION_MISMATCH"
    FEATURE_EXCLUSION_MISMATCH = "FEATURE_EXCLUSION_MISMATCH"
    TRIAL_DURATION_MISMATCH = "TRIAL_DURATION_MISMATCH"
    SUBSCRIPTION_PRICE_MISMATCH = "SUBSCRIPTION_PRICE_MISMATCH"
    PROMOTION_END_MISMATCH = "PROMOTION_END_MISMATCH"
    REGION_MISMATCH = "REGION_MISMATCH"
    MINIMUM_PURCHASE_MISMATCH = "MINIMUM_PURCHASE_MISMATCH"


class MutationMetadata(EvaluationModel):
    type: MutationType
    source_value: Any
    mutated_value: Any
    magnitude: str | None = None


class GenerationMetadata(EvaluationModel):
    template_id: str
    seed: int
    generator_version: str


class EvaluationSample(EvaluationModel):
    sample_id: str = Field(pattern=r"^(?:eval|dev)-\d{6}$")
    claim: str = Field(min_length=1, max_length=1000)
    reference_id: str | None = None
    region: str | None = None
    as_of: date | None = None
    expected_verdict: Verdict
    claim_type: ClaimType
    source_kind: SourceKind
    difficulty: Difficulty
    mutation: MutationMetadata | None = None
    generation: GenerationMetadata
    ground_truth_explanation: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def _metadata_matches_source(self) -> EvaluationSample:
        if self.source_kind is SourceKind.MUTATED and self.mutation is None:
            raise ValueError("mutated samples require mutation metadata")
        if self.source_kind is not SourceKind.MUTATED and self.mutation is not None:
            raise ValueError("only mutated samples may carry mutation metadata")
        return self

    def to_verification_request(self) -> VerificationRequest:
        """Drop every label and generation field at the system-under-test boundary."""
        return VerificationRequest(
            claim=self.claim,
            reference_id=self.reference_id,
            region=self.region,
            as_of=self.as_of,
            request_id=self.sample_id,
        )


class DatasetManifest(EvaluationModel):
    dataset_version: str
    sample_count: int
    seed: int
    generator_version: str
    catalog_fingerprint: str
    sha256: str
    verdict_distribution: dict[str, int]
    claim_type_distribution: dict[str, int]
    mutation_distribution: dict[str, int]
    difficulty_distribution: dict[str, int]
    source_distribution: dict[str, int]


class CoverageReport(EvaluationModel):
    dataset_version: str
    sample_count: int
    catalog_records: int
    unique_reference_records: int
    catalog_coverage_percent: float
    max_samples_per_reference: int
    median_samples_per_reference: float
    duplicate_claims: int
    duplicate_claim_reference_pairs: int
    duplicate_semantic_mutations: int
    template_distribution: dict[str, int]
    warnings: tuple[str, ...] = ()
