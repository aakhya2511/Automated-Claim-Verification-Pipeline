"""Closed vocabularies shared by every stage of the pipeline.

Every value that crosses a module boundary (verdicts, claim categories, reason
codes, audit paths) is an enum rather than a bare string so that the rule
engine, the LLM rater, the evaluation harness and the API schema cannot drift
apart silently.
"""

from __future__ import annotations

from enum import StrEnum


class Verdict(StrEnum):
    """Final classification of a claim against the reference source of truth."""

    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_CLAIM = "INVALID_CLAIM"


class EntityType(StrEnum):
    """Kind of commercial entity a claim is about."""

    PRODUCT = "product"
    PLAN = "plan"
    OFFER = "offer"
    UNKNOWN = "unknown"


class ClaimType(StrEnum):
    """Semantic category of the claim, used for routing and error analysis."""

    PRICE = "price"
    DISCOUNT = "discount"
    SHIPPING = "shipping"
    AVAILABILITY = "availability"
    FEATURE_INCLUSION = "feature_inclusion"
    FEATURE_EXCLUSION = "feature_exclusion"
    SUBSCRIPTION_TERMS = "subscription_terms"
    TRIAL_DURATION = "trial_duration"
    PROMOTION_DATES = "promotion_dates"
    GEO_ELIGIBILITY = "geo_eligibility"
    MINIMUM_PURCHASE = "minimum_purchase"
    UNKNOWN = "unknown"


class Operator(StrEnum):
    """Comparison requested by the claim between its value and the reference."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    AT_MOST = "at_most"
    AT_LEAST = "at_least"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    INCLUDES = "includes"
    EXCLUDES = "excludes"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"
    WITHIN_RANGE = "within_range"


class Qualifier(StrEnum):
    """Hedging language attached to a claimed value.

    This distinction is load-bearing: ``up to 20% off`` is satisfied by a 15%
    discount while a bare ``20% off`` is not, and ``starting at $99`` is a lower
    bound rather than an equality.
    """

    NONE = "none"
    UP_TO = "up_to"
    STARTING_AT = "starting_at"
    APPROXIMATELY = "approximately"
    AT_LEAST = "at_least"
    AT_MOST = "at_most"
    UNLIMITED = "unlimited"


class VerificationPath(StrEnum):
    """Which stage produced the final verdict. Recorded for every request."""

    DETERMINISTIC = "DETERMINISTIC"
    LLM_RATER = "LLM_RATER"
    DETERMINISTIC_WITH_LLM_EXTRACTION = "DETERMINISTIC_WITH_LLM_EXTRACTION"
    LLM_EXTRACTION_AND_RATER = "LLM_EXTRACTION_AND_RATER"
    FAILED_SAFE = "FAILED_SAFE"


class EscalationReason(StrEnum):
    """Why deterministic verification could not safely terminate."""

    LOW_PARSE_CONFIDENCE = "LOW_PARSE_CONFIDENCE"
    NON_TERMINAL_RULE_RESULT = "NON_TERMINAL_RULE_RESULT"
    AMBIGUOUS_SEMANTICS = "AMBIGUOUS_SEMANTICS"
    UNSUPPORTED_DETERMINISTIC_CLAIM_TYPE = "UNSUPPORTED_DETERMINISTIC_CLAIM_TYPE"
    INSUFFICIENT_DETERMINISTIC_COVERAGE = "INSUFFICIENT_DETERMINISTIC_COVERAGE"


class ExtractionMethod(StrEnum):
    """How a raw claim string became a :class:`NormalizedClaim`."""

    DETERMINISTIC = "deterministic"
    LLM_ASSISTED = "llm_assisted"
    CLIENT_SUPPLIED = "client_supplied"
    FAILED = "failed"


class InventoryStatus(StrEnum):
    IN_STOCK = "in_stock"
    LIMITED_STOCK = "limited_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    DISCONTINUED = "discontinued"


class BillingPeriod(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    ONE_TIME = "one_time"


class Attribute(StrEnum):
    """Comparable field of a reference record.

    Values match :class:`app.domain.models.ReferenceRecord` field names exactly
    so that attribute lookup is a direct, typo-proof projection of the record.
    """

    PRICE = "price"
    CURRENCY = "currency"
    DISCOUNT_PERCENT = "discount_percent"
    SHIPPING_COST = "shipping_cost"
    FREE_SHIPPING = "free_shipping"
    INVENTORY_STATUS = "inventory_status"
    TRIAL_DAYS = "trial_days"
    SUBSCRIPTION_PRICE = "subscription_price"
    BILLING_PERIOD = "billing_period"
    OFFER_START = "offer_start"
    OFFER_END = "offer_end"
    ELIGIBLE_REGIONS = "eligible_regions"
    MINIMUM_PURCHASE = "minimum_purchase"
    INCLUDED_FEATURES = "included_features"
    EXCLUDED_FEATURES = "excluded_features"
    TERMS = "terms"
    UNKNOWN = "unknown"


class ReasonCode(StrEnum):
    """Machine-readable justification attached to a verdict.

    The first block is the canonical mismatch vocabulary shared with the LLM
    rater (it is injected verbatim into the prompt, so it must stay small and
    stable). The later blocks are internal codes emitted only by deterministic
    stages; they exist to give error analysis enough resolution to attribute a
    false positive to a specific mechanism.
    """

    # --- shared with the LLM rater -----------------------------------------
    PRICE_MISMATCH = "PRICE_MISMATCH"
    DISCOUNT_MISMATCH = "DISCOUNT_MISMATCH"
    FEATURE_MISMATCH = "FEATURE_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    REGION_MISMATCH = "REGION_MISMATCH"
    SHIPPING_MISMATCH = "SHIPPING_MISMATCH"
    AVAILABILITY_MISMATCH = "AVAILABILITY_MISMATCH"
    TERMS_MISMATCH = "TERMS_MISMATCH"
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
    AMBIGUOUS_LANGUAGE = "AMBIGUOUS_LANGUAGE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    UNSUPPORTED_INFERENCE = "UNSUPPORTED_INFERENCE"

    # --- deterministic sub-categories (finer-grained mismatch attribution) --
    TRIAL_DURATION_MISMATCH = "TRIAL_DURATION_MISMATCH"
    SUBSCRIPTION_PRICE_MISMATCH = "SUBSCRIPTION_PRICE_MISMATCH"
    MINIMUM_PURCHASE_MISMATCH = "MINIMUM_PURCHASE_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    OFFER_NOT_STARTED = "OFFER_NOT_STARTED"

    # --- support / abstain codes -------------------------------------------
    EXACT_MATCH = "EXACT_MATCH"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"
    QUALIFIER_SATISFIED = "QUALIFIER_SATISFIED"
    FEATURE_PRESENT = "FEATURE_PRESENT"
    FEATURE_ABSENT = "FEATURE_ABSENT"
    FIELD_NOT_IN_REFERENCE = "FIELD_NOT_IN_REFERENCE"
    UNPARSEABLE_CLAIM = "UNPARSEABLE_CLAIM"
    EMPTY_CLAIM = "EMPTY_CLAIM"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    LOW_CONFIDENCE_ABSTAIN = "LOW_CONFIDENCE_ABSTAIN"
    RATER_UNAVAILABLE = "RATER_UNAVAILABLE"
    RATER_INVALID_OUTPUT = "RATER_INVALID_OUTPUT"


#: Reason codes the LLM rater is permitted to emit. Anything outside this set is
#: rejected during response validation instead of being passed downstream.
RATER_REASON_CODES: frozenset[ReasonCode] = frozenset(
    {
        ReasonCode.PRICE_MISMATCH,
        ReasonCode.DISCOUNT_MISMATCH,
        ReasonCode.FEATURE_MISMATCH,
        ReasonCode.DATE_MISMATCH,
        ReasonCode.REGION_MISMATCH,
        ReasonCode.SHIPPING_MISMATCH,
        ReasonCode.AVAILABILITY_MISMATCH,
        ReasonCode.TERMS_MISMATCH,
        ReasonCode.REFERENCE_NOT_FOUND,
        ReasonCode.AMBIGUOUS_LANGUAGE,
        ReasonCode.INSUFFICIENT_EVIDENCE,
        ReasonCode.UNSUPPORTED_INFERENCE,
    }
)

#: Default mismatch code per claim category, used when a deterministic rule
#: detects a contradiction but has no more specific code to report.
CLAIM_TYPE_MISMATCH_CODE: dict[ClaimType, ReasonCode] = {
    ClaimType.PRICE: ReasonCode.PRICE_MISMATCH,
    ClaimType.DISCOUNT: ReasonCode.DISCOUNT_MISMATCH,
    ClaimType.SHIPPING: ReasonCode.SHIPPING_MISMATCH,
    ClaimType.AVAILABILITY: ReasonCode.AVAILABILITY_MISMATCH,
    ClaimType.FEATURE_INCLUSION: ReasonCode.FEATURE_MISMATCH,
    ClaimType.FEATURE_EXCLUSION: ReasonCode.FEATURE_MISMATCH,
    ClaimType.SUBSCRIPTION_TERMS: ReasonCode.SUBSCRIPTION_PRICE_MISMATCH,
    ClaimType.TRIAL_DURATION: ReasonCode.TRIAL_DURATION_MISMATCH,
    ClaimType.PROMOTION_DATES: ReasonCode.DATE_MISMATCH,
    ClaimType.GEO_ELIGIBILITY: ReasonCode.REGION_MISMATCH,
    ClaimType.MINIMUM_PURCHASE: ReasonCode.MINIMUM_PURCHASE_MISMATCH,
    ClaimType.UNKNOWN: ReasonCode.TERMS_MISMATCH,
}
