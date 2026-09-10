"""Conservative Phase 4 final-decision precedence."""

from __future__ import annotations

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

    def decide(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        rules: RuleEngineResult,
        rater: RaterResult | None,
    ) -> DecisionOutcome:
        del claim
        if rules.is_terminal and rules.verdict is not None:
            return DecisionOutcome(
                verdict=rules.verdict,
                confidence=rules.confidence,
                reason_codes=rules.reason_codes,
                explanation=_rule_explanation(rules),
            )

        if rater is not None:
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
