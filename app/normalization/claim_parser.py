"""Deterministic claim normalization.

This is the stage that makes everything downstream possible. Raw strings never
travel past here: the output is a :class:`NormalizedClaim` carrying the
attribute under assertion, the comparison operator, the claimed value, the
hedge, the polarity, and the time frame.

Classification is an ordered cascade of explicit matchers rather than a single
regex or a model call. Ordering encodes real precedence — "includes a 30-day
free trial" is a trial-duration claim even though it also matches the feature
pattern, and "free shipping on orders over $50" is a shipping claim even though
it contains money. Each matcher reports its own confidence, so a weak parse
escalates to the semantic rater instead of being decided on a guess.

An unparsed claim is not an error. It is a routing decision: ``is_parsed`` is
False, no deterministic rule fires, and the claim goes to the rater.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from time import perf_counter

from app.core.clock import Clock
from app.core.exceptions import InvalidClaimError
from app.core.pipeline_config import NormalizationConfig
from app.domain.enums import (
    Attribute,
    ClaimType,
    EntityType,
    ExtractionMethod,
    InventoryStatus,
    Operator,
    Qualifier,
)
from app.domain.interfaces import ClaimExtractor
from app.domain.models import (
    ClaimEntity,
    ExtractionContext,
    NormalizedClaim,
    TimeContext,
    VerificationRequest,
)
from app.normalization import negation, qualifiers, regions, units
from app.normalization import text as textutil
from app.normalization.features import FeatureResolver

# --------------------------------------------------------------------------- #
# Cue vocabularies
# --------------------------------------------------------------------------- #
_TRIAL_CUES = ("trial", "try it free", "free for", "trial period")
_SHIPPING_CUES = ("shipping", "shipped", "ships", "delivery", "postage", "freight")
_NO_SHIPPING_CHARGE_CUES = (
    "no shipping charge",
    "no shipping fee",
    "no delivery charge",
    "no delivery fee",
    "won't add anything to the bill",
    "will not add anything to the bill",
    "doesn't add anything to the bill",
    "does not add anything to the bill",
)
_PAID_SHIPPING_CUES = (
    "shipping charge applies",
    "shipping fee applies",
    "delivery charge applies",
    "delivery fee applies",
    "adds a charge",
    "adds a fee",
    "shipping is not free",
    "delivery is not free",
    "does not include free delivery",
    "does not include free shipping",
)
_DISCOUNT_CUES = ("off", "discount", "save", "reduced", "markdown", "sale price", "% back")
_MIN_PURCHASE_CUES = (
    "minimum purchase",
    "minimum order",
    "min purchase",
    "min order",
    "minimum spend",
    "spend at least",
    "orders over",
    "orders above",
    "purchase of at least",
    "minimum of",
)
_NO_MINIMUM_CUES = (
    "no minimum purchase",
    "no minimum order",
    "no minimum spend",
    "without a minimum",
    "no minimum",
)
_SUBSCRIPTION_CUES = (
    "per month",
    "a month",
    "/mo",
    "per mo",
    "monthly",
    "per year",
    "annually",
    "annual",
    "/yr",
    "per seat",
    "subscription",
    "billed",
    "billing",
    "recurring",
)
_DATE_CUES = (
    "through",
    "thru",
    "until",
    "till",
    "ends",
    "ending",
    "expires",
    "expiring",
    "expired",
    "valid",
    "runs",
    "starts",
    "starting on",
    "begins",
    "from",
    "between",
    "good until",
    "offer ends",
)
_PLAN_CUES = ("plan", "tier", "subscription", "seat", "license", "workspace")
_INCLUSION_CUES = (
    "includes",
    "include",
    "included",
    "comes with",
    "ships with",
    "features",
    "supports",
    "offers",
    "has",
    "have",
    "with",
    "bundled",
    "built in",
    "built-in",
)
_EXCLUSION_CUES = ("excludes", "exclude", "excluded", "without", "does not include", "lacks")

#: Availability phrases mapped to the reference's ``inventory_status`` values.
_AVAILABILITY_PHRASES: tuple[tuple[str, InventoryStatus], ...] = (
    ("out of stock", InventoryStatus.OUT_OF_STOCK),
    ("sold out", InventoryStatus.OUT_OF_STOCK),
    ("back in stock", InventoryStatus.IN_STOCK),
    ("in stock", InventoryStatus.IN_STOCK),
    ("available now", InventoryStatus.IN_STOCK),
    ("ships today", InventoryStatus.IN_STOCK),
    ("limited stock", InventoryStatus.LIMITED_STOCK),
    ("limited availability", InventoryStatus.LIMITED_STOCK),
    ("only a few left", InventoryStatus.LIMITED_STOCK),
    ("pre-order", InventoryStatus.PREORDER),
    ("preorder", InventoryStatus.PREORDER),
    ("backordered", InventoryStatus.PREORDER),
    ("discontinued", InventoryStatus.DISCONTINUED),
    ("no longer sold", InventoryStatus.DISCONTINUED),
)

_BILLING_PHRASES: tuple[tuple[str, str], ...] = (
    ("billed monthly", "monthly"),
    ("per month", "monthly"),
    ("a month", "monthly"),
    ("/mo", "monthly"),
    ("monthly", "monthly"),
    ("billed annually", "annual"),
    ("per year", "annual"),
    ("annually", "annual"),
    ("/yr", "annual"),
    ("yearly", "annual"),
    ("quarterly", "quarterly"),
    ("one-time", "one_time"),
    ("one time payment", "one_time"),
)

#: Verbs that separate the entity from the assertion, used to isolate a name
#: for lexical retrieval when no identifier was supplied.
_ASSERTION_VERBS = re.compile(
    r"\b(is|are|was|were|costs?|includes?|included|has|have|comes|ships?|gives?|offers?|"
    r"provides?|features?|supports?|comes with|comes without|excludes?|comes in|"
    r"starts?|starting|priced|comes to|runs?|expires?|ends?|does)\b"
)
_LEADING_DETERMINERS = re.compile(r"^(the|this|that|these|those|our|a|an|all)\s+", re.IGNORECASE)

_PRESENT_TENSE_CUES = ("today", "right now", "currently", "now", "this week", "at the moment")

# Narrow advertising speech acts that contain no factual proposition the
# verifier knows how to compare. This runs only after every factual matcher has
# declined, so "Buy it today for $20" remains a price claim rather than being
# rejected because it starts with an imperative.
_NON_VERIFIABLE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:buy|discover|do not miss)\b.+(?:today|now)?[!.]*$",
        r"^(?:premium quality with|great value from|you will love)\b.+[!.]*$",
        r"^(?:a smarter choice|an amazing deal on|best offer ever for)\b.+[!.]*$",
        r"^everything you want in\b.+[!.]*$",
    )
)

#: Below this length an extracted entity name is noise, and feeding it to
#: lexical retrieval would drag the match toward an arbitrary record.
_MIN_ENTITY_NAME_CHARS = 3


def _is_non_verifiable(text: str) -> bool:
    return any(pattern.match(text) for pattern in _NON_VERIFIABLE_PATTERNS)


@dataclass(slots=True)
class _Parse:
    """Intermediate classification result from one matcher."""

    claim_type: ClaimType
    attribute: Attribute
    operator: Operator = Operator.EQUALS
    value: bool | int | Decimal | float | str | None = None
    unit: str | None = None
    feature: str | None = None
    confidence: float = 0.9
    notes: list[str] = field(default_factory=list)


class DeterministicClaimNormalizer:
    """Parses claims into structured form without calling a model.

    Satisfies :class:`app.domain.interfaces.ClaimNormalizer`. The
    :class:`NormalizationConfig` it receives decides how much of this runs: the
    baseline profile disables parsing entirely, which is how the "hand the raw
    sentence to the model" arm is expressed without a second code path.
    """

    def __init__(
        self,
        *,
        config: NormalizationConfig,
        feature_resolver: FeatureResolver,
        clock: Clock,
        extractor: ClaimExtractor | None = None,
    ) -> None:
        self._config = config
        self._features = feature_resolver
        self._clock = clock
        self._extractor = extractor

    async def normalize(self, request: VerificationRequest) -> NormalizedClaim:
        cleaned = self._validate(request.claim)
        as_of = request.as_of or self._clock.today()

        if not self._config.deterministic_parsing:
            # Baseline arm: carry through only what is needed to retrieve a
            # record and time-box the claim. Everything else is the model's job.
            normalized = NormalizedClaim(
                raw_text=cleaned,
                entity=self._entity(request, cleaned),
                region=request.region,
                time_context=TimeContext(as_of=as_of, raw_expression=None),
                extraction_method=ExtractionMethod.DETERMINISTIC,
                parse_confidence=0.0,
                notes=("deterministic_parsing_disabled",),
            )
            return await self._maybe_extract(normalized, request=request, as_of=as_of)

        negation_match = (
            negation.detect_negation(cleaned)
            if self._config.negation_detection
            else negation.NegationMatch(negated=False)
        )
        qualifier_match = (
            qualifiers.detect_qualifier(cleaned) if self._config.qualifier_detection else None
        )
        qualifier = qualifier_match.qualifier if qualifier_match else Qualifier.NONE

        parse = self._classify(cleaned, negated=negation_match.negated, as_of=as_of)

        notes = [*parse.notes]
        if parse.claim_type is ClaimType.UNKNOWN and _is_non_verifiable(cleaned):
            notes.append("non_verifiable_commercial_proposition")
        if negation_match.cue:
            notes.append(f"negation_cue:{negation_match.cue}")
        if negation_match.double:
            # Double negation is exactly the construction deterministic parsing
            # should not be trusted with, so drop confidence and let the rater
            # take it.
            notes.append("double_negation")
        if qualifier_match:
            notes.append(f"qualifier_cue:{qualifier_match.cue}")

        operator = self._resolve_operator(parse, qualifier)
        confidence = parse.confidence
        if negation_match.double:
            confidence = min(confidence, 0.35)

        normalized = NormalizedClaim(
            raw_text=cleaned,
            entity=self._entity(request, cleaned),
            claim_type=parse.claim_type,
            attribute=parse.attribute,
            operator=operator,
            qualifier=qualifier,
            value=parse.value,
            unit=parse.unit,
            feature=parse.feature,
            negated=negation_match.negated,
            time_context=self._time_context(cleaned, as_of=as_of),
            region=self._region(request, cleaned),
            extraction_method=ExtractionMethod.DETERMINISTIC,
            parse_confidence=confidence,
            notes=tuple(notes),
        )
        return await self._maybe_extract(normalized, request=request, as_of=as_of)

    async def _maybe_extract(
        self,
        claim: NormalizedClaim,
        *,
        request: VerificationRequest,
        as_of: date,
    ) -> NormalizedClaim:
        """Use the extractor only when the deterministic parse is not trustworthy."""
        if (
            not self._config.llm_extraction_fallback
            or self._extractor is None
            or (
                claim.is_parsed
                and claim.parse_confidence >= self._config.llm_extraction_below_confidence
            )
        ):
            return claim

        extraction_started = perf_counter()
        extracted = await self._extractor.extract(
            claim.raw_text,
            context=ExtractionContext(evaluation_date=as_of, request_id=request.request_id),
        )
        extraction_latency_ms = (perf_counter() - extraction_started) * 1000
        entity = claim.entity.model_copy(
            update={"name": extracted.entity_name or claim.entity.name}
        )
        return NormalizedClaim(
            raw_text=claim.raw_text,
            entity=entity,
            claim_type=extracted.claim_type,
            attribute=extracted.attribute,
            operator=extracted.operator,
            qualifier=extracted.qualifier,
            value=extracted.value,
            unit=extracted.unit,
            feature=extracted.feature,
            negated=extracted.negated,
            time_context=claim.time_context,
            region=extracted.region or claim.region,
            extraction_method=ExtractionMethod.LLM_ASSISTED,
            parse_confidence=extracted.confidence,
            extraction_latency_ms=extraction_latency_ms,
            notes=(*claim.notes, "llm_extraction_fallback"),
        )

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #
    def _validate(self, claim: str) -> str:
        cleaned = textutil.clean(claim)
        if not cleaned:
            raise InvalidClaimError("Claim text is empty.")
        if len(cleaned) < self._config.min_claim_chars:
            raise InvalidClaimError(
                f"Claim text is too short (minimum {self._config.min_claim_chars} characters).",
                details={"length": len(cleaned)},
            )
        if len(cleaned) > self._config.max_claim_chars:
            raise InvalidClaimError(
                f"Claim text exceeds {self._config.max_claim_chars} characters.",
                details={"length": len(cleaned)},
            )
        if not any(char.isalnum() for char in cleaned):
            raise InvalidClaimError("Claim text contains no verifiable content.")
        return cleaned

    # ------------------------------------------------------------------ #
    # Classification cascade
    # ------------------------------------------------------------------ #
    def _classify(self, text: str, *, negated: bool, as_of: date) -> _Parse:
        folded = textutil.fold(text)
        money = units.parse_money(text)
        percent = units.parse_percent(text)

        for matcher in (
            self._match_trial,
            self._match_shipping,
            self._match_minimum_purchase,
            self._match_discount,
            # Region before availability: "not available in Canada" is a
            # geographic claim, and the availability matcher's negated-
            # "available" fallback would otherwise swallow it.
            self._match_region,
            self._match_availability,
            self._match_subscription,
            self._match_price,
            self._match_promotion_dates,
            self._match_feature,
        ):
            parse = matcher(text, folded, money, percent, negated, as_of)
            if parse is not None:
                return parse

        return _Parse(
            claim_type=ClaimType.UNKNOWN,
            attribute=Attribute.UNKNOWN,
            confidence=0.0,
            notes=["unclassified"],
        )

    def _match_trial(
        self,
        text: str,
        folded: str,
        _money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        days = units.parse_duration_days(text)
        explicit_trial_cue = any(cue in folded for cue in _TRIAL_CUES if cue != "free for")
        duration_free_cue = "free for" in folded and days is not None
        if not explicit_trial_cue and not duration_free_cue:
            return None

        if days is not None:
            return _Parse(
                claim_type=ClaimType.TRIAL_DURATION,
                attribute=Attribute.TRIAL_DAYS,
                value=days,
                unit="days",
                confidence=0.95,
                notes=["trial_duration_parsed"],
            )
        if negated:
            # "does not include a free trial" asserts trial_days == 0.
            return _Parse(
                claim_type=ClaimType.TRIAL_DURATION,
                attribute=Attribute.TRIAL_DAYS,
                value=0,
                unit="days",
                confidence=0.8,
                notes=["negated_trial_implies_zero_days"],
            )
        # "includes a free trial" with no duration: real assertion, but the
        # only checkable part is that a trial exists at all.
        return _Parse(
            claim_type=ClaimType.TRIAL_DURATION,
            attribute=Attribute.TRIAL_DAYS,
            operator=Operator.GREATER_THAN,
            value=0,
            unit="days",
            confidence=0.7,
            notes=["trial_without_duration"],
        )

    def _match_shipping(
        self,
        text: str,
        folded: str,
        money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if not any(cue in folded for cue in _SHIPPING_CUES):
            return None

        if "no shipping cost" in folded or any(cue in folded for cue in _NO_SHIPPING_CHARGE_CUES):
            return _Parse(
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.FREE_SHIPPING,
                operator=Operator.IS_TRUE,
                value=True,
                confidence=0.95,
                notes=["no_cost_shipping_assertion"],
            )
        if (
            "free" in folded
            or "shipping included" in folded
            or "complimentary shipping" in folded
            or "complimentary delivery" in folded
        ):
            return _Parse(
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.FREE_SHIPPING,
                operator=Operator.IS_FALSE if negated else Operator.IS_TRUE,
                value=not negated,
                confidence=0.95,
                notes=["free_shipping_assertion"],
            )
        if any(cue in folded for cue in _PAID_SHIPPING_CUES):
            return _Parse(
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.FREE_SHIPPING,
                operator=Operator.IS_FALSE,
                value=False,
                confidence=0.95,
                notes=["paid_shipping_assertion"],
            )
        if money is not None:
            return _Parse(
                claim_type=ClaimType.SHIPPING,
                attribute=Attribute.SHIPPING_COST,
                value=money.amount,
                unit=money.currency,
                confidence=0.9,
                notes=["shipping_cost_parsed"],
            )
        # A delivery-related service (for example, "weekend delivery") is not
        # an assertion that shipping is free.  Keep the proposition untyped so
        # unrelated free_shipping evidence cannot be treated as capable proof.
        return _Parse(
            claim_type=ClaimType.UNKNOWN,
            attribute=Attribute.UNKNOWN,
            confidence=0.0,
            notes=["shipping_without_value"],
        )

    def _match_minimum_purchase(
        self,
        _text: str,
        folded: str,
        money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if not any(cue in folded for cue in _MIN_PURCHASE_CUES):
            return None
        # "no minimum purchase" asserts the requirement is *absent*, i.e. a
        # threshold of zero. It is handled here by phrase rather than via the
        # negation flag, because the negation detector deliberately classifies
        # it as a positive assertion about the field.
        if any(cue in folded for cue in _NO_MINIMUM_CUES) or (negated and money is None):
            return _Parse(
                claim_type=ClaimType.MINIMUM_PURCHASE,
                attribute=Attribute.MINIMUM_PURCHASE,
                value=0,
                confidence=0.88,
                notes=["no_minimum_purchase_assertion"],
            )
        if money is None:
            return None
        return _Parse(
            claim_type=ClaimType.MINIMUM_PURCHASE,
            attribute=Attribute.MINIMUM_PURCHASE,
            value=money.amount,
            unit=money.currency,
            confidence=0.9,
            notes=["minimum_purchase_parsed"],
        )

    def _match_discount(
        self,
        _text: str,
        folded: str,
        _money: units.MoneyValue | None,
        percent: Decimal | None,
        _negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if percent is None:
            return None
        if not any(cue in folded for cue in _DISCOUNT_CUES):
            return None
        return _Parse(
            claim_type=ClaimType.DISCOUNT,
            attribute=Attribute.DISCOUNT_PERCENT,
            value=percent,
            unit="percent",
            confidence=0.93,
            notes=["discount_percent_parsed"],
        )

    def _match_availability(
        self,
        _text: str,
        folded: str,
        _money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        for phrase, status in _AVAILABILITY_PHRASES:
            if phrase in folded:
                return _Parse(
                    claim_type=ClaimType.AVAILABILITY,
                    attribute=Attribute.INVENTORY_STATUS,
                    operator=Operator.NOT_EQUALS if negated else Operator.EQUALS,
                    value=status.value,
                    confidence=0.9,
                    notes=[f"availability_phrase:{phrase}"],
                )
        if negated and ("available" in folded or "in stock" in folded):
            return _Parse(
                claim_type=ClaimType.AVAILABILITY,
                attribute=Attribute.INVENTORY_STATUS,
                operator=Operator.NOT_EQUALS,
                value=InventoryStatus.IN_STOCK.value,
                confidence=0.8,
                notes=["negated_availability"],
            )
        return None

    def _match_region(
        self,
        text: str,
        _folded: str,
        _money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if not regions.has_region_cue(text):
            return None
        region = regions.detect_region(text, require_cue=True)
        if region is None:
            return None
        return _Parse(
            claim_type=ClaimType.GEO_ELIGIBILITY,
            attribute=Attribute.ELIGIBLE_REGIONS,
            operator=Operator.EXCLUDES if negated else Operator.INCLUDES,
            value=region,
            confidence=0.9,
            notes=[f"region_detected:{region}"],
        )

    def _match_subscription(
        self,
        _text: str,
        folded: str,
        money: units.MoneyValue | None,
        _percent: Decimal | None,
        _negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if not any(cue in folded for cue in _SUBSCRIPTION_CUES):
            return None

        if money is not None:
            return _Parse(
                claim_type=ClaimType.SUBSCRIPTION_TERMS,
                attribute=Attribute.SUBSCRIPTION_PRICE,
                value=money.amount,
                unit=money.currency,
                confidence=0.92,
                notes=["subscription_price_parsed"],
            )
        for phrase, period in _BILLING_PHRASES:
            if phrase in folded:
                return _Parse(
                    claim_type=ClaimType.SUBSCRIPTION_TERMS,
                    attribute=Attribute.BILLING_PERIOD,
                    value=period,
                    confidence=0.85,
                    notes=[f"billing_period:{period}"],
                )
        return None

    def _match_price(
        self,
        _text: str,
        folded: str,
        money: units.MoneyValue | None,
        _percent: Decimal | None,
        _negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        if money is None:
            return None
        # A price claim about something described as a plan is a subscription
        # price; the reference stores those in a different column.
        is_plan = any(cue in folded for cue in _PLAN_CUES)
        return _Parse(
            claim_type=ClaimType.SUBSCRIPTION_TERMS if is_plan else ClaimType.PRICE,
            attribute=Attribute.SUBSCRIPTION_PRICE if is_plan else Attribute.PRICE,
            value=money.amount,
            unit=money.currency,
            confidence=0.92,
            notes=["price_parsed", "plan_context" if is_plan else "product_context"],
        )

    def _match_promotion_dates(
        self,
        text: str,
        folded: str,
        _money: units.MoneyValue | None,
        _percent: Decimal | None,
        _negated: bool,
        as_of: date,
    ) -> _Parse | None:
        if not any(cue in folded for cue in _DATE_CUES):
            return None
        start, end = units.month_day_span(text, as_of=as_of)
        if end is None and start is None:
            if "expired" in folded or "no longer valid" in folded:
                return _Parse(
                    claim_type=ClaimType.PROMOTION_DATES,
                    attribute=Attribute.OFFER_END,
                    operator=Operator.LESS_THAN,
                    value=as_of.isoformat(),
                    confidence=0.85,
                    notes=["expired_assertion"],
                )
            return None

        # Confidence stays high even for a bare "June 30": the year is not part
        # of the comparison when it was inferred (see TimeContext.year_inferred),
        # so the residual uncertainty is handled by the rule rather than by
        # discounting the parse.
        if end is not None:
            return _Parse(
                claim_type=ClaimType.PROMOTION_DATES,
                attribute=Attribute.OFFER_END,
                value=end.isoformat(),
                unit="date",
                confidence=0.92,
                notes=["offer_end_parsed"],
            )
        return _Parse(
            claim_type=ClaimType.PROMOTION_DATES,
            attribute=Attribute.OFFER_START,
            value=start.isoformat() if start else None,
            unit="date",
            confidence=0.92,
            notes=["offer_start_parsed"],
        )

    def _match_feature(
        self,
        text: str,
        folded: str,
        _money: units.MoneyValue | None,
        _percent: Decimal | None,
        negated: bool,
        _as_of: date,
    ) -> _Parse | None:
        match = self._features.resolve(text)
        if match is None:
            return None

        explicit_exclusion = any(cue in folded for cue in _EXCLUSION_CUES)
        has_inclusion_cue = any(cue in folded for cue in _INCLUSION_CUES)
        if not (explicit_exclusion or has_inclusion_cue):
            # The feature is named but nothing is asserted about it.
            return None

        excluding = negated or explicit_exclusion
        return _Parse(
            claim_type=ClaimType.FEATURE_EXCLUSION if excluding else ClaimType.FEATURE_INCLUSION,
            attribute=Attribute.EXCLUDED_FEATURES if excluding else Attribute.INCLUDED_FEATURES,
            operator=Operator.EXCLUDES if excluding else Operator.INCLUDES,
            value=match.key,
            feature=match.key,
            confidence=min(0.93, 0.6 + 0.35 * match.confidence),
            notes=[f"feature_match:{match.matched_via}"],
        )

    # ------------------------------------------------------------------ #
    # Cross-cutting fields
    # ------------------------------------------------------------------ #
    def _resolve_operator(self, parse: _Parse, qualifier: Qualifier) -> Operator:
        """Let an explicit hedge override the matcher's default comparison.

        Only for value comparisons: a hedge must not rewrite set membership
        (``INCLUDES``) or boolean assertions, where "up to" is meaningless.
        """
        if qualifier is Qualifier.NONE:
            return parse.operator
        if parse.operator in (
            Operator.INCLUDES,
            Operator.EXCLUDES,
            Operator.IS_TRUE,
            Operator.IS_FALSE,
        ):
            return parse.operator
        return qualifiers.operator_for(qualifier)

    def _entity(self, request: VerificationRequest, text: str) -> ClaimEntity:
        """Identify the subject of the claim.

        An explicit identifier always wins. Otherwise a candidate name is
        carved out of the text for lexical retrieval; imperfect extraction is
        acceptable here because the retriever scores candidates and abstains
        below a threshold rather than trusting this string.
        """
        if request.reference_id:
            return ClaimEntity(type=EntityType.UNKNOWN, id=request.reference_id)
        if request.sku:
            return ClaimEntity(type=EntityType.UNKNOWN, id=request.sku)

        entity_type = (
            EntityType.PLAN
            if any(cue in textutil.fold(text) for cue in _PLAN_CUES)
            else EntityType.UNKNOWN
        )
        return ClaimEntity(type=entity_type, name=self._extract_entity_name(text))

    @staticmethod
    def _extract_entity_name(text: str) -> str | None:
        cleaned = textutil.clean(text)
        split = _ASSERTION_VERBS.split(cleaned, maxsplit=1)
        candidate = split[0].strip(" ,.;:") if split else ""
        candidate = _LEADING_DETERMINERS.sub("", candidate).strip()
        # A single stopword-ish fragment is worse than nothing.
        return candidate if len(candidate) >= _MIN_ENTITY_NAME_CHARS else None

    def _region(self, request: VerificationRequest, text: str) -> str | None:
        if request.region:
            return regions.normalize_region(request.region) or request.region.upper()
        return regions.detect_region(text, require_cue=True)

    def _time_context(self, text: str, *, as_of: date) -> TimeContext:
        folded = textutil.fold(text)
        has_date_cue = any(cue in folded for cue in _DATE_CUES)
        start, end = units.month_day_span(text, as_of=as_of) if has_date_cue else (None, None)
        return TimeContext(
            as_of=as_of,
            start=start,
            end=end,
            is_present_tense=any(cue in folded for cue in _PRESENT_TENSE_CUES) or not has_date_cue,
            year_inferred=(start is not None or end is not None)
            and not units.date_year_is_explicit(text),
            raw_expression=text if has_date_cue else None,
        )
