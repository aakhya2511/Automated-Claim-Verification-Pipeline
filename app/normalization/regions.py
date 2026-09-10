"""Region name and code normalization.

"available in the US", "for customers in the United States" and "US-only" all
refer to the same ISO code stored in ``eligible_regions``. Region claims are
also one of the easier places for a model to be wrong in a plausible-looking
way ("Canada is in North America, so a US claim is fine"), so resolution is
deterministic and the containment check is exact.
"""

from __future__ import annotations

import re

from app.normalization import text as textutil

#: Surface form -> ISO 3166-1 alpha-2 (plus the EU bloc pseudo-code used by
#: the catalog). Longest-first matching happens in :func:`detect_region`.
REGION_ALIASES: dict[str, str] = {
    "us": "US",
    "usa": "US",
    "u s": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "stateside": "US",
    "ca": "CA",
    "can": "CA",
    "canada": "CA",
    "canadian": "CA",
    "gb": "GB",
    "uk": "GB",
    "united kingdom": "GB",
    "britain": "GB",
    "great britain": "GB",
    "england": "GB",
    "de": "DE",
    "germany": "DE",
    "german": "DE",
    "fr": "FR",
    "france": "FR",
    "french": "FR",
    "mx": "MX",
    "mexico": "MX",
    "br": "BR",
    "brazil": "BR",
    "au": "AU",
    "australia": "AU",
    "australian": "AU",
    "nz": "NZ",
    "new zealand": "NZ",
    "eu": "EU",
    "europe": "EU",
    "european union": "EU",
    "jp": "JP",
    "japan": "JP",
}

#: Bare two-letter tokens are only treated as regions when a region cue is
#: present. Otherwise "CA" inside a product name or "de" in a foreign phrase
#: would silently become a geographic assertion.
_AMBIGUOUS_SHORT_CODES: frozenset[str] = frozenset(
    {"us", "ca", "gb", "de", "fr", "mx", "br", "au", "nz", "eu", "jp", "can", "u s"}
)

_ISO_CODE_LENGTH = 2

REGION_CUES: tuple[str, ...] = (
    "available in",
    "available to",
    "eligible in",
    "eligible for customers in",
    "valid in",
    "offered in",
    "customers in",
    "residents of",
    "shoppers in",
    "ships to",
    "shipping to",
    "only in",
    "restricted to",
    "limited to",
    "buyers in",
    "applies to",
)


#: Longest alias first so "united states" wins over a bare "us" inside it, and
#: word-boundary anchored so trailing punctuation ("...United States.") and
#: hyphenation ("US-only") still match.
_ALIAS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{re.escape(alias)}\b"), code)
    for alias, code in sorted(REGION_ALIASES.items(), key=lambda item: -len(item[0]))
)


def has_region_cue(text: str) -> bool:
    folded = textutil.fold(text)
    return any(cue in folded for cue in REGION_CUES)


def normalize_region(value: str) -> str | None:
    """Resolve a single region string to a canonical code."""
    folded = textutil.fold(value).strip()
    if not folded:
        return None
    if folded in REGION_ALIASES:
        return REGION_ALIASES[folded]
    if len(folded) == _ISO_CODE_LENGTH and folded.upper() in set(REGION_ALIASES.values()):
        return folded.upper()
    return None


def detect_region(text: str, *, require_cue: bool = True) -> str | None:
    """Find the region a claim is about.

    ``require_cue`` guards the ambiguous two-letter forms: without a phrase
    like "available in", a bare "CA" is far more likely to be part of a name
    than a geographic assertion.
    """
    folded = textutil.fold(text)
    cue_present = has_region_cue(text)

    for pattern, code in _ALIAS_PATTERNS:
        match = pattern.search(folded)
        if match is None:
            continue
        if match.group(0) in _AMBIGUOUS_SHORT_CODES and require_cue and not cue_present:
            continue
        return code
    return None
