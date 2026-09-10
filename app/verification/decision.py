"""Conservative Phase 4 final-decision precedence."""

from __future__ import annotations

from app.core.pipeline_config import DecisionConfig, PostRaterHeuristics
from app.domain.enums import ReasonCode, Verdict
from app.domain.models import (
    DecisionOutcome,
    NormalizedClaim,
    RaterResult,
    ReferenceEvidence,
    RuleEngineResult,
)


class ConservativeDecisionEngine:
    """Deterministic terminal results win; otherwise use validated rater output."""

    def __init__(self, config: DecisionConfig | None = None) -> None:
        self._config = config or DecisionConfig(
            heuristics=PostRaterHeuristics(block_support_without_evidence=False)
        )

    def decide(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        rules: RuleEngineResult,
        rater: RaterResult | None,
    ) -> DecisionOutcome:
        if rules.is_terminal and rules.verdict is not None:
            return DecisionOutcome(
                verdict=rules.verdict,
                confidence=rules.confidence,
                reason_codes=rules.reason_codes,
                explanation=_rule_explanation(rules),
            )

        if rater is not None:
            if (
                self._config.heuristics.block_support_without_evidence
                and rater.verdict is Verdict.SUPPORTED
                and not _has_capable_evidence(claim, evidence)
            ):
                return DecisionOutcome(
                    verdict=Verdict.INSUFFICIENT_EVIDENCE,
                    confidence=0.0,
                    reason_codes=(ReasonCode.INSUFFICIENT_EVIDENCE,),
                    explanation=(
                        "The semantic rater returned support without an authoritative "
                        "field capable of establishing the assertion."
                    ),
                )
            return DecisionOutcome(
                verdict=rater.verdict,
                confidence=rater.confidence,
                reason_codes=rater.reason_codes,
                explanation=rater.explanation,
            )

        if rules.verdict is not None:
            return DecisionOutcome(
                verdict=rules.verdict,
                confidence=rules.confidence,
                reason_codes=rules.reason_codes,
                explanation=_rule_explanation(rules),
            )

        code = (
            ReasonCode.REFERENCE_NOT_FOUND
            if evidence.record_id is None
            else ReasonCode.INSUFFICIENT_EVIDENCE
        )
        return DecisionOutcome(
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            reason_codes=(code,),
            explanation="The deterministic layer could not establish or refute the claim.",
        )


def _rule_explanation(rules: RuleEngineResult) -> str:
    if rules.decisive_rule_id:
        for outcome in rules.outcomes:
            if outcome.rule_id == rules.decisive_rule_id and outcome.detail:
                return outcome.detail
    if rules.verdict is Verdict.INSUFFICIENT_EVIDENCE:
        return "The authoritative reference does not contain the required evidence."
    return "The claim was resolved by deterministic comparison."


def _has_capable_evidence(claim: NormalizedClaim, evidence: ReferenceEvidence) -> bool:
    if evidence.record_id is None or not evidence.fields:
        return False
    if claim.attribute.value != "unknown" and evidence.has(evidence.effective_attribute):
        return True
    if claim.feature:
        return evidence.has(claim.attribute)
    return False
