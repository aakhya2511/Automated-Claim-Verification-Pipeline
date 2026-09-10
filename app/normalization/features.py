"""Resolving feature phrases in claims to canonical reference feature keys.

The reference data stores capabilities as snake_case keys
(``active_noise_cancellation``, ``unlimited_api_requests``, ``32_gb_ram``).
Claims describe the same things in prose. Without a resolver, feature
verification degenerates into substring matching, which both misses real
matches ("noise cancelling" vs ``active_noise_cancellation``) and invents false
ones ("includes a filter" vs ``hepa_filter``).

The vocabulary is built at startup from the feature keys actually present in
the catalog rather than hard-coded, so adding a feature to the reference data
does not require a code change. Only genuinely different wordings need an
entry in :data:`SYNONYMS`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.models import ReferenceRecord
from app.normalization import text as textutil

#: Alternate wordings that cannot be derived mechanically from the key. Keys
#: not listed here are matched by their own tokens, so this table stays small
#: and only carries real linguistic variation, not spelling noise.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "active_noise_cancellation": (
        "noise cancellation",
        "noise cancelling",
        "noise canceling",
        "anc",
    ),
    "unlimited_api_requests": (
        "unlimited api calls",
        "unlimited api requests",
        "unmetered api",
        "unlimited requests",
    ),
    "sso": ("single sign on", "single sign-on", "saml", "saml sso"),
    "audit_logs": ("audit log", "audit trail", "audit logging"),
    "hepa_filter": ("hepa filtration", "true hepa"),
    "usb_c_charging": ("usb-c charging", "type-c charging", "usb c port"),
    "wireless_charging_case": ("wireless charging case", "qi charging case"),
    "thunderbolt_4": ("thunderbolt 4", "thunderbolt port"),
    "oled_display": ("oled screen", "oled panel"),
    "4k_resolution": ("4k display", "4k screen", "uhd resolution"),
    "120_hz_refresh": ("120hz", "120 hz refresh rate"),
    "gps_tracking": ("built-in gps", "gps"),
    "heart_rate_monitor": ("heart rate sensor", "heart-rate tracking"),
    "blood_oxygen_sensor": ("spo2 sensor", "blood oxygen monitoring"),
    "lte_connectivity": ("lte", "cellular connectivity", "4g lte"),
    "priority_support": ("priority customer support", "priority assistance"),
    "dedicated_csm": ("dedicated customer success manager", "dedicated account manager"),
    "dedicated_support_engineer": ("dedicated support engineer", "named support engineer"),
    "on_premise_option": ("on-premise deployment", "self-hosted option", "on prem"),
    "in_body_stabilization": ("ibis", "in-body image stabilization", "sensor stabilization"),
    "self_emptying_base": ("self-emptying dock", "auto-empty base"),
    "electric_height_adjustment": ("electric standing adjustment", "motorized height"),
    "hot_swappable_switches": ("hot swap switches", "hotswap sockets"),
    "wifi_6e": ("wi-fi 6e", "wifi 6e support"),
    "free_shipping": ("free delivery", "shipping included", "no shipping charge"),
}

#: Single-token keys are accepted on an exact token match ("sso"). Multi-token
#: keys require *every* token to be present, which is what stops "includes a
#: filter" from resolving to `hepa_filter`.
_CONTAINMENT_SCORE = 1.0
_SYNONYM_SCORE = 0.95
_SQUASHED_SCORE = 0.9
_TOKEN_SUBSET_SCORE = 0.8
_SINGLE_TOKEN_SCORE = 0.75


@dataclass(frozen=True, slots=True)
class FeatureMatch:
    """A claim phrase resolved to a canonical feature key."""

    key: str
    confidence: float
    matched_via: str


def humanize(key: str) -> str:
    """Render a canonical key for explanations: ``32_gb_ram`` -> ``32 gb ram``."""
    return key.replace("_", " ")


class FeatureResolver:
    """Maps claim text onto the catalog's feature vocabulary."""

    def __init__(
        self,
        vocabulary: Iterable[str],
        *,
        synonyms: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._keys = tuple(sorted(set(vocabulary)))
        table = synonyms if synonyms is not None else SYNONYMS

        self._phrases: dict[str, str] = {}
        self._squashed: dict[str, str] = {}
        self._tokens: dict[str, frozenset[str]] = {}

        for key in self._keys:
            canonical = humanize(key)
            self._phrases[textutil.fold(canonical)] = key
            self._squashed[textutil.squash(canonical)] = key
            self._tokens[key] = frozenset(textutil.tokenize(canonical, drop_stopwords=False))

        self._synonym_phrases: dict[str, str] = {}
        for key, phrases in table.items():
            # Only index synonyms for features this catalog actually has, so a
            # stale table entry cannot resolve to a key that does not exist.
            if key in self._tokens:
                for phrase in phrases:
                    self._synonym_phrases[textutil.fold(phrase)] = key

    @property
    def vocabulary(self) -> tuple[str, ...]:
        return self._keys

    def resolve(self, text: str) -> FeatureMatch | None:
        """Return the most specific feature key mentioned in ``text``."""
        candidates = self.resolve_all(text)
        return candidates[0] if candidates else None

    def resolve_all(self, text: str) -> list[FeatureMatch]:
        """All feature keys mentioned, best first.

        Ranking prefers longer literal matches so a claim about ``32_gb_ram``
        cannot be answered with ``16_gb_ram``, and a specific phrase always
        beats a bag-of-tokens match.
        """
        folded = textutil.fold(text)
        squashed = textutil.squash(text)
        claim_tokens = frozenset(textutil.tokenize(text, drop_stopwords=False))

        best: dict[str, FeatureMatch] = {}

        def offer(key: str, confidence: float, via: str) -> None:
            existing = best.get(key)
            if existing is None or confidence > existing.confidence:
                best[key] = FeatureMatch(key=key, confidence=confidence, matched_via=via)

        for phrase, key in self._phrases.items():
            if phrase and phrase in folded:
                offer(key, _CONTAINMENT_SCORE, "canonical_phrase")

        for phrase, key in self._synonym_phrases.items():
            if phrase and phrase in folded:
                offer(key, _SYNONYM_SCORE, "synonym")

        for compact, key in self._squashed.items():
            if compact and compact in squashed:
                offer(key, _SQUASHED_SCORE, "compact")

        for key, tokens in self._tokens.items():
            if not tokens or not tokens <= claim_tokens:
                continue
            score = _TOKEN_SUBSET_SCORE if len(tokens) > 1 else _SINGLE_TOKEN_SCORE
            offer(key, score, "token_subset")

        # Longer keys are more specific; on a confidence tie prefer them.
        return sorted(
            best.values(),
            key=lambda match: (-match.confidence, -len(match.key), match.key),
        )


def vocabulary_from_records(records: Iterable[ReferenceRecord]) -> set[str]:
    """Collect every feature key present in a set of reference records.

    Called at startup so the resolver's vocabulary is exactly what the source
    of truth contains — no drift between a hard-coded list and the catalog.
    """
    keys: set[str] = set()
    for record in records:
        keys.update(record.included_features)
        keys.update(record.excluded_features)
    return keys
