"""Parsers for the value types commercial claims actually contain.

Money, percentages, durations and dates are parsed here and nowhere else, so
that "$1,299.99", "1299.99 USD" and "USD 1,299.99" produce one canonical value
and a price comparison never depends on how the sentence was written.

Everything in this module is a pure function over a string. That makes the
tricky cases — ``$0``, ``0%``, ``up to 20%``, decimal commas, a bare
"September 30" that means *next* September — cheap to test exhaustively without
constructing a pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from app.normalization import text as textutil

#: Symbols and ISO codes we accept. Deliberately a closed set: silently
#: treating an unknown symbol as USD would turn a currency mismatch into a
#: price comparison against the wrong unit.
CURRENCY_SYMBOLS: dict[str, str] = {
    "$": "USD",
    "us$": "USD",
    "c$": "CAD",
    "ca$": "CAD",
    "a$": "AUD",
    "£": "GBP",
    "€": "EUR",
    "¥": "JPY",
}

CURRENCY_CODES: frozenset[str] = frozenset({"usd", "cad", "aud", "gbp", "eur", "jpy", "chf", "nzd"})

CURRENCY_WORDS: dict[str, str] = {
    "dollar": "USD",
    "dollars": "USD",
    "bucks": "USD",
    "pound": "GBP",
    "pounds": "GBP",
    "euro": "EUR",
    "euros": "EUR",
    "yen": "JPY",
}

#: Small-number words appear constantly in trial and shipping copy
#: ("a 30 day trial" vs "a one month trial").
NUMBER_WORDS: dict[str, int] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fourteen": 14,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "sixty": 60,
    "ninety": 90,
}

#: Calendar months are irregular, but subscription copy treats "a month" as 30
#: days and "a year" as 365. Matching that convention is what the reference
#: data itself uses (``trial_days``), so converting here keeps the comparison
#: apples-to-apples rather than introducing a fake off-by-one.
_DURATION_UNIT_DAYS: dict[str, int] = {
    "day": 1,
    "days": 1,
    "week": 7,
    "weeks": 7,
    "month": 30,
    "months": 30,
    "year": 365,
    "years": 365,
}

MONTHS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_CURRENCY_SYMBOL_CLASS = "|".join(re.escape(symbol) for symbol in CURRENCY_SYMBOLS)
_MONTH_CLASS = "|".join(sorted(MONTHS, key=len, reverse=True))
_NUMBER_WORD_CLASS = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))

#: A number with optional thousands separators and optional decimals. Both
#: "1,299.99" (en) and "1.299,99" (de) appear in real feeds, so `.` and `,` are
#: each allowed in either role; :func:`_to_decimal` decides which is the
#: decimal separator from its position rather than from a locale we don't have.
#: Grouping requires exactly three following digits, which is what keeps
#: "12.5%" from being read as a grouped "125".
_NUMBER = r"\d{1,3}(?:[.,\u202f\s]\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?"

_MONEY_PREFIX_RE = re.compile(
    rf"(?P<symbol>{_CURRENCY_SYMBOL_CLASS})\s*(?P<amount>{_NUMBER})",
    re.IGNORECASE,
)
_MONEY_SUFFIX_RE = re.compile(
    rf"(?P<amount>{_NUMBER})\s*(?P<code>[a-z]{{3}}|dollars?|bucks|pounds?|euros?|yen)\b",
    re.IGNORECASE,
)
_MONEY_CODE_PREFIX_RE = re.compile(
    rf"\b(?P<code>[a-z]{{3}})\s*(?P<amount>{_NUMBER})",
    re.IGNORECASE,
)

_PERCENT_RE = re.compile(
    rf"(?P<amount>{_NUMBER})\s*(?:%|percent|pct)\b|(?P<amount2>{_NUMBER})\s*%",
    re.IGNORECASE,
)

_DURATION_RE = re.compile(
    rf"\b(?P<count>{_NUMBER}|{_NUMBER_WORD_CLASS})[\s-]*"
    rf"(?P<unit>day|days|week|weeks|month|months|year|years)\b",
    re.IGNORECASE,
)

_ISO_DATE_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_NUMERIC_DATE_RE = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>\d{2,4}))?\b")
_MONTH_DAY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_CLASS})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)
_DAY_MONTH_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<month>{_MONTH_CLASS})\.?"
    rf"(?:,?\s*(?P<year>\d{{4}}))?\b",
    re.IGNORECASE,
)

#: Bare "September 30" is read as the *upcoming* 30 September. A promotional
#: end date more than this far in the past almost certainly means next year,
#: which is how a reader interprets "20% off through September 30" in December.
_BACKDATE_TOLERANCE_DAYS = 120

_MAX_PERCENT = Decimal("100")
_MONTHS_IN_YEAR = 12
#: Two-digit years in commercial feeds are always this century.
_TWO_DIGIT_YEAR_CUTOFF = 100
_CENTURY_BASE = 2000


@dataclass(frozen=True, slots=True)
class MoneyValue:
    """A parsed monetary amount.

    ``currency is None`` means the text carried a bare number with no unit.
    That is not the same as USD: the rule engine treats an explicit mismatched
    currency as a contradiction but an absent one as "assume the reference
    currency", which is the only reading that does not invent information.
    """

    amount: Decimal
    currency: str | None = None


def _to_decimal(raw: str) -> Decimal | None:
    """Parse a localized number string into an exact ``Decimal``.

    Handles ``1,299.99``, ``1.299,99``, ``1 299,99`` and plain ``19.99`` by
    deciding which separator is decimal from its position, not from a locale
    setting we do not have.
    """
    cleaned = raw.replace("\u202f", "").replace(" ", "")
    if not cleaned:
        return None

    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")

    if last_comma > last_dot:
        # Comma is the decimal separator: strip dots, swap the comma.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Dot is decimal (or there is none): commas are grouping.
        cleaned = cleaned.replace(",", "")

    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _normalize_currency(token: str) -> str | None:
    lowered = token.lower().rstrip(".")
    if lowered in CURRENCY_SYMBOLS:
        return CURRENCY_SYMBOLS[lowered]
    if lowered in CURRENCY_WORDS:
        return CURRENCY_WORDS[lowered]
    if lowered in CURRENCY_CODES:
        return lowered.upper()
    return None


def parse_money(text: str) -> MoneyValue | None:
    """Extract the first monetary amount from ``text``.

    Tries symbol-prefixed ("$199.99"), then code/word-suffixed ("199.99 USD",
    "199 dollars"), then code-prefixed ("USD 199"). A bare number is *not*
    money — that decision belongs to the caller, which knows whether the claim
    is about a price at all.
    """
    cleaned = textutil.clean(text)

    prefix = _MONEY_PREFIX_RE.search(cleaned)
    if prefix:
        amount = _to_decimal(prefix.group("amount"))
        if amount is not None:
            return MoneyValue(amount=amount, currency=_normalize_currency(prefix.group("symbol")))

    suffix = _MONEY_SUFFIX_RE.search(cleaned)
    if suffix:
        currency = _normalize_currency(suffix.group("code"))
        amount = _to_decimal(suffix.group("amount"))
        if currency and amount is not None:
            return MoneyValue(amount=amount, currency=currency)

    code_prefix = _MONEY_CODE_PREFIX_RE.search(cleaned)
    if code_prefix:
        currency = _normalize_currency(code_prefix.group("code"))
        amount = _to_decimal(code_prefix.group("amount"))
        if currency and amount is not None:
            return MoneyValue(amount=amount, currency=currency)

    return None


def parse_percent(text: str) -> Decimal | None:
    """Extract a percentage. Returns ``None`` rather than guessing a bare number.

    ``0%`` parses to ``Decimal("0")``, which must remain distinguishable from
    "no percentage stated"; that is why the return is optional rather than a
    default of zero.
    """
    match = _PERCENT_RE.search(textutil.clean(text))
    if not match:
        return None
    raw = match.group("amount") or match.group("amount2")
    value = _to_decimal(raw) if raw else None
    if value is None or value < 0 or value > _MAX_PERCENT:
        return None
    return value


def parse_duration_days(text: str) -> int | None:
    """Convert a duration phrase to whole days.

    Accepts digits or number words with day/week/month/year units, so
    "30-day", "30 days", "one month" and "6 weeks" all normalize against the
    reference's integer ``trial_days``.
    """
    match = _DURATION_RE.search(textutil.clean(text))
    if not match:
        return None

    raw_count = match.group("count").lower()
    if raw_count in NUMBER_WORDS:
        count = Decimal(NUMBER_WORDS[raw_count])
    else:
        parsed = _to_decimal(raw_count)
        if parsed is None:
            return None
        count = parsed

    multiplier = _DURATION_UNIT_DAYS[match.group("unit").lower()]
    days = count * multiplier
    if days < 0:
        return None
    return int(days)


def _resolve_year(month: int, day: int, *, as_of: date, explicit_year: int | None) -> int:
    """Pick the year for a date that may not have stated one."""
    if explicit_year is not None:
        if explicit_year < _TWO_DIGIT_YEAR_CUTOFF:
            return _CENTURY_BASE + explicit_year
        return explicit_year

    candidate = _safe_date(as_of.year, month, day)
    if candidate is None:
        return as_of.year
    if (as_of - candidate).days > _BACKDATE_TOLERANCE_DAYS:
        return as_of.year + 1
    return as_of.year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        # February 30, month 13, and similar. Returning None lets the caller
        # treat it as unparsed rather than crashing on user input.
        return None


def parse_date(text: str, *, as_of: date) -> date | None:
    """Extract the first date, resolving a missing year relative to ``as_of``.

    ISO form wins over ambiguous numeric form so ``2026-09-30`` is never read
    as month 20. ``9/30/2026`` is interpreted US-style (month first), matching
    the locale of the reference data.
    """
    cleaned = textutil.clean(text)

    iso = _ISO_DATE_RE.search(cleaned)
    if iso:
        return _safe_date(int(iso.group("year")), int(iso.group("month")), int(iso.group("day")))

    for pattern in (_MONTH_DAY_RE, _DAY_MONTH_RE):
        match = pattern.search(cleaned)
        if match:
            month = MONTHS[match.group("month").lower().rstrip(".")]
            day = int(match.group("day"))
            raw_year = match.group("year")
            year = _resolve_year(
                month, day, as_of=as_of, explicit_year=int(raw_year) if raw_year else None
            )
            resolved = _safe_date(year, month, day)
            if resolved:
                return resolved

    numeric = _NUMERIC_DATE_RE.search(cleaned)
    if numeric:
        month = int(numeric.group("month"))
        day = int(numeric.group("day"))
        raw_year = numeric.group("year")
        if 1 <= month <= _MONTHS_IN_YEAR:
            year = _resolve_year(
                month, day, as_of=as_of, explicit_year=int(raw_year) if raw_year else None
            )
            return _safe_date(year, month, day)

    return None


_EXPLICIT_YEAR_RE = re.compile(
    rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b"
    rf"|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b"
    rf"|\b(?:{_MONTH_CLASS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s*\d{{4}}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH_CLASS})\.?,?\s*\d{{4}}\b",
    re.IGNORECASE,
)


def date_year_is_explicit(text: str) -> bool:
    """Whether a date in ``text`` states its year.

    Drives :attr:`TimeContext.year_inferred`. When the year is absent, the date
    rules drop to a month/day comparison so a year guessed from ``as_of``
    cannot manufacture a date mismatch.
    """
    return _EXPLICIT_YEAR_RE.search(textutil.clean(text)) is not None


def parse_quantity(text: str) -> tuple[Decimal, str] | None:
    """Extract a bare number with a trailing unit, e.g. ``("32", "gb")``.

    Used for spec-style feature claims ("includes 32 GB RAM") where the
    reference stores the whole spec as a feature key rather than a number.
    """
    match = re.search(rf"(?P<amount>{_NUMBER})\s*(?P<unit>[a-z]+)", textutil.fold(text))
    if not match:
        return None
    amount = _to_decimal(match.group("amount"))
    if amount is None:
        return None
    return amount, match.group("unit")


def month_day_span(text: str, *, as_of: date) -> tuple[date | None, date | None]:
    """Parse an explicit range such as "from September 1 to September 30".

    Returns ``(start, end)`` with either side possibly ``None``. The second
    date is resolved relative to the first so a range crossing New Year does
    not collapse into a backwards window.
    """
    cleaned = textutil.clean(text)
    matches: list[date] = []
    for pattern in (_ISO_DATE_RE, _MONTH_DAY_RE, _DAY_MONTH_RE, _NUMERIC_DATE_RE):
        for match in pattern.finditer(cleaned):
            parsed = parse_date(match.group(0), as_of=as_of)
            if parsed and parsed not in matches:
                matches.append(parsed)
        if matches:
            break

    if not matches:
        return None, None
    if len(matches) == 1:
        return None, matches[0]

    start, end = matches[0], matches[1]
    if end < start:
        end = end + timedelta(days=365)
    return start, end
