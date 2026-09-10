"""One auditable policy for all semantic-rater routing decisions."""

from __future__ import annotations

from app.core.pipeline_config import RaterConfig
from app.domain.enums import ClaimType, EscalationReason, ReasonCode
from app.domain.models import (
    EscalationDecision,
    NormalizedClaim,
    ReferenceEvidence,
    RuleEngineResult,
)

_MISSING_EVIDENCE_CODES = frozenset(
    {ReasonCode.REFERENCE_NOT_FOUND, ReasonCode.FIELD_NOT_IN_REFERENCE}
)


class ConservativeEscalationPolicy:
    """Escalate only when semantics can change the answer using existing evidence."""

    def __init__(self, config: RaterConfig) -> None:
        self._config = config

    def should_rate(
        self,
        claim: NormalizedClaim,
        rules: RuleEngineResult,
        evidence: ReferenceEvidence,
    ) -> EscalationDecision:
        if rules.is_terminal:
            return EscalationDecision(required=False, detail="deterministic result is terminal")

        if evidence.record_id is None:
            return EscalationDecision(
                required=False,
                detail="no authoritative reference record is available",
            )

        if set(rules.reason_codes).intersection(_MISSING_EVIDENCE_CODES) or any(
            key.endswith("__absent") for key in evidence.fields
        ):
            return EscalationDecision(
                required=False,
                detail="the authoritative record does not provide the required field",
            )

        if not self._config.enabled:
            return EscalationDecision(
                required=False,
                detail="semantic rating is disabled by pipeline configuration",
            )

        if not claim.is_parsed or claim.claim_type is ClaimType.UNKNOWN:
            return EscalationDecision(
                required=True,
                reason=EscalationReason.UNSUPPORTED_DETERMINISTIC_CLAIM_TYPE,
                detail="claim semantics are outside deterministic coverage",
            )

        if claim.parse_confidence < 1.0 and ReasonCode.LOW_CONFIDENCE_ABSTAIN in rules.reason_codes:
            return EscalationDecision(
                required=True,
                reason=EscalationReason.LOW_PARSE_CONFIDENCE,
                detail="parse confidence was too low for deterministic termination",
            )

        if not rules.outcomes:
            return EscalationDecision(
                required=True,
                reason=EscalationReason.INSUFFICIENT_DETERMINISTIC_COVERAGE,
                detail="no deterministic rule covers the normalized claim",
            )

        return EscalationDecision(
            required=True,
            reason=EscalationReason.NON_TERMINAL_RULE_RESULT,
            detail="deterministic rules produced a non-terminal result",
        )
