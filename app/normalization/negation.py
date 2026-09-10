"""Negation detection.

"Product X includes free shipping" and "Product X does not include free
shipping" differ by one cue and have opposite verdicts against the same
reference row. Getting this wrong produces the worst kind of error: a confident
verdict with correct-looking evidence attached.

The detector is deliberately conservative. It only fires on explicit
polarity-reversing cues, and it distinguishes them from vocabulary that merely
*contains* a negative word but asserts something positive — "out of stock" is a
positive claim about availability, and "no minimum purchase" negates a
requirement rather than the claim itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.normalization import text as textutil


@dataclass(frozen=True, slots=True)
class NegationMatch:
    negated: bool
    cue: str | None = None
    double: bool = False


#: Explicit polarity reversers. Word-boundary anchored so "cannot" does not
#: match inside another word and "no" does not match inside "noise".
_NEGATION_PATTERNS: tuple[str, ...] = (
    r"\bdoes\s*n[o']?t\b",
    r"\bdo\s*n[o']?t\b",
    r"\bis\s*n[o']?t\b",
    r"\bare\s*n[o']?t\b",
    r"\bwas\s*n[o']?t\b",
    r"\bwo\s*n[o']?t\b",
    r"\bcan\s*n[o']?t\b",
    r"\bcannot\b",
    r"\bnot\s+(?:include|included|available|eligible|offered|valid|covered)\b",
    r"\bwithout\b",
    r"\bexcludes?\b",
    r"\bexcluded\b",
    r"\blacks?\b",
    r"\bnever\b",
    r"\bno\s+(?:free|longer|included)\b",
    r"\bnone\s+of\b",
    r"\bunavailable\b",
    r"\bineligible\b",
)

#: Phrases that look negative but are positive assertions about a field value.
#: They are removed before scanning so they cannot trip a cue.
_POSITIVE_DESPITE_NEGATIVE_WORDING: tuple[str, ...] = (
    "out of stock",
    "sold out",
    "no minimum purchase",
    "no minimum order",
    "no shipping cost",
    "no additional cost",
    "no extra charge",
    "no annual contract",
    "no credit card required",
    "no cap on",
    "no limits on",
    "noise cancellation",
    "noise cancelling",
    "noise canceling",
)

_COMPILED = tuple(re.compile(pattern) for pattern in _NEGATION_PATTERNS)


def _merge_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Collapse overlapping matches into one cue each.

    Several patterns intentionally overlap on the same words: "does not
    include" is matched both by the auxiliary cue ``does not`` and by the
    verb-specific cue ``not include``. Counting those as two negations would
    read the phrase as a double negative and silently invert the claim, so
    overlapping spans are merged before anything is counted.
    """
    merged: list[tuple[int, int, str]] = []
    for start, end, cue in sorted(spans):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end, previous_cue = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), previous_cue)
        else:
            merged.append((start, end, cue))
    return merged


def detect_negation(text: str) -> NegationMatch:
    """Determine whether the claim asserts the negative of its attribute.

    An even number of *distinct* cues is treated as not negated ("it isn't
    true that the plan lacks SSO"). Double negation is rare in commercial copy,
    but reporting it via :attr:`NegationMatch.double` lets the caller route the
    claim to the semantic rater instead of guessing, since it is exactly the
    construction deterministic parsing should not be trusted with.
    """
    folded = textutil.fold(text)
    for phrase in _POSITIVE_DESPITE_NEGATIVE_WORDING:
        # Replaced with spaces of equal length so recorded offsets stay aligned
        # with the string actually being scanned.
        folded = folded.replace(phrase, " " * len(phrase))

    spans = [
        (match.start(), match.end(), match.group(0).strip())
        for pattern in _COMPILED
        for match in pattern.finditer(folded)
    ]
    cues = _merge_spans(spans)
    if not cues:
        return NegationMatch(negated=False)

    double = len(cues) % 2 == 0
    return NegationMatch(negated=not double, cue=cues[0][2], double=double)
