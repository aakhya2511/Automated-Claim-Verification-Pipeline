"""Domain models for claims, reference data, evidence and verdicts.

These types are the contract between pipeline stages. They are deliberately
immutable (``frozen=True``) wherever a stage hands data to the next one, so a
downstream component cannot quietly mutate the audit trail.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.domain.enums import (
    Attribute,
    BillingPeriod,
    ClaimType,
    EntityType,
    EscalationReason,
    ExtractionMethod,
    InventoryStatus,
    Operator,
    Qualifier,
    ReasonCode,
    Verdict,
    VerificationPath,
)

#: Money is carried as ``Decimal`` end-to-end. Binary floats cannot represent
#: values like 19.99 exactly, and cent-level rounding drift would show up as
#: spurious price contradictions.
Money = Annotated[Decimal, Field(max_digits=12, decimal_places=4)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Percent = Annotated[float, Field(ge=0.0, le=100.0)]


class DomainModel(BaseModel):
    """Base for immutable domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------- #
# Reference data (source of truth)
# --------------------------------------------------------------------------- #
class ReferenceRecord(DomainModel):
    """One authoritative commercial record: a product, plan, or offer.

    Different entity types populate different subsets of fields; a physical
    product has no ``trial_days`` and a subscription plan has no ``sku``. An
    absent field means "the source of truth says nothing here", which the rule
    engine must translate to ``INSUFFICIENT_EVIDENCE`` rather than a
    contradiction.
    """

    record_id: str = Field(min_length=1, max_length=64)
    entity_type: EntityType

    # identity / naming
    product_id: str | None = None
    sku: str | None = None
    product_name: str | None = None
    plan_name: str | None = None
    brand: str | None = None
    aliases: tuple[str, ...] = ()

    # pricing
    price: Money | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    discount_percent: Percent | None = None
    minimum_purchase: Money | None = None

    # fulfilment
    shipping_cost: Money | None = None
    free_shipping: bool | None = None
    inventory_status: InventoryStatus | None = None

    # subscription
    subscription_price: Money | None = None
    billing_period: BillingPeriod | None = None
    trial_days: int | None = Field(default=None, ge=0, le=3650)

    # promotion window
    offer_start: date | None = None
    offer_end: date | None = None
    eligible_regions: tuple[str, ...] = ()

    # capabilities
    included_features: tuple[str, ...] = ()
    excluded_features: tuple[str, ...] = ()
    terms: str | None = Field(default=None, max_length=2000)

    updated_at: datetime

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value

    @model_validator(mode="after")
    def _validate_offer_window(self) -> Self:
        if self.offer_start and self.offer_end and self.offer_end < self.offer_start:
            raise ValueError("offer_end must not precede offer_start")
        return self

    @property
    def display_name(self) -> str:
        return self.product_name or self.plan_name or self.record_id

    def get_attribute(self, attribute: Attribute) -> Any | None:
        """Return the reference value for ``attribute``, or ``None`` if unset.

        ``None`` is intentionally indistinguishable from "field does not apply
        to this entity type": both mean the source of truth is silent, and both
        must lead to an abstention rather than a contradiction.
        """
        if attribute is Attribute.UNKNOWN:
            return None
        value = getattr(self, attribute.value, None)
        if isinstance(value, tuple) and not value:
            return None
        return value

    def has_attribute(self, attribute: Attribute) -> bool:
        return self.get_attribute(attribute) is not None


# --------------------------------------------------------------------------- #
# Claim representation
# --------------------------------------------------------------------------- #
class ClaimEntity(DomainModel):
    """The commercial entity a claim refers to, as understood from the text."""

    type: EntityType = EntityType.UNKNOWN
    id: str | None = None
    name: str | None = None


class TimeContext(DomainModel):
    """Temporal framing of a claim.

    ``as_of`` is the instant the claim is evaluated against (defaults to request
    time), while ``start``/``end`` capture dates asserted *by* the claim, e.g.
    "20% off through September 30".
    """

    as_of: date | None = None
    start: date | None = None
    end: date | None = None
    is_present_tense: bool = True
    #: True when the claim stated a month and day but no year, so the year in
    #: ``start``/``end`` was inferred relative to ``as_of``. Date rules compare
    #: month and day only in that case, which removes the inference from the
    #: comparison instead of hoping it was right.
    year_inferred: bool = False
    raw_expression: str | None = None


class NormalizedClaim(DomainModel):
    """Structured form of a natural-language claim.

    Raw strings never travel past normalization. Every later stage — rules,
    evidence selection, the LLM prompt, evaluation — consumes this type, which
    is what makes deterministic short-circuiting and reproducible evaluation
    possible.
    """

    raw_text: str = Field(min_length=1)
    entity: ClaimEntity = ClaimEntity()
    claim_type: ClaimType = ClaimType.UNKNOWN
    attribute: Attribute = Attribute.UNKNOWN
    operator: Operator = Operator.EQUALS
    qualifier: Qualifier = Qualifier.NONE

    #: ``bool`` is listed first deliberately. Under pydantic's smart union a
    #: later ``bool`` would let ``True`` coerce into ``Decimal("1")``, which
    #: would turn a free-shipping claim into a price comparison.
    value: bool | int | Money | float | str | None = None
    unit: str | None = None
    feature: str | None = None

    negated: bool = False
    time_context: TimeContext = TimeContext()
    region: str | None = None

    extraction_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC
    parse_confidence: Confidence = 1.0
    extraction_latency_ms: float = Field(default=0.0, ge=0.0)
    notes: tuple[str, ...] = ()

    @property
    def is_parsed(self) -> bool:
        """True when the claim has enough structure for deterministic rules."""
        return (
            self.claim_type is not ClaimType.UNKNOWN
            and self.attribute is not Attribute.UNKNOWN
            and self.extraction_method is not ExtractionMethod.FAILED
        )


class ClaimExtraction(DomainModel):
    """Schema the LLM must satisfy on the structured-extraction path.

    Kept separate from :class:`NormalizedClaim` so that model output is
    validated against a narrow, purely-descriptive schema before it is merged
    into the internal representation.
    """

    claim_type: ClaimType
    attribute: Attribute
    operator: Operator = Operator.EQUALS
    qualifier: Qualifier = Qualifier.NONE
    value: float | bool | str | None = None
    unit: str | None = Field(default=None, max_length=16)
    feature: str | None = Field(default=None, max_length=120)
    negated: bool = False
    entity_name: str | None = Field(default=None, max_length=200)
    region: str | None = Field(default=None, max_length=8)
    confidence: Confidence = 0.5


class ExtractionContext(DomainModel):
    """Explicit context allowed to influence parsing (never verification)."""

    evaluation_date: date
    request_id: str | None = None


class VerificationRequest(DomainModel):
    """Service-level input, decoupled from the HTTP schema.

    The API layer maps its wire model onto this so the verification service can
    also be driven by the evaluation harness and benchmarks without a server.
    """

    claim: str
    reference_id: str | None = None
    sku: str | None = None
    region: str | None = None
    as_of: date | None = None
    request_id: str | None = None
    allow_llm: bool = True


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
class ReferenceEvidence(DomainModel):
    """The slice of the source of truth considered for one claim.

    ``fields`` is the projection actually shown to the rule engine and the LLM.
    Sending the whole record would bloat the prompt, add latency, and give the
    model unrelated numbers to confuse with the claimed one.
    """

    record_id: str | None = None
    entity_type: EntityType = EntityType.UNKNOWN
    display_name: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    #: Which reference column actually answers this claim. Usually the claim's
    #: own attribute, but retrieval may redirect it — a "$99" claim about a
    #: subscription plan has to be checked against ``subscription_price``,
    #: because plans do not populate ``price``.
    effective_attribute: Attribute = Attribute.UNKNOWN
    match_method: str = "unmatched"
    match_score: Confidence = 0.0
    candidate_ids: tuple[str, ...] = ()
    record_updated_at: datetime | None = None
    reference_version: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(frozen=True, extra="forbid")

    @property
    def is_empty(self) -> bool:
        return self.record_id is None or not self.fields

    def value_of(self, attribute: Attribute) -> Any | None:
        """Look up a projected field. Absent means the evidence is silent."""
        return self.fields.get(attribute.value)

    def has(self, attribute: Attribute) -> bool:
        return self.fields.get(attribute.value) is not None


# --------------------------------------------------------------------------- #
# Stage outputs
# --------------------------------------------------------------------------- #
class RuleOutcome(DomainModel):
    """Result of evaluating a single deterministic rule."""

    rule_id: str
    fired: bool
    verdict: Verdict | None = None
    reason_codes: tuple[ReasonCode, ...] = ()
    confidence: Confidence = 0.0
    terminal: bool = False
    detail: str | None = Field(default=None, max_length=400)
    observed: Any | None = None
    expected: Any | None = None


class RuleEngineResult(DomainModel):
    """Aggregate of all rules evaluated for a claim.

    ``verdict is None`` means the deterministic layer abstained and the claim
    must be escalated to the semantic rater.
    """

    verdict: Verdict | None = None
    confidence: Confidence = 0.0
    reason_codes: tuple[ReasonCode, ...] = ()
    outcomes: tuple[RuleOutcome, ...] = ()
    decisive_rule_id: str | None = None
    escalate_to_rater: bool = True

    @property
    def is_terminal(self) -> bool:
        return self.verdict is not None and not self.escalate_to_rater


class RaterResult(DomainModel):
    """Validated output of the LLM semantic rater plus its audit metadata."""

    verdict: Verdict
    confidence: Confidence
    reason_codes: tuple[ReasonCode, ...] = ()
    explanation: str = Field(default="", max_length=600)
    metadata: RaterMetadata | None = None
    trace: RatingTrace | None = None
    cached: bool = False
    degraded: bool = False


class TokenUsage(DomainModel):
    """Provider-neutral token accounting when a provider reports it."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class RaterMetadata(DomainModel):
    """Stable experiment identity for one semantic rating."""

    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=40)
    schema_version: str = Field(min_length=1, max_length=40)
    temperature: float = Field(ge=0.0, le=2.0)


class RatingContext(DomainModel):
    """Request context supplied explicitly rather than inferred by a model."""

    evaluation_date: date
    request_id: str | None = None
    trace_id: str | None = None


class RatingTrace(DomainModel):
    """Safe, typed audit record; deliberately excludes raw provider content."""

    request_id: str | None = None
    trace_id: str
    metadata: RaterMetadata
    attempt_count: int = Field(ge=1)
    provider_latency_ms: float = Field(ge=0.0)
    total_rater_latency_ms: float = Field(ge=0.0)
    verdict: Verdict
    confidence: Confidence
    reason_codes: tuple[ReasonCode, ...] = ()
    usage: TokenUsage | None = None


class LatencyBreakdown(BaseModel):
    """Per-stage wall-clock cost of a single verification, in milliseconds."""

    model_config = ConfigDict(extra="forbid")

    normalization: float = 0.0
    extraction: float = 0.0
    retrieval: float = 0.0
    rules: float = 0.0
    rating: float = 0.0
    decision: float = 0.0
    total: float = 0.0

    def rounded(self, digits: int = 3) -> LatencyBreakdown:
        return LatencyBreakdown(
            **{key: round(value, digits) for key, value in self.model_dump().items()}
        )


class AuditMetadata(DomainModel):
    """Everything needed to explain and reproduce a verdict after the fact."""

    config_name: str
    pipeline_version: str
    llm_invoked: bool = False
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    rater_degraded: bool = False
    cache_hit: bool = False
    rules_evaluated: int = 0
    decisive_rule_id: str | None = None
    extraction_method: ExtractionMethod = ExtractionMethod.DETERMINISTIC
    escalation_reason: EscalationReason | None = None
    reference_version: str | None = None


class EscalationDecision(DomainModel):
    """Centralized, auditable semantic-routing decision."""

    required: bool
    reason: EscalationReason | None = None
    detail: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _reason_matches_requirement(self) -> Self:
        if self.required and self.reason is None:
            raise ValueError("required escalation must include a reason")
        if not self.required and self.reason is not None:
            raise ValueError("non-required escalation cannot include a reason")
        return self


class DecisionOutcome(DomainModel):
    """Conservative final decision before service audit assembly."""

    verdict: Verdict
    confidence: Confidence
    reason_codes: tuple[ReasonCode, ...] = ()
    explanation: str = Field(default="", max_length=600)


class VerificationResult(DomainModel):
    """The auditable outcome of one claim verification."""

    request_id: str
    verdict: Verdict
    confidence: Confidence
    reason_codes: tuple[ReasonCode, ...] = ()
    explanation: str = ""
    verification_path: VerificationPath
    escalation: EscalationDecision = EscalationDecision(required=False)
    normalized_claim: NormalizedClaim | None = None
    evidence: ReferenceEvidence = ReferenceEvidence()
    rule_outcomes: tuple[RuleOutcome, ...] = ()
    rater_result: RaterResult | None = None
    latency_ms: LatencyBreakdown = LatencyBreakdown()
    audit: AuditMetadata

    model_config = ConfigDict(frozen=True, extra="forbid")


class VerificationFailure(DomainModel):
    """Execution failure kept separate from semantic verdicts, mainly for batches."""

    request_id: str
    code: str
    message: str
    status_code: int = Field(ge=400, le=599)
    latency_ms: float = Field(default=0.0, ge=0.0)
