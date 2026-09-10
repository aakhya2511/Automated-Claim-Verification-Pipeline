"""Deterministic rule engine.

These tests are the specification of the fast path: what terminates without a
model call, what escalates, and — most importantly — what refuses to call a
missing field a contradiction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.core.clock import FixedClock
from app.core.pipeline_config import NormalizationConfig, RetrievalConfig, RuleConfig
from app.domain.enums import Attribute, ClaimType, Qualifier, ReasonCode, Verdict
from app.domain.models import VerificationRequest
from app.normalization.claim_parser import DeterministicClaimNormalizer
from app.normalization.features import FeatureResolver, vocabulary_from_records
from app.retrieval.evidence import ReferenceEvidenceRetriever
from app.rules.engine import DeterministicRuleEngine

from tests.conftest import FIXED_NOW

AS_OF = FIXED_NOW.date()


class Pipeline:
    """Normalize -> retrieve -> evaluate, so tests read like real claims."""

    def __init__(self, repository, rule_config: RuleConfig) -> None:
        clock = FixedClock(moment=FIXED_NOW)
        self.normalizer = DeterministicClaimNormalizer(
            config=NormalizationConfig(),
            feature_resolver=FeatureResolver(vocabulary_from_records(repository.all_records())),
            clock=clock,
        )
        self.retriever = ReferenceEvidenceRetriever(repository=repository, config=RetrievalConfig())
        self.engine = DeterministicRuleEngine(config=rule_config, clock=clock)

    async def run(self, claim: str, **kwargs):
        request = VerificationRequest(claim=claim, **kwargs)
        normalized = await self.normalizer.normalize(request)
        evidence = await self.retriever.retrieve(normalized, request)
        return normalized, evidence, self.engine.evaluate(normalized, evidence)


@pytest.fixture
def pipeline(repository) -> Pipeline:
    return Pipeline(repository, RuleConfig())


class TestPriceRules:
    async def test_exact_price_match_is_supported_without_a_model(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $199.99.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.SUPPORTED
        assert result.is_terminal is True
        assert result.escalate_to_rater is False
        assert ReasonCode.EXACT_MATCH in result.reason_codes

    async def test_wrong_price_is_contradicted_without_a_model(self, pipeline) -> None:
        # "$149 vs reference 199.99" is the canonical case that must never
        # cost a model call.
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $149.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert result.is_terminal is True
        assert ReasonCode.PRICE_MISMATCH in result.reason_codes
        assert result.decisive_rule_id == "numeric_comparison"

    async def test_cent_level_rounding_is_within_tolerance(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $199.99 today.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_one_dollar_difference_is_still_a_mismatch(self, pipeline) -> None:
        # Tolerance absorbs representation noise, not real differences.
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $198.99.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_currency_mismatch_is_reported_as_a_unit_error(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are 199.99 GBP.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.CURRENCY_MISMATCH in result.reason_codes

    async def test_starting_at_is_satisfied_by_a_higher_reference_price(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are starting at $99.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.SUPPORTED
        assert ReasonCode.QUALIFIER_SATISFIED in result.reason_codes

    async def test_approximately_widens_the_band(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones cost approximately $200.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_approximately_does_not_excuse_a_large_gap(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones cost approximately $120.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_plan_price_uses_subscription_price(self, pipeline) -> None:
        _, evidence, result = await pipeline.run(
            "The Pro plan costs $99 per month.", reference_id="plan-pro"
        )
        assert evidence.effective_attribute is Attribute.SUBSCRIPTION_PRICE
        assert result.verdict is Verdict.SUPPORTED


class TestDiscountRules:
    async def test_bare_percentage_must_match_exactly(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer FALL30 gives 30% off.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_wrong_percentage_is_contradicted(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer FALL30 gives 40% off.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.DISCOUNT_MISMATCH in result.reason_codes

    async def test_up_to_is_satisfied_by_a_smaller_discount(self, pipeline) -> None:
        # Reference is 30%; "up to 40%" is true, a bare "40%" is not. Same
        # numbers, opposite verdicts.
        _, _, up_to = await pipeline.run(
            "Save up to 40% off with offer FALL30.", reference_id="offer-active-1"
        )
        assert up_to.verdict is Verdict.SUPPORTED

    async def test_up_to_rejects_a_larger_reference_discount(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Save up to 20% off with offer FALL30.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.CONTRADICTED


class TestExpiredPromotions:
    async def test_present_tense_discount_on_an_expired_offer_is_contradicted(
        self, pipeline
    ) -> None:
        # offer-expired-1 ran 2026-06-01..2026-06-30; evaluated 2026-09-15.
        _, _, result = await pipeline.run(
            "Offer SAVE20 gives 20% off.", reference_id="offer-expired-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.OFFER_EXPIRED in result.reason_codes

    async def test_the_discount_value_itself_still_matches(self, pipeline) -> None:
        # The contradiction is about the window, not the number: both outcomes
        # are recorded, and the contradiction wins.
        _, _, result = await pipeline.run(
            "Offer SAVE20 gives 20% off.", reference_id="offer-expired-1"
        )
        verdicts = {outcome.rule_id: outcome.verdict for outcome in result.outcomes}
        assert verdicts["numeric_comparison"] is Verdict.SUPPORTED
        assert verdicts["active_offer_window"] is Verdict.CONTRADICTED
        assert result.verdict is Verdict.CONTRADICTED

    async def test_a_claim_stating_its_own_window_is_not_an_activity_assertion(
        self, pipeline
    ) -> None:
        # "20% off through June 30" describes the window; it does not assert
        # that the window is open today, so the expiry guard must not fire.
        _, _, result = await pipeline.run(
            "Offer SAVE20 gives 20% off through June 30.", reference_id="offer-expired-1"
        )
        rule_ids = {outcome.rule_id for outcome in result.outcomes if outcome.fired}
        assert "active_offer_window" not in rule_ids
        assert result.verdict is Verdict.SUPPORTED

    async def test_wrong_end_date_is_a_date_mismatch(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer SAVE20 is valid through July 31.", reference_id="offer-expired-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.DATE_MISMATCH in result.reason_codes

    async def test_expired_assertion_is_supported_for_an_expired_offer(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer SAVE20 has expired.", reference_id="offer-expired-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_expired_assertion_is_contradicted_for_an_active_offer(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer FALL30 has expired.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_inferred_year_does_not_manufacture_a_date_mismatch(self, pipeline) -> None:
        # Evaluated in December, a bare "June 30" resolves forward to 2027,
        # while the reference offer ended 2026-06-30. Comparing full dates
        # would report a mismatch caused entirely by our own year inference,
        # so month/day is compared instead.
        claim, _, result = await pipeline.run(
            "Offer SAVE20 is valid through June 30.",
            reference_id="offer-expired-1",
            as_of=date(2026, 12, 20),
        )
        assert claim.time_context.year_inferred is True
        assert claim.time_context.end == date(2027, 6, 30)
        assert result.verdict is Verdict.SUPPORTED

    async def test_explicit_year_is_compared_in_full(self, pipeline) -> None:
        claim, _, result = await pipeline.run(
            "Offer SAVE20 is valid through June 30, 2027.", reference_id="offer-expired-1"
        )
        assert claim.time_context.year_inferred is False
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.DATE_MISMATCH in result.reason_codes

    async def test_date_grace_absorbs_a_boundary_offset(self, repository) -> None:
        # An offer that ended yesterday is not "wrong" under a one-day grace;
        # this is the timezone-boundary safety valve.
        lenient = Pipeline(repository, RuleConfig(date_grace_days=1))
        _, _, result = await lenient.run(
            "Offer WINDOW gives 30% off.", reference_id="offer-active-1"
        )
        assert result.verdict is not Verdict.CONTRADICTED


class TestShippingRules:
    async def test_free_shipping_supported(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This product includes free shipping.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_false_free_shipping_claim_is_contradicted(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This offer includes free shipping.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.SHIPPING_MISMATCH in result.reason_codes

    async def test_negated_shipping_claim_matches_a_paid_shipping_record(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This offer does not include free shipping.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_negation_flips_the_verdict_on_identical_evidence(self, pipeline) -> None:
        # One cue, opposite verdicts, same reference row.
        _, _, positive = await pipeline.run(
            "This product includes free shipping.", reference_id="prod-headphones-1"
        )
        _, _, negative = await pipeline.run(
            "This product does not include free shipping.", reference_id="prod-headphones-1"
        )
        assert positive.verdict is Verdict.SUPPORTED
        assert negative.verdict is Verdict.CONTRADICTED


class TestTrialRules:
    async def test_correct_trial_duration(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Pro plan includes a 30-day free trial.", reference_id="plan-pro"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_wrong_trial_duration(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Pro plan includes a 60-day free trial.", reference_id="plan-pro"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.TRIAL_DURATION_MISMATCH in result.reason_codes

    async def test_zero_trial_days_is_a_real_value_not_a_missing_field(self, pipeline) -> None:
        # plan-premium has trial_days == 0: the plan has no trial, which is a
        # verifiable fact and must contradict a 30-day claim.
        _, _, result = await pipeline.run(
            "The Premium plan includes a 30-day free trial.", reference_id="plan-premium"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.INSUFFICIENT_EVIDENCE not in result.reason_codes

    async def test_no_trial_claim_matches_a_zero_trial_plan(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Premium plan does not include a free trial.", reference_id="plan-premium"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_month_phrasing_matches_a_thirty_day_trial(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Pro plan includes a one month free trial.", reference_id="plan-pro"
        )
        assert result.verdict is Verdict.SUPPORTED


class TestFeatureRules:
    async def test_included_feature_is_supported(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This laptop includes 32 GB RAM.", reference_id="prod-laptop-1"
        )
        assert result.verdict is Verdict.SUPPORTED
        assert ReasonCode.FEATURE_PRESENT in result.reason_codes

    async def test_explicitly_excluded_feature_is_contradicted(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This laptop includes a discrete GPU.", reference_id="prod-laptop-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.FEATURE_MISMATCH in result.reason_codes

    async def test_feature_in_neither_list_is_insufficient_not_contradicted(self, pipeline) -> None:
        # The single most important policy in this engine: silence in the
        # source of truth is not evidence of falsehood.
        _, _, result = await pipeline.run(
            "This laptop includes Thunderbolt 4 and a HEPA filter.",
            reference_id="prod-laptop-1",
        )
        assert result.verdict is not Verdict.CONTRADICTED

    async def test_exclusion_claim_matching_the_exclusion_list(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Pro plan does not include SSO.", reference_id="plan-pro"
        )
        assert result.verdict is Verdict.SUPPORTED
        assert ReasonCode.FEATURE_ABSENT in result.reason_codes

    async def test_exclusion_claim_contradicted_by_the_inclusion_list(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Premium plan does not include SSO.", reference_id="plan-premium"
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_unlimited_api_requests_feature(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Premium plan includes unlimited API requests.", reference_id="plan-premium"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_unlimited_api_requests_contradicted_for_pro(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Pro plan includes unlimited API requests.", reference_id="plan-pro"
        )
        # plan-pro lists neither, so this is genuinely unknown.
        assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE


class TestAvailabilityRules:
    async def test_in_stock_claim_supported(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are in stock.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_limited_stock_satisfies_an_in_stock_claim(self, pipeline) -> None:
        # prod-laptop-1 is limited_stock. It is in stock, just not much of it.
        _, _, result = await pipeline.run(
            "The Vector Pro 14 is in stock.", reference_id="prod-laptop-1"
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_out_of_stock_claim_contradicted(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are out of stock.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.AVAILABILITY_MISMATCH in result.reason_codes


class TestRegionRules:
    async def test_eligible_region_supported(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This product is available in the United States.",
            reference_id="prod-headphones-1",
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_ineligible_region_contradicted(self, pipeline) -> None:
        # offer-expired-1 is CA-only; a US eligibility claim must fail.
        _, _, result = await pipeline.run(
            "This offer is available in the United States.", reference_id="offer-expired-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert ReasonCode.REGION_MISMATCH in result.reason_codes

    async def test_allow_list_absence_is_a_contradiction_by_definition(self, pipeline) -> None:
        # eligible_regions is exhaustive, so absence means "not available".
        # This is the one field where missing == false.
        _, _, result = await pipeline.run(
            "This laptop is available in Germany.", reference_id="prod-laptop-1"
        )
        assert result.verdict is Verdict.CONTRADICTED


class TestInsufficientEvidence:
    async def test_unresolved_entity_abstains(self, pipeline) -> None:
        _, _, result = await pipeline.run("The Quantum Flux Capacitor is $99.")
        assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert ReasonCode.REFERENCE_NOT_FOUND in result.reason_codes
        assert result.decisive_rule_id == "entity_resolution"

    async def test_missing_field_abstains_rather_than_contradicts(self, pipeline) -> None:
        # prod-sparse-1 has a price but no trial_days.
        _, _, result = await pipeline.run(
            "This grinder includes a 30-day free trial.", reference_id="prod-sparse-1"
        )
        assert result.verdict is Verdict.INSUFFICIENT_EVIDENCE
        assert ReasonCode.FIELD_NOT_IN_REFERENCE in result.reason_codes

    async def test_unparsed_claim_escalates_without_a_verdict(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This is the best product we have ever made.", reference_id="prod-headphones-1"
        )
        assert result.verdict is None
        assert result.escalate_to_rater is True
        assert ReasonCode.UNPARSEABLE_CLAIM in result.reason_codes

    async def test_abstention_still_permits_escalation(self, pipeline) -> None:
        # Whether the rater is actually consulted is a profile decision, not
        # the rule engine's.
        _, _, result = await pipeline.run(
            "This grinder includes a 30-day free trial.", reference_id="prod-sparse-1"
        )
        assert result.escalate_to_rater is True


class TestConfidenceGating:
    async def test_weakly_parsed_claim_does_not_terminate(self, pipeline) -> None:
        # "includes a free trial" parses at 0.7, below the 0.9 termination
        # floor, so the deterministic verdict is a prior rather than final.
        claim, _, result = await pipeline.run(
            "The Pro plan includes a free trial.", reference_id="plan-pro"
        )
        assert claim.parse_confidence < 0.9
        assert result.escalate_to_rater is True
        assert ReasonCode.LOW_CONFIDENCE_ABSTAIN in result.reason_codes

    async def test_confidence_is_the_minimum_of_rule_and_parse(self, pipeline) -> None:
        claim, _, result = await pipeline.run(
            "The Pro plan includes a free trial.", reference_id="plan-pro"
        )
        assert result.confidence == pytest.approx(claim.parse_confidence)

    async def test_profile_can_forbid_terminal_contradictions(self, repository) -> None:
        cautious = Pipeline(repository, RuleConfig(allow_terminal_contradicted=False))
        _, _, result = await cautious.run(
            "The Noise Cancelling Headphones are $149.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.CONTRADICTED
        assert result.escalate_to_rater is True
        assert result.is_terminal is False


class TestDisabledEngine:
    async def test_baseline_profile_escalates_everything(self, repository) -> None:
        disabled = Pipeline(repository, RuleConfig(enabled=False))
        _, _, result = await disabled.run(
            "The Noise Cancelling Headphones are $149.", reference_id="prod-headphones-1"
        )
        assert result.verdict is None
        assert result.escalate_to_rater is True
        assert result.outcomes == ()


class TestAuditability:
    async def test_outcomes_record_observed_and_expected(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $149.", reference_id="prod-headphones-1"
        )
        numeric = next(o for o in result.outcomes if o.rule_id == "numeric_comparison")
        assert numeric.expected == "149"
        assert numeric.observed == "199.99"
        assert numeric.detail is not None

    async def test_every_evaluated_rule_is_reported(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $199.99.", reference_id="prod-headphones-1"
        )
        rule_ids = [outcome.rule_id for outcome in result.outcomes]
        # Including the gates that declined to fire, so a reviewer can see
        # resolution and presence were checked.
        assert "entity_resolution" in rule_ids
        assert "field_presence" in rule_ids
        assert "numeric_comparison" in rule_ids


class TestEdgeCases:
    async def test_zero_price_claim(self, repository) -> None:
        pipeline = Pipeline(repository, RuleConfig())
        _, _, result = await pipeline.run(
            "The Noise Cancelling Headphones are $0.", reference_id="prod-headphones-1"
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_zero_percent_discount_claim(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "Offer FALL30 gives 0% off.", reference_id="offer-active-1"
        )
        assert result.verdict is Verdict.CONTRADICTED

    async def test_decimal_precision_is_exact(self, pipeline) -> None:
        claim, _, _ = await pipeline.run(
            "The Noise Cancelling Headphones are $199.99.", reference_id="prod-headphones-1"
        )
        assert claim.value == Decimal("199.99")

    async def test_evaluation_date_can_be_overridden(self, pipeline) -> None:
        # Evaluating the expired offer during its window makes it valid again.
        _, _, result = await pipeline.run(
            "Offer SAVE20 gives 20% off.",
            reference_id="offer-expired-1",
            as_of=date(2026, 6, 15),
        )
        assert result.verdict is Verdict.SUPPORTED

    async def test_qualifier_on_a_feature_claim_is_ignored(self, pipeline) -> None:
        _, _, result = await pipeline.run(
            "This laptop includes up to 32 GB RAM.", reference_id="prod-laptop-1"
        )
        assert result.verdict is Verdict.SUPPORTED


class TestClaimTypeCoverage:
    """Every claim category the service advertises must have a deterministic path."""

    @pytest.mark.parametrize(
        ("claim_text", "reference_id", "expected_type"),
        [
            ("The headphones are $199.99.", "prod-headphones-1", ClaimType.PRICE),
            ("Offer FALL30 gives 30% off.", "offer-active-1", ClaimType.DISCOUNT),
            ("This includes free shipping.", "prod-headphones-1", ClaimType.SHIPPING),
            ("The headphones are in stock.", "prod-headphones-1", ClaimType.AVAILABILITY),
            (
                "This laptop includes 32 GB RAM.",
                "prod-laptop-1",
                ClaimType.FEATURE_INCLUSION,
            ),
            ("The Pro plan excludes SSO.", "plan-pro", ClaimType.FEATURE_EXCLUSION),
            (
                "The Pro plan costs $99 per month.",
                "plan-pro",
                ClaimType.SUBSCRIPTION_TERMS,
            ),
            (
                "The Pro plan includes a 30-day free trial.",
                "plan-pro",
                ClaimType.TRIAL_DURATION,
            ),
            (
                "Offer SAVE20 is valid through June 30.",
                "offer-expired-1",
                ClaimType.PROMOTION_DATES,
            ),
            (
                "This product is available in the United States.",
                "prod-headphones-1",
                ClaimType.GEO_ELIGIBILITY,
            ),
            (
                "This offer has a minimum purchase of $50.",
                "offer-expired-1",
                ClaimType.MINIMUM_PURCHASE,
            ),
        ],
    )
    async def test_each_category_reaches_a_deterministic_verdict(
        self, pipeline, claim_text: str, reference_id: str, expected_type: ClaimType
    ) -> None:
        claim, _, result = await pipeline.run(claim_text, reference_id=reference_id)
        assert claim.claim_type is expected_type
        assert result.verdict is not None, f"{expected_type} escalated unexpectedly"
        assert result.is_terminal is True


class TestQualifierIntegration:
    async def test_up_to_and_bare_percentage_diverge_on_the_same_record(self, pipeline) -> None:
        _, _, hedged = await pipeline.run(
            "Save up to 40% off with offer FALL30.", reference_id="offer-active-1"
        )
        _, _, bare = await pipeline.run(
            "Get 40% off with offer FALL30.", reference_id="offer-active-1"
        )
        assert hedged.verdict is Verdict.SUPPORTED
        assert bare.verdict is Verdict.CONTRADICTED

    async def test_qualifier_is_recorded_on_the_claim(self, pipeline) -> None:
        claim, _, _ = await pipeline.run(
            "Save up to 40% off with offer FALL30.", reference_id="offer-active-1"
        )
        assert claim.qualifier is Qualifier.UP_TO
