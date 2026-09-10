"""Negation and hedge detection.

These two are grouped because they are the parsing decisions that flip a
verdict rather than merely refine it: getting either wrong yields a confident
answer with correct-looking evidence attached, which is the hardest class of
error to notice in production.
"""

from __future__ import annotations

import pytest
from app.domain.enums import Operator, Qualifier
from app.normalization import negation, qualifiers


class TestNegationDetection:
    @pytest.mark.parametrize(
        "claim",
        [
            "Product X does not include free shipping.",
            "Product X doesn't include free shipping.",
            "This plan is not available in the US.",
            "The laptop cannot be shipped to Canada.",
            "The Pro plan excludes SSO.",
            "The bundle comes without a charging case.",
            "This tier lacks audit logs.",
            "The headphones are not in stock.",
        ],
    )
    def test_detects_explicit_negation(self, claim: str) -> None:
        assert negation.detect_negation(claim).negated is True

    @pytest.mark.parametrize(
        "claim",
        [
            "Product X includes free shipping.",
            "The Pro plan includes a 30-day free trial.",
            "AirPods Pro are $199 today.",
            "This laptop includes 32 GB RAM.",
        ],
    )
    def test_leaves_positive_claims_alone(self, claim: str) -> None:
        assert negation.detect_negation(claim).negated is False

    @pytest.mark.parametrize(
        "claim",
        [
            "The headphones are out of stock.",
            "This item is sold out.",
            "There is no minimum purchase on this offer.",
            "The Premium plan has no cap on API requests.",
        ],
    )
    def test_negative_wording_that_asserts_a_positive_fact(self, claim: str) -> None:
        # "out of stock" is a claim *about* availability, not a negation of the
        # claim. Treating it as negation would invert the comparison.
        assert negation.detect_negation(claim).negated is False

    def test_noise_cancelling_does_not_trip_the_no_cue(self) -> None:
        # The literal substring "no" appears inside "noise"; word boundaries
        # and a phrase guard both have to hold here.
        result = negation.detect_negation("These headphones include noise cancellation.")
        assert result.negated is False

    def test_double_negation_is_flagged_rather_than_guessed(self) -> None:
        result = negation.detect_negation("It isn't true that the plan lacks SSO.")
        assert result.double is True
        # Reported as not-negated but marked, so the caller can route it to the
        # semantic rater instead of trusting a deterministic reading.
        assert result.negated is False

    def test_reports_the_matched_cue_for_auditability(self) -> None:
        result = negation.detect_negation("Product X does not include free shipping.")
        assert result.cue is not None


class TestQualifierDetection:
    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            ("Save up to 20% on all orders.", Qualifier.UP_TO),
            ("Plans starting at $99 per month.", Qualifier.STARTING_AT),
            ("Prices from $49.", Qualifier.STARTING_AT),
            ("Approximately $200 for the bundle.", Qualifier.APPROXIMATELY),
            ("Roughly 30 days of trial access.", Qualifier.APPROXIMATELY),
            ("At least 30 days of trial.", Qualifier.AT_LEAST),
            ("No more than $50 shipping.", Qualifier.AT_MOST),
            ("The Premium plan includes unlimited API requests.", Qualifier.UNLIMITED),
        ],
    )
    def test_detects_hedges(self, claim: str, expected: Qualifier) -> None:
        match = qualifiers.detect_qualifier(claim)
        assert match is not None
        assert match.qualifier is expected

    def test_unhedged_claim_has_no_qualifier(self) -> None:
        assert qualifiers.detect_qualifier("20% off all orders.") is None

    @pytest.mark.parametrize(
        "claim",
        [
            "Read about our shipping policy.",
            "We ship around the world.",
        ],
    )
    def test_requires_numeric_context_for_weak_cues(self, claim: str) -> None:
        # "about"/"around" only hedge a number. Otherwise they would silently
        # widen the numeric tolerance on an unrelated claim.
        assert qualifiers.detect_qualifier(claim) is None

    def test_no_more_than_is_not_shadowed_by_more_than(self) -> None:
        match = qualifiers.detect_qualifier("Shipping is no more than $10.")
        assert match is not None
        assert match.qualifier is Qualifier.AT_MOST


class TestQualifierOperators:
    def test_up_to_becomes_an_upper_bound(self) -> None:
        assert qualifiers.operator_for(Qualifier.UP_TO) is Operator.AT_MOST

    def test_starting_at_becomes_a_lower_bound(self) -> None:
        assert qualifiers.operator_for(Qualifier.STARTING_AT) is Operator.AT_LEAST

    def test_approximately_stays_an_equality(self) -> None:
        # A fuzzy equality, handled by widening tolerance rather than by
        # turning it into a one-sided bound.
        assert qualifiers.operator_for(Qualifier.APPROXIMATELY) is Operator.EQUALS


class TestSatisfies:
    def test_up_to_accepts_a_smaller_reference_value(self) -> None:
        # "up to 20% off" against an actual 15% discount is true.
        assert qualifiers.satisfies(Qualifier.UP_TO, claimed=20.0, reference=15.0, tolerance=0.01)

    def test_up_to_rejects_a_larger_reference_value(self) -> None:
        assert not qualifiers.satisfies(
            Qualifier.UP_TO, claimed=20.0, reference=25.0, tolerance=0.01
        )

    def test_bare_equality_rejects_what_up_to_would_accept(self) -> None:
        # Same numbers, different phrasing, opposite verdicts. This is the
        # whole reason hedges are parsed deterministically.
        assert not qualifiers.satisfies(
            Qualifier.NONE, claimed=20.0, reference=15.0, tolerance=0.01
        )

    def test_starting_at_accepts_a_larger_reference_value(self) -> None:
        assert qualifiers.satisfies(
            Qualifier.STARTING_AT, claimed=99.0, reference=149.0, tolerance=0.01
        )

    def test_approximately_uses_the_widened_tolerance(self) -> None:
        assert qualifiers.satisfies(
            Qualifier.APPROXIMATELY, claimed=200.0, reference=199.99, tolerance=10.0
        )
        assert not qualifiers.satisfies(
            Qualifier.APPROXIMATELY, claimed=200.0, reference=149.0, tolerance=10.0
        )
