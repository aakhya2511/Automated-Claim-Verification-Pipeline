"""Feature phrase resolution against the catalog's canonical keys."""

from __future__ import annotations

import pytest
from app.normalization.features import FeatureResolver, humanize, vocabulary_from_records

VOCABULARY = (
    "active_noise_cancellation",
    "unlimited_api_requests",
    "sso",
    "audit_logs",
    "hepa_filter",
    "32_gb_ram",
    "16_gb_ram",
    "1_tb_ssd",
    "thunderbolt_4",
    "priority_support",
    "on_premise_option",
)


@pytest.fixture
def resolver() -> FeatureResolver:
    return FeatureResolver(VOCABULARY)


class TestCanonicalMatching:
    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            ("This laptop includes 32 GB RAM.", "32_gb_ram"),
            ("The Premium plan includes unlimited API requests.", "unlimited_api_requests"),
            ("Includes priority support.", "priority_support"),
            ("Comes with a HEPA filter.", "hepa_filter"),
            ("Includes Thunderbolt 4.", "thunderbolt_4"),
        ],
    )
    def test_resolves_prose_to_canonical_key(
        self, resolver: FeatureResolver, claim: str, expected: str
    ) -> None:
        match = resolver.resolve(claim)
        assert match is not None
        assert match.key == expected


class TestSynonyms:
    @pytest.mark.parametrize(
        ("claim", "expected"),
        [
            ("These headphones have noise cancelling.", "active_noise_cancellation"),
            ("Supports ANC.", "active_noise_cancellation"),
            ("Includes single sign-on.", "sso"),
            ("Includes SAML SSO.", "sso"),
            ("Provides an audit trail.", "audit_logs"),
            ("Offers a self-hosted option.", "on_premise_option"),
        ],
    )
    def test_resolves_alternate_wordings(
        self, resolver: FeatureResolver, claim: str, expected: str
    ) -> None:
        match = resolver.resolve(claim)
        assert match is not None
        assert match.key == expected

    def test_synonyms_for_absent_features_are_ignored(self) -> None:
        # A stale synonym table entry must not resolve to a key this catalog
        # does not contain, or evidence lookup would come back empty.
        narrow = FeatureResolver(("sso",))
        assert narrow.resolve("Comes with a HEPA filter.") is None


class TestSpecificity:
    def test_prefers_the_exact_capacity(self, resolver: FeatureResolver) -> None:
        # A claim about 32 GB must never be answered with the 16 GB key.
        match = resolver.resolve("This laptop includes 32 GB RAM.")
        assert match is not None
        assert match.key == "32_gb_ram"

    def test_compact_spelling_matches(self, resolver: FeatureResolver) -> None:
        match = resolver.resolve("Includes 32GB RAM.")
        assert match is not None
        assert match.key == "32_gb_ram"

    def test_requires_all_tokens_of_a_multi_token_key(self, resolver: FeatureResolver) -> None:
        # "includes a filter" shares one token with hepa_filter. Accepting that
        # would fabricate evidence for a feature never mentioned.
        assert resolver.resolve("Includes a filter.") is None

    def test_unrelated_text_resolves_to_nothing(self, resolver: FeatureResolver) -> None:
        assert resolver.resolve("AirPods Pro are $199 today.") is None

    def test_short_alias_does_not_match_inside_brand_name(self, resolver: FeatureResolver) -> None:
        assert resolver.resolve("Volterra Tempo Pro includes a 4-year warranty.") is None

    def test_resolve_all_ranks_best_first(self, resolver: FeatureResolver) -> None:
        matches = resolver.resolve_all("Includes SSO and audit logs.")
        assert {match.key for match in matches} == {"sso", "audit_logs"}
        confidences = [match.confidence for match in matches]
        assert confidences == sorted(confidences, reverse=True)


class TestVocabularyConstruction:
    def test_built_from_the_catalog_not_hard_coded(self, repository) -> None:
        vocabulary = vocabulary_from_records(repository.all_records())
        # Both included and excluded features are part of the vocabulary: an
        # exclusion claim has to resolve too.
        assert "active_noise_cancellation" in vocabulary
        assert "lossless_audio" in vocabulary
        assert "sso" in vocabulary

    def test_humanize_is_readable_for_explanations(self) -> None:
        assert humanize("32_gb_ram") == "32 gb ram"
