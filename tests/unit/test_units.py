"""Value parsers. Pure functions, so the awkward cases are tested exhaustively."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from app.normalization import units

AS_OF = date(2026, 9, 15)


class TestParseMoney:
    @pytest.mark.parametrize(
        ("text", "amount", "currency"),
        [
            ("$199.99", Decimal("199.99"), "USD"),
            ("$199", Decimal("199"), "USD"),
            ("costs $1,299.99 today", Decimal("1299.99"), "USD"),
            ("199.99 USD", Decimal("199.99"), "USD"),
            ("USD 199.99", Decimal("199.99"), "USD"),
            ("199 dollars", Decimal("199"), "USD"),
            ("£99.50", Decimal("99.50"), "GBP"),
            ("€49.99", Decimal("49.99"), "EUR"),
            ("C$129", Decimal("129"), "CAD"),
        ],
    )
    def test_parses_common_forms(self, text: str, amount: Decimal, currency: str) -> None:
        parsed = units.parse_money(text)
        assert parsed is not None
        assert parsed.amount == amount
        assert parsed.currency == currency

    def test_zero_is_a_real_amount(self) -> None:
        # "$0" must parse, not fall through as "no price stated": a $0 claim is
        # verifiable and is a common corruption target.
        parsed = units.parse_money("This add-on is $0 today")
        assert parsed is not None
        assert parsed.amount == Decimal("0")

    def test_decimal_comma_locale(self) -> None:
        parsed = units.parse_money("€1.299,99")
        assert parsed is not None
        assert parsed.amount == Decimal("1299.99")

    def test_bare_number_is_not_money(self) -> None:
        # Whether "199" is a price is the caller's decision, not the parser's.
        assert units.parse_money("the number is 199") is None

    def test_unknown_symbol_is_not_coerced_to_usd(self) -> None:
        assert units.parse_money("₹4999") is None

    def test_no_float_error_in_result(self) -> None:
        parsed = units.parse_money("$19.99")
        assert parsed is not None
        assert parsed.amount == Decimal("19.99")
        assert str(parsed.amount) == "19.99"

    def test_returns_none_for_empty(self) -> None:
        assert units.parse_money("") is None


class TestParsePercent:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("20% off", Decimal("20")),
            ("20 % off", Decimal("20")),
            ("20 percent off", Decimal("20")),
            ("12.5% off", Decimal("12.5")),
            ("0% APR", Decimal("0")),
            ("100% cotton", Decimal("100")),
        ],
    )
    def test_parses_percentages(self, text: str, expected: Decimal) -> None:
        assert units.parse_percent(text) == expected

    def test_zero_percent_is_distinguishable_from_absent(self) -> None:
        # Returning Optional rather than defaulting to 0 is what keeps
        # "0% off" separable from "no percentage mentioned".
        assert units.parse_percent("0% off") == Decimal("0")
        assert units.parse_percent("great deal") is None

    def test_rejects_out_of_range(self) -> None:
        assert units.parse_percent("150% off") is None

    def test_bare_number_is_not_a_percent(self) -> None:
        assert units.parse_percent("20 dollars off") is None


class TestParseDurationDays:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("30-day free trial", 30),
            ("30 day trial", 30),
            ("30 days", 30),
            ("14-day trial", 14),
            ("2 weeks", 14),
            ("one month free", 30),
            ("a month", 30),
            ("6 weeks", 42),
            ("1 year", 365),
            ("0 days", 0),
        ],
    )
    def test_normalizes_to_days(self, text: str, expected: int) -> None:
        assert units.parse_duration_days(text) == expected

    def test_returns_none_without_a_duration(self) -> None:
        assert units.parse_duration_days("includes a free trial") is None


class TestParseDate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("2026-09-30", date(2026, 9, 30)),
            ("through September 30", date(2026, 9, 30)),
            ("through Sept 30, 2026", date(2026, 9, 30)),
            ("through Sep. 30", date(2026, 9, 30)),
            ("30 September 2026", date(2026, 9, 30)),
            ("9/30/2026", date(2026, 9, 30)),
            ("9/30", date(2026, 9, 30)),
        ],
    )
    def test_parses_common_forms(self, text: str, expected: date) -> None:
        assert units.parse_date(text, as_of=AS_OF) == expected

    def test_iso_wins_over_ambiguous_numeric(self) -> None:
        # "2026-09-30" must never be read month-first.
        assert units.parse_date("2026-09-30", as_of=AS_OF) == date(2026, 9, 30)

    def test_bare_date_far_in_the_past_rolls_to_next_year(self) -> None:
        # In September, "through February 10" cannot mean February just gone;
        # it means the upcoming one.
        assert units.parse_date("through February 10", as_of=AS_OF) == date(2027, 2, 10)

    def test_bare_date_recently_past_stays_in_current_year(self) -> None:
        # A recently-expired promotion is entirely plausible, so a bare date
        # just behind us keeps the current year rather than jumping forward and
        # manufacturing a date mismatch.
        assert units.parse_date("ended September 1", as_of=AS_OF) == date(2026, 9, 1)

    def test_bare_future_date_stays_in_current_year(self) -> None:
        assert units.parse_date("through December 31", as_of=AS_OF) == date(2026, 12, 31)

    def test_two_digit_year(self) -> None:
        assert units.parse_date("9/30/26", as_of=AS_OF) == date(2026, 9, 30)

    def test_impossible_date_returns_none(self) -> None:
        assert units.parse_date("February 30, 2026", as_of=AS_OF) is None

    def test_month_thirteen_is_not_a_date(self) -> None:
        assert units.parse_date("13/45", as_of=AS_OF) is None

    def test_no_date_returns_none(self) -> None:
        assert units.parse_date("no dates here", as_of=AS_OF) is None


class TestMonthDaySpan:
    def test_parses_explicit_range(self) -> None:
        start, end = units.month_day_span("valid from September 1 to September 30", as_of=AS_OF)
        assert start == date(2026, 9, 1)
        assert end == date(2026, 9, 30)

    def test_single_date_is_treated_as_an_end_bound(self) -> None:
        # "through September 30" states when the offer stops, not when it began.
        start, end = units.month_day_span("through September 30", as_of=AS_OF)
        assert start is None
        assert end == date(2026, 9, 30)

    def test_range_crossing_new_year_does_not_invert(self) -> None:
        start, end = units.month_day_span("from December 20 to January 5", as_of=date(2026, 12, 1))
        assert start is not None and end is not None
        assert end > start

    def test_no_dates(self) -> None:
        assert units.month_day_span("no dates", as_of=AS_OF) == (None, None)


class TestExplicitYear:
    @pytest.mark.parametrize(
        "text",
        [
            "valid through 2026-09-30",
            "through September 30, 2026",
            "through Sept 30 2026",
            "30 September 2026",
            "9/30/2026",
            "9/30/26",
        ],
    )
    def test_detects_a_stated_year(self, text: str) -> None:
        assert units.date_year_is_explicit(text) is True

    @pytest.mark.parametrize(
        "text",
        ["through September 30", "ends Sep 30", "9/30", "valid until December 31"],
    )
    def test_detects_an_absent_year(self, text: str) -> None:
        # These force the date rules into a month/day comparison, so a year
        # guessed from `as_of` can never create a mismatch on its own.
        assert units.date_year_is_explicit(text) is False


class TestParseQuantity:
    def test_extracts_number_and_unit(self) -> None:
        assert units.parse_quantity("includes 32 GB RAM") == (Decimal("32"), "gb")

    def test_returns_none_without_a_quantity(self) -> None:
        assert units.parse_quantity("includes RAM") is None
