"""Text normalization: the layer that must never turn formatting into meaning."""

from __future__ import annotations

import pytest
from app.normalization import text as textutil


class TestClean:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  spaced   out  ", "spaced out"),
            ("non\u00a0breaking", "non breaking"),
            ("curly \u2019quotes\u2019", "curly 'quotes'"),
            ("en\u2013dash and em\u2014dash", "en-dash and em-dash"),
            ("zero\u200bwidth", "zerowidth"),
            ("20 % off\u2026", "20 % off..."),
        ],
    )
    def test_folds_lookalike_characters(self, raw: str, expected: str) -> None:
        assert textutil.clean(raw) == expected

    def test_preserves_case(self) -> None:
        assert textutil.clean("AirPods Pro") == "AirPods Pro"


class TestFold:
    def test_is_case_insensitive(self) -> None:
        assert textutil.fold("AirPods PRO") == textutil.fold("airpods pro")

    def test_strips_accents(self) -> None:
        assert textutil.fold("Café Blend") == "cafe blend"

    def test_uses_casefold_not_lower(self) -> None:
        # `lower()` leaves "ß" alone; casefold maps it to "ss".
        assert textutil.fold("STRASSE") == textutil.fold("Straße")


class TestTokenize:
    def test_keeps_decimals_intact(self) -> None:
        # Splitting 19.99 into 19/99 would create nonsense lexical matches.
        assert "19.99" in textutil.tokenize("price is $19.99")

    def test_drops_stopwords_by_default(self) -> None:
        assert textutil.tokenize("the price of the plan") == ["price", "plan"]

    def test_can_retain_stopwords(self) -> None:
        tokens = textutil.tokenize("the pro plan", drop_stopwords=False)
        assert tokens == ["the", "pro", "plan"]

    def test_handles_empty_and_symbol_only_input(self) -> None:
        assert textutil.tokenize("") == []
        assert textutil.tokenize("$$$ ?!") == []

    def test_unicode_digits_normalize(self) -> None:
        # NFKC maps full-width digits to ASCII, so scraped copy still parses.
        # The literal below is intentionally full-width; that is the input.
        assert textutil.tokenize("１９９ dollars") == ["199", "dollars"]  # noqa: RUF001


class TestIdentifiers:
    @pytest.mark.parametrize("raw", ["SKU-1234", "sku 1234", "sku1234", "Sku_1234"])
    def test_squash_unifies_identifier_spellings(self, raw: str) -> None:
        assert textutil.squash(raw) == "sku1234"

    def test_slug_keeps_stopwords_for_stable_ids(self) -> None:
        assert textutil.slug("Back to School BACK159") == "back-to-school-back159"
