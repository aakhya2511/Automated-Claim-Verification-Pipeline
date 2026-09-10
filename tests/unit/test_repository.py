"""Reference repository: lookup, entity resolution, and failure behaviour."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.exceptions import RepositoryError
from app.domain.enums import Attribute, EntityType
from app.domain.models import ReferenceRecord
from app.retrieval.repository import InMemoryReferenceRepository


class TestLookup:
    async def test_get_by_id(self, repository: InMemoryReferenceRepository) -> None:
        record = await repository.get_by_id("plan-pro")
        assert record is not None
        assert record.plan_name == "Pro"
        assert record.trial_days == 30

    async def test_get_by_id_tolerates_identifier_drift(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        # Callers pass ids from spreadsheets and URLs; case and punctuation vary.
        assert await repository.get_by_id("PLAN_PRO") is not None
        assert await repository.get_by_id("planpro") is not None

    async def test_get_by_id_unknown_returns_none(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        assert await repository.get_by_id("plan-does-not-exist") is None

    @pytest.mark.parametrize("sku", ["SKU-123", "sku 123", "sku123"])
    async def test_get_by_sku(self, repository: InMemoryReferenceRepository, sku: str) -> None:
        record = await repository.get_by_sku(sku)
        assert record is not None
        assert record.record_id == "prod-headphones-1"

    async def test_offer_does_not_shadow_its_parent_product(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        # `offer-expired-1` denormalizes SKU-123 and the headphones' name for
        # evidence display. Identifiers must still resolve to the product.
        assert (await repository.get_by_sku("SKU-123")).record_id == "prod-headphones-1"
        assert (
            await repository.get_by_name("Noise Cancelling Headphones")
        ).record_id == "prod-headphones-1"

    async def test_offer_is_reachable_by_its_own_code(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        record = await repository.get_by_name("SAVE20")
        assert record is not None
        assert record.record_id == "offer-expired-1"

    async def test_get_by_name_matches_alias(self, repository: InMemoryReferenceRepository) -> None:
        record = await repository.get_by_name("NC Headphones")
        assert record is not None
        assert record.record_id == "prod-headphones-1"

    async def test_get_by_name_is_accent_and_case_insensitive(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        record = await repository.get_by_name("café blend grinder")
        assert record is not None
        assert record.record_id == "prod-sparse-1"


class TestSearch:
    async def test_ranks_exact_product_name_first(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        results = await repository.search("Noise Cancelling Headphones", limit=3)
        assert results
        assert results[0][0].record_id == "prod-headphones-1"
        assert results[0][1] > 0.5

    async def test_returns_empty_for_unmatched_query(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        assert await repository.search("quantum flux capacitor") == []

    async def test_returns_empty_for_stopword_only_query(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        assert await repository.search("the and of") == []

    async def test_respects_limit(self, repository: InMemoryReferenceRepository) -> None:
        assert len(await repository.search("Aurex", limit=1)) <= 1

    async def test_scores_are_bounded_and_sorted(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        results = await repository.search("Vector Pro 14 laptop", limit=5)
        scores = [score for _, score in results]
        assert all(0.0 <= score <= 1.0 for score in scores)
        assert scores == sorted(scores, reverse=True)

    async def test_ranking_is_deterministic_across_calls(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        # Candidate generation walks a set, so ties are broken by record_id to
        # keep evaluation runs reproducible.
        first = await repository.search("Aurex headphones", limit=5)
        second = await repository.search("Aurex headphones", limit=5)
        assert [r.record_id for r, _ in first] == [r.record_id for r, _ in second]


class TestLoadFailures:
    async def test_missing_file_raises_repository_error(self, tmp_path) -> None:
        with pytest.raises(RepositoryError, match="not found"):
            InMemoryReferenceRepository.from_jsonl(tmp_path / "nope.jsonl")

    async def test_malformed_line_raises_with_line_number(self, tmp_path) -> None:
        valid = '{"record_id": "a", "entity_type": "product", "updated_at": "2026-01-01T00:00:00Z"}'
        path = tmp_path / "bad.jsonl"
        path.write_text(f"{valid}\nnot json\n")
        with pytest.raises(RepositoryError) as excinfo:
            InMemoryReferenceRepository.from_jsonl(path)
        # A partially loaded source of truth is worse than no service at all.
        assert excinfo.value.details["line"] == 2

    async def test_schema_violation_is_reported_with_line_number(self, tmp_path) -> None:
        path = tmp_path / "schema.jsonl"
        # Missing the required `updated_at`.
        path.write_text('{"record_id": "a", "entity_type": "product"}\n')
        with pytest.raises(RepositoryError) as excinfo:
            InMemoryReferenceRepository.from_jsonl(path)
        assert excinfo.value.details["line"] == 1

    async def test_empty_file_raises(self, tmp_path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("\n\n")
        with pytest.raises(RepositoryError, match="empty"):
            InMemoryReferenceRepository.from_jsonl(path)

    async def test_duplicate_record_id_raises(self) -> None:
        record = ReferenceRecord(
            record_id="dupe",
            entity_type=EntityType.PRODUCT,
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(RepositoryError, match="duplicate"):
            InMemoryReferenceRepository([record, record])


class TestStats:
    async def test_health_and_counts(self, repository: InMemoryReferenceRepository) -> None:
        assert await repository.health_check() is True
        assert await repository.count() == 7
        stats = repository.stats()
        assert stats["by_entity_type"] == {"offer": 2, "plan": 2, "product": 3}


class TestRecordAttributes:
    async def test_absent_field_reports_as_unset(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        record = await repository.get_by_id("prod-sparse-1")
        assert record is not None
        # A sparse record must report "silent", not a falsy value: the
        # difference decides CONTRADICTED vs INSUFFICIENT_EVIDENCE.
        assert record.has_attribute(Attribute.PRICE) is True
        assert record.has_attribute(Attribute.TRIAL_DAYS) is False
        assert record.get_attribute(Attribute.INCLUDED_FEATURES) is None

    async def test_zero_and_false_are_present_not_missing(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        premium = await repository.get_by_id("plan-premium")
        assert premium is not None
        # trial_days == 0 means "no trial", which is a verifiable fact.
        assert premium.trial_days == 0
        assert premium.has_attribute(Attribute.TRIAL_DAYS) is True

        offer = await repository.get_by_id("offer-active-1")
        assert offer is not None
        assert offer.free_shipping is False
        assert offer.has_attribute(Attribute.FREE_SHIPPING) is True

    async def test_price_is_exact_decimal(self, repository: InMemoryReferenceRepository) -> None:
        record = await repository.get_by_id("prod-headphones-1")
        assert record is not None
        assert record.price == Decimal("199.99")

    async def test_display_name_prefers_product_then_plan(
        self, repository: InMemoryReferenceRepository
    ) -> None:
        product = await repository.get_by_id("prod-laptop-1")
        plan = await repository.get_by_id("plan-pro")
        assert product is not None and plan is not None
        assert product.display_name == "Vector Pro 14"
        assert plan.display_name == "Pro"
