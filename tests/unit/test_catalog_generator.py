"""Reference-catalog generator: determinism and internal consistency.

The evaluation set is derived from this catalog, so a self-contradictory record
(a feature both included and excluded, an offer ending before it starts) would
show up later as an unexplainable model error. These invariants are checked
here so that never happens silently.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest
from app.datagen.catalog import ANCHOR_DATE, generate_catalog, serialize_catalog
from app.domain.enums import EntityType
from app.retrieval.repository import InMemoryReferenceRepository


@pytest.fixture(scope="module")
def catalog():
    return generate_catalog()


class TestDeterminism:
    def test_same_seed_produces_identical_output(self) -> None:
        assert serialize_catalog(generate_catalog(seed=7)) == serialize_catalog(
            generate_catalog(seed=7)
        )

    def test_different_seed_produces_different_output(self) -> None:
        assert serialize_catalog(generate_catalog(seed=7)) != serialize_catalog(
            generate_catalog(seed=8)
        )

    def test_committed_catalog_matches_generator(self, full_catalog_path) -> None:
        # Guards against someone editing data/reference/catalog.jsonl by hand.
        assert full_catalog_path.read_text(encoding="utf-8") == serialize_catalog(
            generate_catalog()
        )


class TestComposition:
    def test_expected_size_and_mix(self, catalog) -> None:
        counts = Counter(record.entity_type for record in catalog)
        assert len(catalog) == 420
        assert counts[EntityType.PRODUCT] == 240
        assert counts[EntityType.PLAN] == 90
        assert counts[EntityType.OFFER] == 90

    def test_record_ids_are_unique(self, catalog) -> None:
        ids = [record.record_id for record in catalog]
        assert len(set(ids)) == len(ids)

    def test_catalog_loads_into_the_repository(self, catalog) -> None:
        repository = InMemoryReferenceRepository(catalog)
        assert repository.stats()["records"] == 420


class TestRecordConsistency:
    def test_features_never_both_included_and_excluded(self, catalog) -> None:
        for record in catalog:
            overlap = set(record.included_features) & set(record.excluded_features)
            assert not overlap, f"{record.record_id} has contradictory features: {overlap}"

    def test_offer_windows_are_ordered(self, catalog) -> None:
        for record in catalog:
            if record.offer_start and record.offer_end:
                assert record.offer_end >= record.offer_start

    def test_free_shipping_implies_zero_cost(self, catalog) -> None:
        for record in catalog:
            if record.free_shipping and record.shipping_cost is not None:
                assert record.shipping_cost == Decimal("0.00")

    def test_prices_are_exact_two_decimal_values(self, catalog) -> None:
        for record in catalog:
            for value in (record.price, record.subscription_price, record.shipping_cost):
                if value is not None:
                    assert value == value.quantize(Decimal("0.01"))

    def test_every_record_has_a_resolvable_name(self, catalog) -> None:
        for record in catalog:
            assert record.display_name


class TestDateSpread:
    def test_offers_span_expired_active_and_future_windows(self, catalog) -> None:
        offers = [r for r in catalog if r.entity_type is EntityType.OFFER]
        expired = [r for r in offers if r.offer_end and r.offer_end < ANCHOR_DATE]
        future = [r for r in offers if r.offer_start and r.offer_start > ANCHOR_DATE]
        active = [
            r
            for r in offers
            if r.offer_start and r.offer_end and r.offer_start <= ANCHOR_DATE <= r.offer_end
        ]
        # All three branches must be populated or the date rules are untested.
        assert expired and active and future

    def test_trial_days_include_zero_and_missing(self, catalog) -> None:
        plans = [r for r in catalog if r.entity_type is EntityType.PLAN]
        assert any(plan.trial_days == 0 for plan in plans)
        assert any(plan.trial_days is None for plan in plans)


class TestAliases:
    def test_products_carry_alternate_surface_forms(self, catalog) -> None:
        products = [r for r in catalog if r.entity_type is EntityType.PRODUCT]
        assert sum(1 for product in products if product.aliases) / len(products) > 0.9

    def test_flagship_plan_tier_alias_is_unambiguous(self, catalog) -> None:
        plans = [r for r in catalog if r.entity_type is EntityType.PLAN]
        bare_tier_aliases = [
            alias for plan in plans for alias in plan.aliases if alias == "Pro plan"
        ]
        # Exactly one record may answer to a bare "<Tier> plan".
        assert len(bare_tier_aliases) == 1
