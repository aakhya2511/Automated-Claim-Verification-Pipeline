"""Audited status of compatibility-sensitive configuration fields.

This registry is documentation enforced by tests. It does not participate in
verification decisions and therefore cannot alter frozen quality behavior.
"""

from enum import StrEnum


class ConfigStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    DEPRECATED = "DEPRECATED"
    EXPERIMENT_ONLY = "EXPERIMENT_ONLY"


AMBIGUOUS_FIELD_STATUS: dict[str, ConfigStatus] = {
    "decision.contradiction_threshold": ConfigStatus.DORMANT,
    "decision.support_threshold": ConfigStatus.DORMANT,
    "decision.abstain_below": ConfigStatus.DORMANT,
    "decision.conflict_policy": ConfigStatus.DORMANT,
    "decision.rater_confidence_scale": ConfigStatus.DORMANT,
    "decision.heuristics.demote_contradiction_on_missing_field": ConfigStatus.DORMANT,
    "decision.heuristics.respect_qualifiers": ConfigStatus.DORMANT,
    "decision.heuristics.require_numeric_agreement": ConfigStatus.DORMANT,
    "decision.heuristics.block_support_without_evidence": ConfigStatus.ACTIVE,
    "decision.heuristics.enforce_offer_window": ConfigStatus.DORMANT,
    "cache.enabled": ConfigStatus.DEPRECATED,
    "cache.ttl_seconds": ConfigStatus.DEPRECATED,
    "cache.max_entries": ConfigStatus.DEPRECATED,
    "rater.temperature": ConfigStatus.EXPERIMENT_ONLY,
    "rater.max_output_tokens": ConfigStatus.EXPERIMENT_ONLY,
    "rater.max_concurrency": ConfigStatus.EXPERIMENT_ONLY,
    "rater.schema_version": ConfigStatus.EXPERIMENT_ONLY,
    "llm.temperature": ConfigStatus.ACTIVE,
    "llm.max_output_tokens": ConfigStatus.ACTIVE,
    "llm.max_concurrency": ConfigStatus.ACTIVE,
    "llm.schema_version": ConfigStatus.ACTIVE,
}

REMOVED_FIELDS = frozenset(
    {
        "observability.log_claim_text",
        "observability.expose_debug_endpoints",
    }
)
