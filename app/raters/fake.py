"""Deterministic test doubles with observable calls and no network path."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import RaterTimeoutError
from app.domain.models import (
    ClaimExtraction,
    ExtractionContext,
    NormalizedClaim,
    RaterResult,
    RatingContext,
    ReferenceEvidence,
)


@dataclass(frozen=True, slots=True)
class CapturedRating:
    claim: NormalizedClaim
    evidence: ReferenceEvidence
    context: RatingContext


class FakeRater:
    def __init__(
        self,
        result: RaterResult,
        *,
        failure: Exception | None = None,
        simulate_timeout: bool = False,
    ) -> None:
        self.result = result
        self.failure = failure
        self.simulate_timeout = simulate_timeout
        self.calls: list[CapturedRating] = []

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str:
        return "fake"

    @property
    def prompt_version(self) -> str:
        return "fake-v1"

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult:
        self.calls.append(CapturedRating(claim, evidence, context))
        if self.simulate_timeout:
            raise RaterTimeoutError("fake timeout")
        if self.failure is not None:
            raise self.failure
        return self.result

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class CapturedExtraction:
    raw_claim: str
    context: ExtractionContext


class FakeClaimExtractor:
    def __init__(self, result: ClaimExtraction, *, failure: Exception | None = None) -> None:
        self.result = result
        self.failure = failure
        self.calls: list[CapturedExtraction] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def extract(self, raw_claim: str, *, context: ExtractionContext) -> ClaimExtraction:
        self.calls.append(CapturedExtraction(raw_claim, context))
        if self.failure is not None:
            raise self.failure
        return self.result

    async def aclose(self) -> None:
        return None
