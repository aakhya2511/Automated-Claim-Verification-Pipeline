"""Guard the Phase 9 declaration-to-runtime configuration audit."""

from app.core.config import ObservabilitySettings
from app.core.config_status import AMBIGUOUS_FIELD_STATUS, REMOVED_FIELDS, ConfigStatus


def test_runtime_consumed_provider_limits_are_active() -> None:
    assert AMBIGUOUS_FIELD_STATUS["llm.max_concurrency"] is ConfigStatus.ACTIVE
    assert AMBIGUOUS_FIELD_STATUS["llm.max_output_tokens"] is ConfigStatus.ACTIVE


def test_legacy_decision_and_cache_knobs_do_not_claim_runtime_effect() -> None:
    assert AMBIGUOUS_FIELD_STATUS["decision.conflict_policy"] is ConfigStatus.DORMANT
    assert AMBIGUOUS_FIELD_STATUS["cache.enabled"] is ConfigStatus.DEPRECATED
    assert (
        AMBIGUOUS_FIELD_STATUS["decision.heuristics.block_support_without_evidence"]
        is ConfigStatus.ACTIVE
    )


def test_removed_fields_are_absent_from_typed_settings() -> None:
    assert {
        "observability.log_claim_text",
        "observability.expose_debug_endpoints",
    } == REMOVED_FIELDS
    assert "log_claim_text" not in ObservabilitySettings.model_fields
    assert "expose_debug_endpoints" not in ObservabilitySettings.model_fields
