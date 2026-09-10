"""Hedge detection: "up to 20%", "starting at $99", "approximately 30 days".

This is one of the highest-value pieces of deterministic parsing in the system.
A hedged claim asserts a *bound*, not an equality, so the same reference value
can support one phrasing and contradict another:

    reference discount_percent = 15
    "up to 20% off"  -> SUPPORTED    (15 <= 20)
    "20% off"        -> CONTRADICTED (15 != 20)

Baseline error analysis showed the model treating these as interchangeable in
both directions. Detecting the hedge here converts the comparison into the
right operator before any rule or prompt sees it, and gives the post-rater
heuristics a deterministic ground truth to check the model against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.enums import Operator, Qualifier
from app.normalization import text as textutil


@dataclass(frozen=True, slots=True)
class QualifierMatch:
    qualifier: Qualifier
    cue: str


#: Ordered longest-cue-first within each group so "no more than" is not
#: shadowed by "more than", and checked in this overall order so that the more
#: specific bound wins when a sentence contains several hedges.
_CUES: tuple[tuple[Qualifier, tuple[str, ...]], ...] = (
    (
        Qualifier.UP_TO,
        ("up to", "as much as", "save up to", "discounts of up to"),
    ),
    (
        Qualifier.STARTING_AT,
        ("starting at", "starting from", "starts at", "from as low as", "as low as", "prices from"),
    ),
    (
        Qualifier.AT_MOST,
        ("no more than", "not more than", "at most", "maximum of", "max of", "or less", "under"),
    ),
    (
        Qualifier.AT_LEAST,
        ("no less than", "at least", "minimum of", "or more", "and up", "over"),
    ),
    (
        Qualifier.APPROXIMATELY,
        ("approximately", "roughly", "about", "around", "nearly", "almost", "~", "circa"),
    ),
    (
        Qualifier.UNLIMITED,
        ("unlimited", "unmetered", "no cap on", "no limits on"),
    ),
)

#: "about" and "around" are only hedges when they precede a number. "Read
#: about our plans" and "shipping around the world" must not become
#: APPROXIMATELY, which would silently widen the numeric tolerance.
_NUMERIC_CONTEXT_REQUIRED: frozenset[str] = frozenset(
    {"about", "around", "under", "over", "nearly", "almost"}
)

_NUMBER_AFTER = re.compile(r"^\W*(?:\$|£|€|¥)?\s*\d")

#: How a hedge changes the comparison. APPROXIMATELY keeps EQUALS and instead
#: widens the tolerance band, because "approximately $200" is a fuzzy equality
#: rather than a one-sided bound.
QUALIFIER_OPERATORS: dict[Qualifier, Operator] = {
    Qualifier.NONE: Operator.EQUALS,
    Qualifier.UP_TO: Operator.AT_MOST,
    Qualifier.AT_MOST: Operator.AT_MOST,
    Qualifier.STARTING_AT: Operator.AT_LEAST,
    Qualifier.AT_LEAST: Operator.AT_LEAST,
    Qualifier.APPROXIMATELY: Operator.EQUALS,
    Qualifier.UNLIMITED: Operator.EQUALS,
}


def detect_qualifier(text: str) -> QualifierMatch | None:
    """Find the hedge governing the claimed value, if any."""
    folded = textutil.fold(text)

    for qualifier, cues in _CUES:
        for cue in sorted(cues, key=len, reverse=True):
            index = folded.find(cue)
            if index < 0:
                continue
            if cue in _NUMERIC_CONTEXT_REQUIRED and not _NUMBER_AFTER.match(
                folded[index + len(cue) :]
            ):
                continue
            return QualifierMatch(qualifier=qualifier, cue=cue)
    return None


def operator_for(qualifier: Qualifier) -> Operator:
    return QUALIFIER_OPERATORS.get(qualifier, Operator.EQUALS)


def satisfies(
    qualifier: Qualifier,
    *,
    claimed: float,
    reference: float,
    tolerance: float,
) -> bool:
    """Check a numeric relation under the semantics of ``qualifier``.

    Used twice: by the rule engine to decide numeric claims, and by the
    post-rater heuristics to overrule a model that ignored the hedge. Sharing
    one implementation is what keeps those two paths from disagreeing.
    """
    match qualifier:
        case Qualifier.UP_TO | Qualifier.AT_MOST:
            return reference <= claimed + tolerance
        case Qualifier.STARTING_AT | Qualifier.AT_LEAST:
            return reference >= claimed - tolerance
        case _:
            return abs(reference - claimed) <= tolerance
