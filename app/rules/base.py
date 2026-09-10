"""Rule interface and shared comparison helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.core.pipeline_config import RuleConfig
from app.domain.enums import Attribute, Operator, Qualifier, ReasonCode, Verdict
from app.domain.models import NormalizedClaim, ReferenceEvidence, RuleOutcome


@dataclass(frozen=True, slots=True)
class RuleContext:
    """Everything a rule needs beyond the claim and the evidence.

    ``as_of`` is passed explicitly rather than read from a clock so that date
    boundaries are reproducible in tests and in evaluation runs.
    """

    config: RuleConfig
    as_of: date


@runtime_checkable
class Rule(Protocol):
    """A single deterministic check.

    ``applies`` is separate from ``evaluate`` so the engine can report which
    rules were even considered — part of making a verdict explainable — and so
    a rule never has to encode "not my business" as a verdict.
    """

    @property
    def rule_id(self) -> str: ...

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool: ...

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome: ...


#: Confidence assigned to deterministic outcomes. These are high because the
#: comparison itself is exact; the uncertainty in this pipeline lives in
#: parsing and entity resolution, and is accounted for separately by combining
#: rule confidence with the claim's parse confidence.
EXACT_CONFIDENCE = 0.99
TOLERANCE_CONFIDENCE = 0.97
INFERRED_CONFIDENCE = 0.95

#: Mismatch code per reference column, finer-grained than the per-claim-type
#: default so error analysis can separate a wrong subscription price from a
#: wrong product price.
ATTRIBUTE_MISMATCH_CODE: dict[Attribute, ReasonCode] = {
    Attribute.PRICE: ReasonCode.PRICE_MISMATCH,
    Attribute.SUBSCRIPTION_PRICE: ReasonCode.SUBSCRIPTION_PRICE_MISMATCH,
    Attribute.DISCOUNT_PERCENT: ReasonCode.DISCOUNT_MISMATCH,
    Attribute.SHIPPING_COST: ReasonCode.SHIPPING_MISMATCH,
    Attribute.FREE_SHIPPING: ReasonCode.SHIPPING_MISMATCH,
    Attribute.MINIMUM_PURCHASE: ReasonCode.MINIMUM_PURCHASE_MISMATCH,
    Attribute.TRIAL_DAYS: ReasonCode.TRIAL_DURATION_MISMATCH,
    Attribute.INVENTORY_STATUS: ReasonCode.AVAILABILITY_MISMATCH,
    Attribute.INCLUDED_FEATURES: ReasonCode.FEATURE_MISMATCH,
    Attribute.EXCLUDED_FEATURES: ReasonCode.FEATURE_MISMATCH,
    Attribute.ELIGIBLE_REGIONS: ReasonCode.REGION_MISMATCH,
    Attribute.OFFER_START: ReasonCode.DATE_MISMATCH,
    Attribute.OFFER_END: ReasonCode.DATE_MISMATCH,
    Attribute.BILLING_PERIOD: ReasonCode.TERMS_MISMATCH,
    Attribute.CURRENCY: ReasonCode.CURRENCY_MISMATCH,
}

#: Columns the numeric comparison rule owns.
NUMERIC_ATTRIBUTES: frozenset[Attribute] = frozenset(
    {
        Attribute.PRICE,
        Attribute.SUBSCRIPTION_PRICE,
        Attribute.SHIPPING_COST,
        Attribute.MINIMUM_PURCHASE,
        Attribute.DISCOUNT_PERCENT,
        Attribute.TRIAL_DAYS,
    }
)

MONEY_ATTRIBUTES: frozenset[Attribute] = frozenset(
    {
        Attribute.PRICE,
        Attribute.SUBSCRIPTION_PRICE,
        Attribute.SHIPPING_COST,
        Attribute.MINIMUM_PURCHASE,
    }
)


def mismatch_code(attribute: Attribute) -> ReasonCode:
    return ATTRIBUTE_MISMATCH_CODE.get(attribute, ReasonCode.TERMS_MISMATCH)


def as_decimal(value: object) -> Decimal | None:
    """Coerce a numeric claim or reference value to ``Decimal``.

    ``bool`` is rejected explicitly: it is a subclass of ``int``, and letting
    ``True`` become ``Decimal("1")`` would send a free-shipping claim through
    the numeric comparison path.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return None


def tolerance_for(
    attribute: Attribute,
    *,
    reference: Decimal,
    qualifier: Qualifier,
    config: RuleConfig,
) -> Decimal:
    """Tolerance band for one comparison.

    Money gets an absolute cent tolerance plus an optional relative component;
    the absolute part absorbs representation noise (19.99 vs 19.990) without
    coming anywhere near accepting 149 vs 199. An explicit "approximately"
    hedge widens the band relative to the reference value — but only when the
    claim actually said so.
    """
    if attribute is Attribute.TRIAL_DAYS:
        base = Decimal(config.trial_days_tolerance)
    elif attribute is Attribute.DISCOUNT_PERCENT:
        base = Decimal(str(config.percent_absolute_tolerance))
    else:
        base = Decimal(str(config.price_absolute_tolerance)) + Decimal(
            str(config.price_relative_tolerance)
        ) * abs(reference)

    if qualifier is Qualifier.APPROXIMATELY:
        widened = Decimal(str(config.approximate_relative_tolerance)) * abs(reference)
        return max(base, widened)
    return base


def compare_numeric(
    *,
    claimed: Decimal,
    reference: Decimal,
    operator: Operator,
    tolerance: Decimal,
) -> bool:
    """Evaluate ``reference <operator> claimed`` within ``tolerance``.

    Note the direction: the claim states a value and the reference is the
    truth, so an ``AT_MOST`` claim ("up to 20% off") is satisfied when the
    *reference* does not exceed the claimed bound.
    """
    difference = reference - claimed
    match operator:
        case Operator.EQUALS:
            return abs(difference) <= tolerance
        case Operator.NOT_EQUALS:
            return abs(difference) > tolerance
        case Operator.AT_MOST:
            return reference <= claimed + tolerance
        case Operator.AT_LEAST:
            return reference >= claimed - tolerance
        case Operator.GREATER_THAN:
            return reference > claimed + tolerance
        case Operator.LESS_THAN:
            return reference < claimed - tolerance
        case _:
            return abs(difference) <= tolerance


def supported(
    rule_id: str,
    *,
    codes: tuple[ReasonCode, ...],
    confidence: float,
    detail: str,
    observed: object = None,
    expected: object = None,
) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule_id,
        fired=True,
        verdict=Verdict.SUPPORTED,
        reason_codes=codes,
        confidence=confidence,
        terminal=True,
        detail=detail,
        observed=observed,
        expected=expected,
    )


def contradicted(
    rule_id: str,
    *,
    codes: tuple[ReasonCode, ...],
    confidence: float,
    detail: str,
    observed: object = None,
    expected: object = None,
) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule_id,
        fired=True,
        verdict=Verdict.CONTRADICTED,
        reason_codes=codes,
        confidence=confidence,
        terminal=True,
        detail=detail,
        observed=observed,
        expected=expected,
    )


def insufficient(
    rule_id: str,
    *,
    codes: tuple[ReasonCode, ...],
    confidence: float,
    detail: str,
) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule_id,
        fired=True,
        verdict=Verdict.INSUFFICIENT_EVIDENCE,
        reason_codes=codes,
        confidence=confidence,
        terminal=True,
        detail=detail,
    )


def abstain(rule_id: str, *, detail: str) -> RuleOutcome:
    """The rule applied but reached no conclusion; the claim escalates."""
    return RuleOutcome(rule_id=rule_id, fired=False, terminal=False, detail=detail)
