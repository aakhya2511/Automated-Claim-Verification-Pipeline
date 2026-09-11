"""Structural interfaces for every replaceable pipeline component.

These are :class:`typing.Protocol` definitions rather than ABCs so that
implementations stay decoupled from the abstraction (no import of the interface
required to satisfy it) while still being statically checked. Concrete
collaborators are wired in :mod:`app.api.dependencies`.

The JSONL-backed repository and the Postgres repository we would swap in later
differ only behind :class:`ReferenceRepository`; nothing in normalization,
rules, or decisioning knows how records are stored.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.pipeline_config import PipelineConfig
from app.domain.models import (
    ClaimExtraction,
    DecisionOutcome,
    ExtractionContext,
    NormalizedClaim,
    RaterResult,
    RatingContext,
    ReferenceEvidence,
    ReferenceRecord,
    RuleEngineResult,
    VerificationFailure,
    VerificationRequest,
    VerificationResult,
)


@runtime_checkable
class ReferenceRepository(Protocol):
    """Read access to the authoritative commercial dataset.

    Async even for the in-memory implementation: the call sites are written
    once against an awaitable contract so moving to Postgres/asyncpg is a
    constructor change rather than a refactor of every caller.
    """

    async def get_by_id(self, record_id: str) -> ReferenceRecord | None: ...

    async def get_by_sku(self, sku: str) -> ReferenceRecord | None: ...

    async def search(self, query: str, *, limit: int = 5) -> list[tuple[ReferenceRecord, float]]:
        """Return ``(record, score)`` candidates ranked by lexical relevance."""
        ...

    async def count(self) -> int: ...

    async def health_check(self) -> bool: ...


@runtime_checkable
class ClaimNormalizer(Protocol):
    """Turns a raw claim string into a structured, comparable claim."""

    async def normalize(self, request: VerificationRequest) -> NormalizedClaim: ...


@runtime_checkable
class EvidenceRetriever(Protocol):
    """Resolves a claim to the record and field projection it should be judged against."""

    async def retrieve(
        self, claim: NormalizedClaim, request: VerificationRequest
    ) -> ReferenceEvidence: ...


@runtime_checkable
class RuleEngine(Protocol):
    """Deterministic verification layer; may abstain and escalate."""

    def evaluate(self, claim: NormalizedClaim, evidence: ReferenceEvidence) -> RuleEngineResult: ...


@runtime_checkable
class LLMRater(Protocol):
    """Semantic rater used only for claims deterministic rules cannot settle."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def prompt_version(self) -> str: ...

    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class ClaimExtractor(Protocol):
    """LLM-assisted parser. It describes a claim and never verifies it."""

    async def extract(self, raw_claim: str, *, context: ExtractionContext) -> ClaimExtraction: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class DecisionEngine(Protocol):
    """Reconciles rule and rater signals into a final calibrated verdict."""

    def decide(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        rules: RuleEngineResult,
        rater: RaterResult | None,
    ) -> DecisionOutcome: ...


@runtime_checkable
class VerificationService(Protocol):
    """Orchestrates the full pipeline for one or many claims."""

    @property
    def config(self) -> PipelineConfig: ...

    async def verify(self, request: VerificationRequest) -> VerificationResult: ...

    async def verify_batch(
        self, requests: list[VerificationRequest]
    ) -> list[VerificationResult | VerificationFailure]: ...
