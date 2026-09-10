"""Unicode-safe text normalization shared by entity matching and claim parsing.

Claims arrive from real surfaces: marketing copy, scraped pages, spreadsheets.
That means curly quotes, non-breaking spaces, en/em dashes, full-width digits,
and accented brand names. Normalizing all of it once, here, keeps the rest of
the pipeline free of defensive string handling and prevents "formatting
difference" from ever being mistaken for a factual contradiction.
"""

from __future__ import annotations

import re
import unicodedata

#: Characters that different sources use interchangeably. Mapped to ASCII so
#: that "20 % off—today" and "20% off - today" tokenize identically.
_CHAR_FOLDS = {
    "\u00a0": " ",  # no-break space
    "\u202f": " ",  # narrow no-break space
    "\u2009": " ",  # thin space
    "\u200b": "",  # zero-width space
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2212": "-",  # minus sign
    "\u2044": "/",  # fraction slash
    "\u2026": "...",
}

_TRANSLATION = str.maketrans(_CHAR_FOLDS)

_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")

#: Words that carry no entity-identifying signal. Kept deliberately short:
#: over-aggressive stopword removal destroys product names like "The Pro Plan".
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "this",
        "to",
        "with",
        "you",
        "your",
    }
)


def clean(text: str) -> str:
    """Fold lookalike characters and collapse whitespace, preserving case."""
    folded = unicodedata.normalize("NFKC", text).translate(_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", folded).strip()


def fold(text: str) -> str:
    """Case-insensitive, accent-insensitive form used for equality and indexing.

    ``casefold`` rather than ``lower`` so non-English text ("STRASSE" vs
    "straße") compares correctly. Accents are stripped so "Café Blend" matches
    a catalog entry stored as "Cafe Blend".
    """
    cleaned = clean(text).casefold()
    decomposed = unicodedata.normalize("NFD", cleaned)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return unicodedata.normalize("NFC", stripped)


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Split folded text into alphanumeric tokens.

    Decimal numbers survive as single tokens (``19.99`` does not become
    ``19``/``99``) because a split price would produce nonsense matches.
    """
    tokens = _TOKEN_RE.findall(fold(text))
    if drop_stopwords:
        return [token for token in tokens if token not in STOPWORDS]
    return tokens


def token_set(text: str, *, drop_stopwords: bool = True) -> frozenset[str]:
    return frozenset(tokenize(text, drop_stopwords=drop_stopwords))


def slug(text: str) -> str:
    """Compact identifier form: ``"AeroPods Pro 2"`` -> ``"aeropods-pro-2"``."""
    return "-".join(tokenize(text, drop_stopwords=False))


def squash(text: str) -> str:
    """Alphanumerics only, for identifier comparison.

    Lets ``SKU-1234``, ``sku 1234`` and ``sku1234`` resolve to the same entity,
    which is how identifiers are written in practice across systems.
    """
    return "".join(ch for ch in fold(text) if ch.isalnum())
