"""The deterministic rule library.

Each rule owns one comparison and is independently testable. Two policies run
through all of them and are worth stating once:

**Absent is not false.** A field the reference does not populate yields
``INSUFFICIENT_EVIDENCE``, never ``CONTRADICTED``. The only fields treated as
exhaustive are ``eligible_regions`` (an allow-list — if a region is not on it,
the offer is not available there) and ``excluded_features`` (an explicit
negative list). Everything else is open-world.

**Exactness where the data is exact.** Money and dates are compared as
``Decimal`` and ``date``, with tolerance bands that exist to absorb
representation noise rather than to paper over real differences.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.domain.enums import (
    Attribute,
    ClaimType,
    EntityType,
    InventoryStatus,
    Operator,
    Qualifier,
    ReasonCode,
)
from app.domain.models import NormalizedClaim, ReferenceEvidence, RuleOutcome
from app.normalization import units
from app.normalization.features import humanize
from app.rules.base import (
    EXACT_CONFIDENCE,
    INFERRED_CONFIDENCE,
    MONEY_ATTRIBUTES,
    NUMERIC_ATTRIBUTES,
    TOLERANCE_CONFIDENCE,
    Rule,
    RuleContext,
    abstain,
    as_decimal,
    compare_numeric,
    contradicted,
    insufficient,
    mismatch_code,
    supported,
    tolerance_for,
)

#: A claim that the item is available is satisfied by limited stock: it is in
#: stock, just not much of it. Contradicting that phrasing would be pedantry
#: reported as a factual error.
_IN_STOCK_EQUIVALENTS: frozenset[str] = frozenset(
    {InventoryStatus.IN_STOCK.value, InventoryStatus.LIMITED_STOCK.value}
)

#: Countries covered by the catalog's ``EU`` bloc code, so a claim about
#: Germany is satisfied by an offer listed for the EU.
_EU_MEMBERS: frozenset[str] = frozenset(
    {"DE", "FR", "IT", "ES", "NL", "BE", "IE", "PT", "AT", "FI", "SE", "DK", "PL"}
)


class EntityResolutionRule:
    """No record resolved -> abstain with ``REFERENCE_NOT_FOUND``.

    Runs first and terminates the pipeline. Without a source of truth there is
    nothing to contradict, and this is the single most important place not to
    let a model guess: in the baseline arm, unresolved entities were the
    largest contributor to false positives.
    """

    rule_id = "entity_resolution"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return True

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        if not evidence.is_empty:
            return abstain(self.rule_id, detail=f"resolved via {evidence.match_method}")
        return insufficient(
            self.rule_id,
            codes=(ReasonCode.REFERENCE_NOT_FOUND,),
            confidence=EXACT_CONFIDENCE,
            detail=f"no reference record resolved ({evidence.match_method})",
        )


class FieldPresenceRule:
    """The answering column is unset -> ``INSUFFICIENT_EVIDENCE``.

    This is the rule that keeps "we don't publish that" from being reported as
    "that is false".
    """

    rule_id = "field_presence"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            not evidence.is_empty
            and claim.is_parsed
            and evidence.effective_attribute is not Attribute.UNKNOWN
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        attribute = evidence.effective_attribute
        if evidence.has(attribute):
            return abstain(self.rule_id, detail=f"{attribute.value} present in reference")

        # Shipping is the one place an absent field is genuinely derivable:
        # a stated shipping cost of exactly zero *is* free shipping.
        if attribute is Attribute.FREE_SHIPPING and evidence.has(Attribute.SHIPPING_COST):
            return abstain(self.rule_id, detail="free_shipping derivable from shipping_cost")

        return insufficient(
            self.rule_id,
            codes=(ReasonCode.FIELD_NOT_IN_REFERENCE, ReasonCode.INSUFFICIENT_EVIDENCE),
            confidence=EXACT_CONFIDENCE,
            detail=f"reference record {evidence.record_id} does not populate {attribute.value}",
        )


class CurrencyRule:
    """A claim in the wrong currency is not a price disagreement, it is a unit error."""

    rule_id = "currency_match"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            evidence.effective_attribute in MONEY_ATTRIBUTES
            and claim.unit is not None
            and claim.unit.upper() in {code.upper() for code in units.CURRENCY_CODES}
            and evidence.has(Attribute.CURRENCY)
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        claimed = str(claim.unit).upper()
        reference = str(evidence.value_of(Attribute.CURRENCY)).upper()
        if claimed == reference:
            return abstain(self.rule_id, detail=f"currency matches ({reference})")
        return contradicted(
            self.rule_id,
            codes=(ReasonCode.CURRENCY_MISMATCH,),
            confidence=EXACT_CONFIDENCE,
            detail=f"claim is in {claimed} but reference is priced in {reference}",
            observed=reference,
            expected=claimed,
        )


class NumericComparisonRule:
    """Prices, percentages, costs, thresholds and trial lengths.

    Handles the tolerance band and the hedge semantics together, so "up to
    20%", "approximately $200" and a bare "$199.99" all route through one
    comparison with the operator and tolerance the claim actually implies.
    """

    rule_id = "numeric_comparison"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            evidence.effective_attribute in NUMERIC_ATTRIBUTES
            and evidence.has(evidence.effective_attribute)
            and as_decimal(claim.value) is not None
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        attribute = evidence.effective_attribute
        claimed = as_decimal(claim.value)
        reference = as_decimal(evidence.value_of(attribute))
        if claimed is None or reference is None:
            return abstain(self.rule_id, detail="non-numeric value")

        tolerance = tolerance_for(
            attribute, reference=reference, qualifier=claim.qualifier, config=context.config
        )
        matched = compare_numeric(
            claimed=claimed,
            reference=reference,
            operator=claim.operator,
            tolerance=tolerance,
        )

        unit = "%" if attribute is Attribute.DISCOUNT_PERCENT else ""
        detail = (
            f"claim asserts {attribute.value} {claim.operator.value} {claimed}{unit}; "
            f"reference is {reference}{unit} (tolerance {tolerance})"
        )

        if not matched:
            return contradicted(
                self.rule_id,
                codes=(mismatch_code(attribute),),
                confidence=EXACT_CONFIDENCE,
                detail=detail,
                observed=str(reference),
                expected=str(claimed),
            )

        codes: tuple[ReasonCode, ...]
        if claim.qualifier is not Qualifier.NONE:
            codes = (ReasonCode.QUALIFIER_SATISFIED,)
            confidence = TOLERANCE_CONFIDENCE
        elif reference == claimed:
            codes = (ReasonCode.EXACT_MATCH,)
            confidence = EXACT_CONFIDENCE
        else:
            codes = (ReasonCode.WITHIN_TOLERANCE,)
            confidence = TOLERANCE_CONFIDENCE

        return supported(
            self.rule_id,
            codes=codes,
            confidence=confidence,
            detail=detail,
            observed=str(reference),
            expected=str(claimed),
        )


class FreeShippingRule:
    """Boolean shipping claims, including the cost-derived case."""

    rule_id = "free_shipping"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return evidence.effective_attribute is Attribute.FREE_SHIPPING and isinstance(
            claim.value, bool
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        claimed = bool(claim.value)
        reference = evidence.value_of(Attribute.FREE_SHIPPING)
        confidence = EXACT_CONFIDENCE
        source = "free_shipping"

        if reference is None:
            cost = as_decimal(evidence.value_of(Attribute.SHIPPING_COST))
            if cost is None:
                return abstain(self.rule_id, detail="no shipping information in reference")
            # Definitional, not inferential: a zero shipping cost is free
            # shipping. Still recorded at a lower confidence and with the
            # source named, so the audit trail shows it was derived.
            reference = cost == Decimal(0)
            confidence = INFERRED_CONFIDENCE
            source = f"derived from shipping_cost={cost}"

        if bool(reference) == claimed:
            return supported(
                self.rule_id,
                codes=(ReasonCode.EXACT_MATCH,),
                confidence=confidence,
                detail=f"free_shipping is {reference} ({source})",
                observed=bool(reference),
                expected=claimed,
            )
        return contradicted(
            self.rule_id,
            codes=(ReasonCode.SHIPPING_MISMATCH,),
            confidence=confidence,
            detail=f"claim asserts free_shipping={claimed} but reference is {reference} ({source})",
            observed=bool(reference),
            expected=claimed,
        )


class AvailabilityRule:
    """Inventory-status claims."""

    rule_id = "availability"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            evidence.effective_attribute is Attribute.INVENTORY_STATUS
            and evidence.has(Attribute.INVENTORY_STATUS)
            and isinstance(claim.value, str)
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        claimed = str(claim.value)
        reference = str(evidence.value_of(Attribute.INVENTORY_STATUS))

        # "Available" is the broad assertion and is satisfied by either full
        # or limited stock.  An explicit "limited stock" assertion is more
        # specific and must match exactly; equivalence is directional.
        if claimed == InventoryStatus.IN_STOCK.value:
            matched = reference in _IN_STOCK_EQUIVALENTS
        else:
            matched = reference == claimed

        if claim.operator is Operator.NOT_EQUALS:
            matched = not matched

        detail = (
            f"claim asserts inventory_status {claim.operator.value} {claimed}; "
            f"reference is {reference}"
        )
        if matched:
            return supported(
                self.rule_id,
                codes=(ReasonCode.EXACT_MATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=detail,
                observed=reference,
                expected=claimed,
            )
        return contradicted(
            self.rule_id,
            codes=(ReasonCode.AVAILABILITY_MISMATCH,),
            confidence=EXACT_CONFIDENCE,
            detail=detail,
            observed=reference,
            expected=claimed,
        )


class FeatureMembershipRule:
    """Feature inclusion and exclusion claims.

    The three-way outcome is the point. A feature on ``included_features`` and
    a claim of inclusion agree; a feature on ``excluded_features`` and a claim
    of inclusion genuinely conflict; a feature on *neither* list is unknown, and
    saying "that is false" would be inventing a fact. That last branch is why
    the reference data maintains an explicit exclusion list at all.
    """

    rule_id = "feature_membership"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return claim.claim_type in (
            ClaimType.FEATURE_INCLUSION,
            ClaimType.FEATURE_EXCLUSION,
        ) and bool(claim.feature)

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        feature = str(claim.feature)
        included = {str(item) for item in (evidence.value_of(Attribute.INCLUDED_FEATURES) or ())}
        excluded = {str(item) for item in (evidence.value_of(Attribute.EXCLUDED_FEATURES) or ())}

        if not included and not excluded:
            return insufficient(
                self.rule_id,
                codes=(ReasonCode.FIELD_NOT_IN_REFERENCE, ReasonCode.INSUFFICIENT_EVIDENCE),
                confidence=EXACT_CONFIDENCE,
                detail="reference record lists no features",
            )

        claims_inclusion = claim.operator is not Operator.EXCLUDES
        readable = humanize(feature)

        if feature in included:
            if claims_inclusion:
                return supported(
                    self.rule_id,
                    codes=(ReasonCode.FEATURE_PRESENT,),
                    confidence=EXACT_CONFIDENCE,
                    detail=f"'{readable}' is in included_features",
                    observed=feature,
                    expected=feature,
                )
            return contradicted(
                self.rule_id,
                codes=(ReasonCode.FEATURE_MISMATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=f"claim excludes '{readable}' but it is in included_features",
                observed=feature,
                expected=feature,
            )

        if feature in excluded:
            if claims_inclusion:
                return contradicted(
                    self.rule_id,
                    codes=(ReasonCode.FEATURE_MISMATCH,),
                    confidence=EXACT_CONFIDENCE,
                    detail=f"claim includes '{readable}' but it is in excluded_features",
                    observed=feature,
                    expected=feature,
                )
            return supported(
                self.rule_id,
                codes=(ReasonCode.FEATURE_ABSENT,),
                confidence=EXACT_CONFIDENCE,
                detail=f"'{readable}' is in excluded_features",
                observed=feature,
                expected=feature,
            )

        # Named in neither list: the source of truth is silent.
        return insufficient(
            self.rule_id,
            codes=(ReasonCode.INSUFFICIENT_EVIDENCE,),
            confidence=TOLERANCE_CONFIDENCE,
            detail=f"'{readable}' appears in neither included_features nor excluded_features",
        )


class RegionEligibilityRule:
    """Geographic eligibility.

    ``eligible_regions`` is an allow-list, so absence *is* a contradiction —
    the one place in this engine where a missing entry means "false" rather
    than "unknown", because that is what an allow-list means.
    """

    rule_id = "region_eligibility"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            claim.claim_type is ClaimType.GEO_ELIGIBILITY
            and isinstance(claim.value, str)
            and evidence.has(Attribute.ELIGIBLE_REGIONS)
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        claimed = str(claim.value).upper()
        regions = {
            str(item).upper() for item in (evidence.value_of(Attribute.ELIGIBLE_REGIONS) or ())
        }

        eligible = claimed in regions or (claimed in _EU_MEMBERS and "EU" in regions)
        if claim.operator is Operator.EXCLUDES:
            eligible = not eligible

        detail = (
            f"claim asserts {claimed} {claim.operator.value} eligible_regions; "
            f"reference lists {sorted(regions)}"
        )
        if eligible:
            return supported(
                self.rule_id,
                codes=(ReasonCode.EXACT_MATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=detail,
                observed=sorted(regions),
                expected=claimed,
            )
        return contradicted(
            self.rule_id,
            codes=(ReasonCode.REGION_MISMATCH,),
            confidence=EXACT_CONFIDENCE,
            detail=detail,
            observed=sorted(regions),
            expected=claimed,
        )


class BillingPeriodRule:
    """Billing-cadence claims ("billed annually")."""

    rule_id = "billing_period"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        return (
            evidence.effective_attribute is Attribute.BILLING_PERIOD
            and evidence.has(Attribute.BILLING_PERIOD)
            and isinstance(claim.value, str)
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        claimed = str(claim.value)
        reference = str(evidence.value_of(Attribute.BILLING_PERIOD))
        detail = f"claim asserts billing_period={claimed}; reference is {reference}"
        if claimed == reference:
            return supported(
                self.rule_id,
                codes=(ReasonCode.EXACT_MATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=detail,
                observed=reference,
                expected=claimed,
            )
        return contradicted(
            self.rule_id,
            codes=(ReasonCode.TERMS_MISMATCH,),
            confidence=EXACT_CONFIDENCE,
            detail=detail,
            observed=reference,
            expected=claimed,
        )


class PromotionWindowRule:
    """Dates asserted *by* the claim, checked against the reference window.

    Covers both a standalone date claim ("valid through September 30") and the
    date half of a compound claim ("20% off through September 30"), because it
    keys off ``time_context`` rather than off the claim type.
    """

    rule_id = "promotion_window"

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        asserts_dates = claim.time_context.end is not None or claim.time_context.start is not None
        expired_assertion = (
            claim.claim_type is ClaimType.PROMOTION_DATES and claim.operator is Operator.LESS_THAN
        )
        has_window = evidence.has(Attribute.OFFER_END) or evidence.has(Attribute.OFFER_START)
        return has_window and (asserts_dates or expired_assertion)

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        grace = timedelta(days=context.config.date_grace_days)
        reference_end = _as_date(evidence.value_of(Attribute.OFFER_END))
        reference_start = _as_date(evidence.value_of(Attribute.OFFER_START))

        if (
            claim.claim_type is ClaimType.PROMOTION_DATES
            and claim.operator is Operator.LESS_THAN
            and reference_end is not None
        ):
            # "That promotion has expired."
            if reference_end < context.as_of:
                return supported(
                    self.rule_id,
                    codes=(ReasonCode.OFFER_EXPIRED,),
                    confidence=EXACT_CONFIDENCE,
                    detail=f"offer ended {reference_end} before {context.as_of}",
                    observed=reference_end.isoformat(),
                    expected="expired",
                )
            return contradicted(
                self.rule_id,
                codes=(ReasonCode.DATE_MISMATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=f"claim says expired but offer runs to {reference_end}",
                observed=reference_end.isoformat(),
                expected="expired",
            )

        year_inferred = claim.time_context.year_inferred
        for label, claimed, reference in (
            ("end", claim.time_context.end, reference_end),
            ("start", claim.time_context.start, reference_start),
        ):
            if claimed is None or reference is None:
                continue
            matched = _dates_agree(
                claimed=claimed,
                reference=reference,
                grace_days=grace.days,
                year_inferred=year_inferred,
            )
            scope = "month/day" if year_inferred else "full date"
            detail = (
                f"claimed {label} {claimed} vs reference offer_{label} "
                f"{reference} (compared by {scope})"
            )
            if matched:
                return supported(
                    self.rule_id,
                    codes=(ReasonCode.EXACT_MATCH,),
                    confidence=EXACT_CONFIDENCE,
                    detail=detail,
                    observed=reference.isoformat(),
                    expected=claimed.isoformat(),
                )
            return contradicted(
                self.rule_id,
                codes=(ReasonCode.DATE_MISMATCH,),
                confidence=EXACT_CONFIDENCE,
                detail=detail,
                observed=reference.isoformat(),
                expected=claimed.isoformat(),
            )

        return abstain(self.rule_id, detail="no comparable dates")


class ActiveOfferRule:
    """Guard: a present-tense promotional claim about a window that is not open.

    Only ever contradicts; it never affirms. If the window is open this rule
    stays silent and the value comparison decides, which keeps a valid discount
    claim from being "supported" for the wrong reason.

    It deliberately does not fire when the claim states its own dates — "20%
    off through September 30" is a claim *about* a window, not an assertion
    that the window is open right now.
    """

    rule_id = "active_offer_window"

    _GUARDED_TYPES = frozenset({ClaimType.DISCOUNT})
    _OFFER_ONLY_TYPES = frozenset(
        {ClaimType.SHIPPING, ClaimType.MINIMUM_PURCHASE, ClaimType.GEO_ELIGIBILITY}
    )

    def applies(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> bool:
        if claim.time_context.end or claim.time_context.start:
            return False
        if not claim.time_context.is_present_tense:
            return False
        if not (evidence.has(Attribute.OFFER_END) or evidence.has(Attribute.OFFER_START)):
            return False
        if claim.claim_type in self._GUARDED_TYPES:
            return True
        return (
            claim.claim_type in self._OFFER_ONLY_TYPES and evidence.entity_type is EntityType.OFFER
        )

    def evaluate(
        self, claim: NormalizedClaim, evidence: ReferenceEvidence, context: RuleContext
    ) -> RuleOutcome:
        grace = context.config.date_grace_days
        end = _as_date(evidence.value_of(Attribute.OFFER_END))
        start = _as_date(evidence.value_of(Attribute.OFFER_START))

        if end is not None and end < context.as_of - timedelta(days=grace):
            return contradicted(
                self.rule_id,
                codes=(ReasonCode.OFFER_EXPIRED, ReasonCode.DATE_MISMATCH),
                confidence=EXACT_CONFIDENCE,
                detail=(
                    f"claim presents the offer as active but it ended {end} "
                    f"(evaluated {context.as_of})"
                ),
                observed=end.isoformat(),
                expected=f"active on {context.as_of.isoformat()}",
            )
        if start is not None and start > context.as_of + timedelta(days=grace):
            return contradicted(
                self.rule_id,
                codes=(ReasonCode.OFFER_NOT_STARTED, ReasonCode.DATE_MISMATCH),
                confidence=EXACT_CONFIDENCE,
                detail=(
                    f"claim presents the offer as active but it starts {start} "
                    f"(evaluated {context.as_of})"
                ),
                observed=start.isoformat(),
                expected=f"active on {context.as_of.isoformat()}",
            )
        return abstain(self.rule_id, detail="offer window is open")


def _dates_agree(*, claimed: date, reference: date, grace_days: int, year_inferred: bool) -> bool:
    """Compare two dates, ignoring the year when the claim never stated one.

    "valid through June 30" does not say *which* June 30. Comparing the
    inferred year against the reference would report a mismatch caused by our
    own inference rather than by the claim, so the claimed month/day is
    re-anchored into the reference's year before comparing.
    """
    if year_inferred:
        anchored = _safe_date(reference.year, claimed.month, claimed.day)
        if anchored is None:
            return (claimed.month, claimed.day) == (reference.month, reference.day)
        return abs((reference - anchored).days) <= grace_days
    return abs((reference - claimed).days) <= grace_days


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _as_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def default_rules() -> tuple[Rule, ...]:
    """The rule set, in evaluation order.

    Ordering matters twice: resolution and presence gates run before any
    comparison, and the active-offer guard runs last so a value mismatch is
    reported as a value mismatch rather than as an expiry.
    """
    return (
        EntityResolutionRule(),
        FieldPresenceRule(),
        CurrencyRule(),
        NumericComparisonRule(),
        FreeShippingRule(),
        AvailabilityRule(),
        FeatureMembershipRule(),
        RegionEligibilityRule(),
        BillingPeriodRule(),
        PromotionWindowRule(),
        ActiveOfferRule(),
    )
