"""Evidence retrieval: entity resolution and field projection."""

from __future__ import annotations

import pytest
from app.core.pipeline_config import RetrievalConfig
from app.domain.enums import Attribute, ClaimType, EntityType, Operator
from app.domain.models import ClaimEntity, NormalizedClaim, VerificationRequest
from app.retrieval.evidence import ReferenceEvidenceRetriever


@pytest.fixture
def retriever(repository) -> ReferenceEvidenceRetriever:
    return ReferenceEvidenceRetriever(repository=repository, config=RetrievalConfig())


@pytest.fixture
def baseline_retriever(repository) -> ReferenceEvidenceRetriever:
    return ReferenceEvidenceRetriever(
        repository=repository,
        config=RetrievalConfig(
            strategy="id_only", evidence_projection="full_record", max_candidates=1
        ),
    )


def claim(**kwargs) -> NormalizedClaim:
    defaults = {
        "raw_text": "test claim",
        "claim_type": ClaimType.PRICE,
        "attribute": Attribute.PRICE,
    }
    return NormalizedClaim(**(defaults | kwargs))


class TestEntityResolution:
    async def test_explicit_reference_id(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="plan-pro")
        )
        assert evidence.record_id == "plan-pro"
        assert evidence.match_method == "reference_id"
        assert evidence.match_score == 1.0

    async def test_explicit_sku(self, retriever) -> None:
        evidence = await retriever.retrieve(claim(), VerificationRequest(claim="x", sku="SKU-123"))
        assert evidence.record_id == "prod-headphones-1"

    async def test_unknown_reference_id_yields_empty_evidence(self, retriever) -> None:
        # Empty evidence is a first-class outcome, not an exception: the rule
        # engine turns it into INSUFFICIENT_EVIDENCE.
        evidence = await retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="does-not-exist")
        )
        assert evidence.is_empty is True
        assert evidence.record_id is None

    async def test_exact_name_resolution(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="Noise Cancelling Headphones")),
            VerificationRequest(claim="Noise Cancelling Headphones are $199.99"),
        )
        assert evidence.record_id == "prod-headphones-1"
        assert evidence.match_method == "exact_name"

    async def test_alias_resolution(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="NC Headphones")),
            VerificationRequest(claim="NC Headphones are $199.99"),
        )
        assert evidence.record_id == "prod-headphones-1"

    async def test_brand_prefixed_name_is_an_exact_surface_form(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="Aurex Noise Cancelling Headphones")),
            VerificationRequest(claim="x"),
        )
        assert evidence.record_id == "prod-headphones-1"
        assert evidence.match_method == "exact_name"

    async def test_lexical_fallback(self, retriever) -> None:
        # Not any indexed surface form, so this has to be scored.
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="Aurex over-ear noise cancelling headphones")),
            VerificationRequest(claim="x"),
        )
        assert evidence.record_id == "prod-headphones-1"
        assert evidence.match_method == "lexical_search"
        assert 0.0 < evidence.match_score <= 1.0

    async def test_low_scoring_match_is_discarded(self, repository) -> None:
        # A wrong record is worse than none: it produces a confident verdict
        # grounded in the wrong row.
        strict = ReferenceEvidenceRetriever(
            repository=repository, config=RetrievalConfig(min_match_score=0.99)
        )
        evidence = await strict.retrieve(
            claim(entity=ClaimEntity(name="headphones")), VerificationRequest(claim="x")
        )
        assert evidence.is_empty is True
        assert evidence.match_method == "unresolved_low_score"

    async def test_unresolvable_entity(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="Quantum Flux Capacitor")),
            VerificationRequest(claim="x"),
        )
        assert evidence.is_empty is True

    async def test_candidates_are_recorded_for_audit(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(entity=ClaimEntity(name="Aurex headphones")), VerificationRequest(claim="x")
        )
        assert evidence.candidate_ids


class TestBaselineRetrieval:
    async def test_id_only_strategy_does_not_fall_back(self, baseline_retriever) -> None:
        # The baseline arm cannot resolve a product named only in prose.
        evidence = await baseline_retriever.retrieve(
            claim(entity=ClaimEntity(name="Noise Cancelling Headphones")),
            VerificationRequest(claim="Noise Cancelling Headphones are $199.99"),
        )
        assert evidence.is_empty is True
        assert evidence.match_method == "unresolved_id_only"

    async def test_full_record_projection_includes_distractor_fields(
        self, baseline_retriever
    ) -> None:
        evidence = await baseline_retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="prod-headphones-1")
        )
        # Every populated column, including numbers unrelated to the claim.
        assert "price" in evidence.fields
        assert "shipping_cost" in evidence.fields
        assert "inventory_status" in evidence.fields
        assert "eligible_regions" in evidence.fields


class TestFieldProjection:
    async def test_price_claim_projects_price_and_context(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="prod-headphones-1")
        )
        assert evidence.value_of(Attribute.PRICE) is not None
        assert evidence.value_of(Attribute.CURRENCY) == "USD"
        # Unrelated numbers stay out of the projection (and out of the prompt).
        assert "trial_days" not in evidence.fields
        assert "subscription_price" not in evidence.fields

    async def test_trial_claim_projects_only_trial_days(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(claim_type=ClaimType.TRIAL_DURATION, attribute=Attribute.TRIAL_DAYS),
            VerificationRequest(claim="x", reference_id="plan-pro"),
        )
        assert evidence.value_of(Attribute.TRIAL_DAYS) == 30
        assert "subscription_price" not in evidence.fields

    async def test_feature_claim_projects_both_feature_lists(self, retriever) -> None:
        # An inclusion claim needs the exclusion list too: that is what makes
        # "wrong" separable from "unknown".
        evidence = await retriever.retrieve(
            claim(
                claim_type=ClaimType.FEATURE_INCLUSION,
                attribute=Attribute.INCLUDED_FEATURES,
                operator=Operator.INCLUDES,
                feature="sso",
            ),
            VerificationRequest(claim="x", reference_id="plan-pro"),
        )
        assert evidence.has(Attribute.INCLUDED_FEATURES)
        assert evidence.has(Attribute.EXCLUDED_FEATURES)

    async def test_discount_claim_projects_the_promotion_window(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(claim_type=ClaimType.DISCOUNT, attribute=Attribute.DISCOUNT_PERCENT),
            VerificationRequest(claim="x", reference_id="offer-expired-1"),
        )
        assert evidence.has(Attribute.OFFER_END)

    async def test_absent_answering_field_is_marked_explicitly(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(claim_type=ClaimType.TRIAL_DURATION, attribute=Attribute.TRIAL_DAYS),
            VerificationRequest(claim="x", reference_id="prod-sparse-1"),
        )
        # The absence is recorded rather than left to be inferred from a
        # missing key, so the audit trail shows the field was looked for.
        assert evidence.fields.get("trial_days__absent") is True
        assert evidence.has(Attribute.TRIAL_DAYS) is False

    async def test_unknown_claim_projects_no_unrelated_answering_fields(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(claim_type=ClaimType.UNKNOWN, attribute=Attribute.UNKNOWN),
            VerificationRequest(claim="unclassified assertion", reference_id="prod-headphones-1"),
        )
        assert evidence.record_id == "prod-headphones-1"
        assert evidence.fields == {}


class TestAttributeFallback:
    async def test_price_claim_about_a_plan_redirects_to_subscription_price(
        self, retriever
    ) -> None:
        # Plans do not populate `price`. Reporting "field missing" here would
        # be an abstention caused purely by schema shape.
        evidence = await retriever.retrieve(
            claim(claim_type=ClaimType.PRICE, attribute=Attribute.PRICE),
            VerificationRequest(claim="x", reference_id="plan-pro"),
        )
        assert evidence.effective_attribute is Attribute.SUBSCRIPTION_PRICE
        assert evidence.has(Attribute.SUBSCRIPTION_PRICE)

    async def test_subscription_claim_about_a_product_redirects_to_price(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(
                claim_type=ClaimType.SUBSCRIPTION_TERMS,
                attribute=Attribute.SUBSCRIPTION_PRICE,
            ),
            VerificationRequest(claim="x", reference_id="prod-headphones-1"),
        )
        assert evidence.effective_attribute is Attribute.PRICE

    async def test_no_redirect_when_the_field_is_present(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="prod-headphones-1")
        )
        assert evidence.effective_attribute is Attribute.PRICE


class TestEvidenceMetadata:
    async def test_carries_record_identity_and_freshness(self, retriever) -> None:
        evidence = await retriever.retrieve(
            claim(), VerificationRequest(claim="x", reference_id="prod-headphones-1")
        )
        assert evidence.entity_type is EntityType.PRODUCT
        assert evidence.display_name == "Noise Cancelling Headphones"
        # Freshness is exposed in the audit trail so the evidence snapshot is
        # reproducible even though the service implements no response cache.
        assert evidence.record_updated_at is not None
