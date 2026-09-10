"""Claim normalization: classification, values, hedges, polarity, time frame."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.core.clock import FixedClock
from app.core.exceptions import InvalidClaimError
from app.core.pipeline_config import NormalizationConfig
from app.domain.enums import Attribute, ClaimType, EntityType, Operator, Qualifier
from app.domain.models import VerificationRequest
from app.normalization.claim_parser import DeterministicClaimNormalizer
from app.normalization.features import FeatureResolver, vocabulary_from_records

from tests.conftest import FIXED_NOW


@pytest.fixture
def normalizer(repository) -> DeterministicClaimNormalizer:
    return DeterministicClaimNormalizer(
        config=NormalizationConfig(),
        feature_resolver=FeatureResolver(vocabulary_from_records(repository.all_records())),
        clock=FixedClock(moment=FIXED_NOW),
    )


@pytest.fixture
def baseline_normalizer(repository) -> DeterministicClaimNormalizer:
    return DeterministicClaimNormalizer(
        config=NormalizationConfig(deterministic_parsing=False),
        feature_resolver=FeatureResolver(vocabulary_from_records(repository.all_records())),
        clock=FixedClock(moment=FIXED_NOW),
    )


async def parse(normalizer: DeterministicClaimNormalizer, claim: str, **kwargs):
    return await normalizer.normalize(VerificationRequest(claim=claim, **kwargs))


class TestInputValidation:
    @pytest.mark.parametrize("claim", ["", "   ", "\n\t"])
    async def test_rejects_empty_claims(self, normalizer, claim: str) -> None:
        with pytest.raises(InvalidClaimError):
            await parse(normalizer, claim)

    async def test_rejects_too_short(self, normalizer) -> None:
        with pytest.raises(InvalidClaimError, match="too short"):
            await parse(normalizer, "$")

    async def test_rejects_symbol_only_claims(self, normalizer) -> None:
        with pytest.raises(InvalidClaimError, match="no verifiable content"):
            await parse(normalizer, "!!!???")

    async def test_rejects_overlong_claims(self, normalizer) -> None:
        with pytest.raises(InvalidClaimError, match="exceeds"):
            await parse(normalizer, "the price is $99. " * 200)

    async def test_accepts_a_long_but_legal_claim(self, normalizer) -> None:
        claim = "Regarding the current promotion, " * 10 + "the price is $199.99."
        result = await parse(normalizer, claim)
        assert result.claim_type is ClaimType.PRICE

    async def test_unicode_claim_is_normalized_not_rejected(self, normalizer) -> None:
        result = await parse(normalizer, "The Café Blend Grinder is \u2018$89\u2019 today.")
        assert result.claim_type is ClaimType.PRICE
        assert result.value == Decimal("89")


class TestPriceClaims:
    async def test_parses_a_simple_price(self, normalizer) -> None:
        result = await parse(normalizer, "AirPods Pro are $199 today.")
        assert result.claim_type is ClaimType.PRICE
        assert result.attribute is Attribute.PRICE
        assert result.operator is Operator.EQUALS
        assert result.value == Decimal("199")
        assert result.unit == "USD"
        assert result.is_parsed is True

    async def test_starting_at_is_a_lower_bound(self, normalizer) -> None:
        result = await parse(normalizer, "The laptop is starting at $999.")
        assert result.qualifier is Qualifier.STARTING_AT
        assert result.operator is Operator.AT_LEAST

    async def test_approximately_keeps_equality(self, normalizer) -> None:
        result = await parse(normalizer, "The headphones cost approximately $200.")
        assert result.qualifier is Qualifier.APPROXIMATELY
        assert result.operator is Operator.EQUALS

    async def test_plan_priced_claim_routes_to_subscription_price(self, normalizer) -> None:
        # The reference stores plan pricing in a different column than product
        # pricing, so the attribute has to differ or evidence lookup misses.
        result = await parse(normalizer, "The Pro plan costs $99 per month.")
        assert result.claim_type is ClaimType.SUBSCRIPTION_TERMS
        assert result.attribute is Attribute.SUBSCRIPTION_PRICE
        assert result.value == Decimal("99")

    async def test_zero_price_claim(self, normalizer) -> None:
        result = await parse(normalizer, "The starter add-on is $0.")
        assert result.value == Decimal("0")
        assert result.is_parsed is True


class TestDiscountClaims:
    async def test_parses_percentage_discount(self, normalizer) -> None:
        result = await parse(normalizer, "Offer ABC gives 20% off.")
        assert result.claim_type is ClaimType.DISCOUNT
        assert result.attribute is Attribute.DISCOUNT_PERCENT
        assert result.value == Decimal("20")

    async def test_up_to_becomes_an_upper_bound(self, normalizer) -> None:
        result = await parse(normalizer, "Save up to 20% on this offer.")
        assert result.claim_type is ClaimType.DISCOUNT
        assert result.qualifier is Qualifier.UP_TO
        assert result.operator is Operator.AT_MOST

    async def test_percentage_without_a_discount_cue_is_not_a_discount(self, normalizer) -> None:
        result = await parse(normalizer, "The shirt is 100% cotton.")
        assert result.claim_type is not ClaimType.DISCOUNT

    async def test_compound_discount_and_date_captures_both(self, normalizer) -> None:
        # The primary assertion is the discount, but the date bound is retained
        # in the time context so the promotion-window rule can also check it.
        result = await parse(normalizer, "Offer ABC gives 20% off through September 30.")
        assert result.claim_type is ClaimType.DISCOUNT
        assert result.value == Decimal("20")
        assert result.time_context.end == date(2026, 9, 30)


class TestShippingClaims:
    async def test_free_shipping_is_a_boolean_claim(self, normalizer) -> None:
        result = await parse(normalizer, "Product X includes free shipping.")
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.operator is Operator.IS_TRUE
        assert result.value is True

    async def test_shipping_is_free_for_entity_is_not_a_trial(self, normalizer) -> None:
        result = await parse(normalizer, "Shipping is free for Product X.")
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.value is True

    async def test_negated_free_shipping_flips_the_boolean(self, normalizer) -> None:
        result = await parse(normalizer, "Product X does not include free shipping.")
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.operator is Operator.IS_FALSE
        assert result.value is False
        assert result.negated is True

    @pytest.mark.parametrize(
        "claim",
        [
            "No shipping charge applies to Product X.",
            "There is no delivery fee for Product X.",
        ],
    )
    async def test_no_shipping_charge_asserts_free_shipping(self, normalizer, claim: str) -> None:
        result = await parse(normalizer, claim)
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.operator is Operator.IS_TRUE
        assert result.value is True
        assert result.parse_confidence >= 0.9

    @pytest.mark.parametrize(
        "claim",
        [
            "Product X includes complimentary delivery.",
            "Delivery for Product X won't add anything to the bill.",
        ],
    )
    async def test_free_shipping_paraphrases_preserve_positive_polarity(
        self, normalizer, claim: str
    ) -> None:
        result = await parse(normalizer, claim)
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.operator is Operator.IS_TRUE
        assert result.value is True
        assert result.parse_confidence >= 0.9

    @pytest.mark.parametrize(
        "claim",
        [
            "A shipping charge applies to Product X.",
            "Delivery for Product X adds a charge to the bill.",
        ],
    )
    async def test_paid_shipping_phrases_assert_shipping_is_not_free(
        self, normalizer, claim: str
    ) -> None:
        result = await parse(normalizer, claim)
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING
        assert result.operator is Operator.IS_FALSE
        assert result.value is False
        assert result.parse_confidence >= 0.9

    async def test_boolean_value_is_not_coerced_to_a_number(self, normalizer) -> None:
        # Guards the union ordering on NormalizedClaim.value: a stray Decimal
        # here would turn a shipping claim into a price comparison.
        result = await parse(normalizer, "Product X includes free shipping.")
        assert isinstance(result.value, bool)

    async def test_shipping_cost_claim(self, normalizer) -> None:
        result = await parse(normalizer, "Shipping costs $9.99.")
        assert result.attribute is Attribute.SHIPPING_COST
        assert result.value == Decimal("9.99")

    async def test_delivery_service_is_not_coerced_to_free_shipping(self, normalizer) -> None:
        result = await parse(normalizer, "Product X includes weekend delivery.")
        assert result.claim_type is ClaimType.UNKNOWN
        assert result.attribute is Attribute.UNKNOWN

    async def test_free_shipping_over_a_threshold_stays_a_shipping_claim(self, normalizer) -> None:
        # Money is present, but the assertion is about shipping.
        result = await parse(normalizer, "Free shipping on orders over $50.")
        assert result.claim_type is ClaimType.SHIPPING
        assert result.attribute is Attribute.FREE_SHIPPING


class TestTrialClaims:
    async def test_parses_trial_duration(self, normalizer) -> None:
        result = await parse(normalizer, "The Pro plan includes a 30-day free trial.")
        assert result.claim_type is ClaimType.TRIAL_DURATION
        assert result.attribute is Attribute.TRIAL_DAYS
        assert result.value == 30
        assert result.unit == "days"

    async def test_trial_beats_the_feature_matcher(self, normalizer) -> None:
        # "includes" would also match the feature pattern; ordering decides.
        result = await parse(normalizer, "The Pro plan includes a 30-day free trial.")
        assert result.claim_type is not ClaimType.FEATURE_INCLUSION

    async def test_month_expressed_trial_normalizes_to_days(self, normalizer) -> None:
        result = await parse(normalizer, "Customers get a one month free trial.")
        assert result.value == 30

    async def test_negated_trial_asserts_zero_days(self, normalizer) -> None:
        result = await parse(normalizer, "The Starter plan does not include a free trial.")
        assert result.attribute is Attribute.TRIAL_DAYS
        assert result.value == 0

    async def test_trial_without_duration_is_a_lower_bound(self, normalizer) -> None:
        result = await parse(normalizer, "The plan includes a free trial.")
        assert result.operator is Operator.GREATER_THAN
        assert result.parse_confidence < 0.9


class TestFeatureClaims:
    async def test_feature_inclusion(self, normalizer) -> None:
        result = await parse(normalizer, "This laptop includes 32 GB RAM.")
        assert result.claim_type is ClaimType.FEATURE_INCLUSION
        assert result.attribute is Attribute.INCLUDED_FEATURES
        assert result.operator is Operator.INCLUDES
        assert result.feature == "32_gb_ram"

    async def test_feature_exclusion_via_negation(self, normalizer) -> None:
        result = await parse(normalizer, "The Pro plan does not include SSO.")
        assert result.claim_type is ClaimType.FEATURE_EXCLUSION
        assert result.operator is Operator.EXCLUDES
        assert result.feature == "sso"

    async def test_explicit_exclusion_wording(self, normalizer) -> None:
        result = await parse(normalizer, "The Pro plan excludes SSO.")
        assert result.claim_type is ClaimType.FEATURE_EXCLUSION

    async def test_unlimited_feature_claim(self, normalizer) -> None:
        result = await parse(normalizer, "The Premium plan includes unlimited API requests.")
        assert result.claim_type is ClaimType.FEATURE_INCLUSION
        assert result.feature == "unlimited_api_requests"

    async def test_hedge_does_not_rewrite_set_membership(self, normalizer) -> None:
        # "up to" is meaningless for membership; the operator must stay INCLUDES.
        result = await parse(normalizer, "Includes up to 32 GB RAM.")
        assert result.operator is Operator.INCLUDES

    async def test_named_feature_without_an_assertion_is_unclassified(self, normalizer) -> None:
        result = await parse(normalizer, "SSO")
        assert result.is_parsed is False


class TestAvailabilityClaims:
    @pytest.mark.parametrize(
        ("claim", "expected_value"),
        [
            ("The headphones are in stock.", "in_stock"),
            ("The laptop is out of stock.", "out_of_stock"),
            ("This item is sold out.", "out_of_stock"),
            ("The camera is available for pre-order.", "preorder"),
            ("The monitor has been discontinued.", "discontinued"),
        ],
    )
    async def test_maps_phrases_to_inventory_status(
        self, normalizer, claim: str, expected_value: str
    ) -> None:
        result = await parse(normalizer, claim)
        assert result.claim_type is ClaimType.AVAILABILITY
        assert result.attribute is Attribute.INVENTORY_STATUS
        assert result.value == expected_value

    async def test_negated_availability_uses_not_equals(self, normalizer) -> None:
        result = await parse(normalizer, "The tablet is not available.")
        assert result.operator is Operator.NOT_EQUALS
        assert result.value == "in_stock"


class TestRegionClaims:
    async def test_parses_region_eligibility(self, normalizer) -> None:
        result = await parse(normalizer, "This promotion is available in the United States.")
        assert result.claim_type is ClaimType.GEO_ELIGIBILITY
        assert result.attribute is Attribute.ELIGIBLE_REGIONS
        assert result.operator is Operator.INCLUDES
        assert result.value == "US"

    async def test_applies_to_region_is_an_eligibility_claim(self, normalizer) -> None:
        result = await parse(normalizer, "Product X applies to the BR region.")
        assert result.claim_type is ClaimType.GEO_ELIGIBILITY
        assert result.attribute is Attribute.ELIGIBLE_REGIONS
        assert result.operator is Operator.INCLUDES
        assert result.value == "BR"

    async def test_negated_region_claim(self, normalizer) -> None:
        result = await parse(normalizer, "The offer is not available in Canada.")
        assert result.operator is Operator.EXCLUDES
        assert result.value == "CA"

    async def test_explicit_request_region_wins(self, normalizer) -> None:
        result = await parse(normalizer, "AirPods Pro are $199 today.", region="ca")
        assert result.region == "CA"

    async def test_bare_country_code_without_a_cue_is_not_a_region_claim(self, normalizer) -> None:
        # "CA" appears in plenty of product names; a cue is required.
        result = await parse(normalizer, "The CA-3000 monitor is $299.")
        assert result.claim_type is ClaimType.PRICE


class TestPromotionDateClaims:
    async def test_parses_an_end_date(self, normalizer) -> None:
        result = await parse(normalizer, "Offer SAVE20 is valid through September 30.")
        assert result.claim_type is ClaimType.PROMOTION_DATES
        assert result.attribute is Attribute.OFFER_END
        assert result.value == "2026-09-30"
        assert result.time_context.end == date(2026, 9, 30)

    async def test_expired_assertion_without_a_date(self, normalizer) -> None:
        result = await parse(normalizer, "That promotion has expired.")
        assert result.claim_type is ClaimType.PROMOTION_DATES
        assert result.operator is Operator.LESS_THAN

    async def test_as_of_defaults_to_the_injected_clock(self, normalizer) -> None:
        result = await parse(normalizer, "AirPods Pro are $199 today.")
        assert result.time_context.as_of == FIXED_NOW.date()

    async def test_explicit_as_of_overrides_the_clock(self, normalizer) -> None:
        result = await parse(normalizer, "AirPods Pro are $199 today.", as_of=date(2026, 1, 1))
        assert result.time_context.as_of == date(2026, 1, 1)


class TestMinimumPurchaseClaims:
    async def test_parses_minimum_purchase(self, normalizer) -> None:
        result = await parse(normalizer, "This offer has a minimum purchase of $50.")
        assert result.claim_type is ClaimType.MINIMUM_PURCHASE
        assert result.value == Decimal("50")

    async def test_no_minimum_purchase_asserts_zero(self, normalizer) -> None:
        result = await parse(normalizer, "There is no minimum purchase for this offer.")
        assert result.claim_type is ClaimType.MINIMUM_PURCHASE
        assert result.value == 0


class TestEntityResolution:
    async def test_explicit_reference_id_wins(self, normalizer) -> None:
        result = await parse(
            normalizer, "The plan includes a 30-day free trial.", reference_id="plan-pro"
        )
        assert result.entity.id == "plan-pro"

    async def test_extracts_a_name_when_no_identifier_is_given(self, normalizer) -> None:
        result = await parse(normalizer, "Noise Cancelling Headphones are $199.99 today.")
        assert result.entity.name == "Noise Cancelling Headphones"

    async def test_strips_leading_determiners(self, normalizer) -> None:
        result = await parse(normalizer, "The Vector Pro 14 costs $1,499.")
        assert result.entity.name == "Vector Pro 14"

    async def test_plan_context_is_detected(self, normalizer) -> None:
        result = await parse(normalizer, "The Pro plan costs $99 per month.")
        assert result.entity.type is EntityType.PLAN

    async def test_short_fragment_is_dropped_rather_than_guessed(self, normalizer) -> None:
        result = await parse(normalizer, "It is $199.")
        assert result.entity.name is None


class TestUnclassifiedClaims:
    @pytest.mark.parametrize(
        "claim",
        [
            "This is the best product we have ever made.",
            "Customers love this item.",
        ],
    )
    async def test_unparseable_claims_escalate_rather_than_fail(
        self, normalizer, claim: str
    ) -> None:
        result = await parse(normalizer, claim)
        assert result.is_parsed is False
        assert result.parse_confidence == 0.0
        assert "unclassified" in result.notes

    async def test_double_negation_lowers_confidence(self, normalizer) -> None:
        result = await parse(normalizer, "It isn't true that the plan lacks SSO.")
        assert result.parse_confidence <= 0.35
        assert "double_negation" in result.notes

    @pytest.mark.parametrize(
        "claim",
        [
            "Best offer ever for Northwind Pro!",
            "Buy Northwind Pro today!",
            "Great value from Northwind Pro.",
        ],
    )
    async def test_non_verifiable_commercial_slogans_are_marked(
        self, normalizer, claim: str
    ) -> None:
        result = await parse(normalizer, claim)
        assert result.is_parsed is False
        assert "non_verifiable_commercial_proposition" in result.notes

    async def test_imperative_with_a_factual_price_is_not_marked_invalid(self, normalizer) -> None:
        result = await parse(normalizer, "Buy Northwind Pro today for $20.")
        assert result.is_parsed is True
        assert "non_verifiable_commercial_proposition" not in result.notes


class TestBaselineProfile:
    async def test_parsing_disabled_produces_an_unparsed_claim(self, baseline_normalizer) -> None:
        # The baseline arm hands the raw sentence to the model. Same code path,
        # different config; there is no second implementation.
        result = await baseline_normalizer.normalize(
            VerificationRequest(claim="AirPods Pro are $199 today.")
        )
        assert result.is_parsed is False
        assert result.claim_type is ClaimType.UNKNOWN
        assert result.value is None
        assert "deterministic_parsing_disabled" in result.notes

    async def test_still_validates_input(self, baseline_normalizer) -> None:
        with pytest.raises(InvalidClaimError):
            await baseline_normalizer.normalize(VerificationRequest(claim="  "))

    async def test_still_carries_identity_and_time(self, baseline_normalizer) -> None:
        result = await baseline_normalizer.normalize(
            VerificationRequest(claim="Anything at all.", reference_id="plan-pro")
        )
        assert result.entity.id == "plan-pro"
        assert result.time_context.as_of == FIXED_NOW.date()
