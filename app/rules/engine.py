"""Rule aggregation and the escalation decision.

The engine runs every applicable rule and then reconciles the outcomes. Two
choices in here carry most of the behaviour:

**Contradictions outrank abstentions.** If any rule finds a genuine conflict,
the verdict is ``CONTRADICTED`` even when another field was unavailable. "We
don't publish that" never overrides "this specific number is wrong".

**Parse confidence gates termination.** A rule's own confidence is high by
construction — comparing two decimals is exact. The real uncertainty is
upstream, in whether the sentence was understood. So the confidence that
decides whether to terminate is ``min(rule_confidence, parse_confidence)``. A
weakly parsed claim escalates to the semantic rater instead of being settled on
a guess, which is where a large share of avoidable false positives come from.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.clock import Clock
from app.core.pipeline_config import RuleConfig
from app.domain.enums import ReasonCode, Verdict
from app.domain.models import (
    NormalizedClaim,
    ReferenceEvidence,
    RuleEngineResult,
    RuleOutcome,
)
from app.rules.base import Rule, RuleContext
from app.rules.library import default_rules

#: Rules that gate the pipeline: if one of these abstains for lack of data,
#: no comparison downstream of it could have had anything to compare.
_GATE_RULES: frozenset[str] = frozenset({"entity_resolution", "field_presence"})


class DeterministicRuleEngine:
    """Evaluates the rule library and decides whether escalation is needed.

    Satisfies :class:`app.domain.interfaces.RuleEngine`. Pure and synchronous:
    no I/O, no clock reads beyond a fallback, no metrics. That makes it trivial
    to unit test and means the whole deterministic path costs microseconds.
    """

    def __init__(
        self,
        *,
        config: RuleConfig,
        clock: Clock,
        rules: Sequence[Rule] | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        available = tuple(rules) if rules is not None else default_rules()
        enabled_ids = config.enabled_rule_ids
        if enabled_ids is None:
            self._rules = available
        else:
            available_by_id = {rule.rule_id: rule for rule in available}
            unknown = set(enabled_ids) - set(available_by_id)
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"unknown enabled_rule_ids: {names}")
            self._rules = tuple(available_by_id[rule_id] for rule_id in enabled_ids)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return self._rules

    def evaluate(self, claim: NormalizedClaim, evidence: ReferenceEvidence) -> RuleEngineResult:
        if not self._config.enabled:
            # Baseline arm: the deterministic layer is switched off entirely,
            # so every claim escalates.
            return RuleEngineResult(
                verdict=None,
                confidence=0.0,
                reason_codes=(),
                outcomes=(),
                escalate_to_rater=True,
            )

        context = RuleContext(
            config=self._config,
            as_of=claim.time_context.as_of or self._clock.today(),
        )

        if (
            self._config.invalid_claim_gate is True
            and "non_verifiable_commercial_proposition" in claim.notes
        ):
            outcome = RuleOutcome(
                rule_id="invalid_claim_gate",
                fired=True,
                verdict=Verdict.INVALID_CLAIM,
                reason_codes=(ReasonCode.UNSUPPORTED_INFERENCE,),
                confidence=0.99,
                terminal=True,
                detail="claim contains no supported verifiable commercial proposition",
            )
            return RuleEngineResult(
                verdict=Verdict.INVALID_CLAIM,
                confidence=outcome.confidence,
                reason_codes=outcome.reason_codes,
                outcomes=(outcome,),
                decisive_rule_id=outcome.rule_id,
                escalate_to_rater=False,
            )

        if not claim.is_parsed:
            # Nothing structured to compare. Not an error — a routing decision.
            return RuleEngineResult(
                verdict=None,
                confidence=0.0,
                reason_codes=(ReasonCode.UNPARSEABLE_CLAIM,),
                outcomes=(
                    RuleOutcome(
                        rule_id="claim_parseability",
                        fired=False,
                        detail="claim could not be parsed into a structured comparison",
                    ),
                ),
                escalate_to_rater=True,
            )

        outcomes: list[RuleOutcome] = []
        for rule in self._rules:
            if not rule.applies(claim, evidence, context):
                continue
            outcome = rule.evaluate(claim, evidence, context)
            outcomes.append(outcome)

            if (
                outcome.fired
                and outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE
                and rule.rule_id in _GATE_RULES
            ):
                # Short-circuit: no later rule can have data to work with.
                return self._abstained(outcome, outcomes)

        return self._reconcile(claim, outcomes)

    # ------------------------------------------------------------------ #
    # Aggregation
    # ------------------------------------------------------------------ #
    def _abstained(self, outcome: RuleOutcome, outcomes: list[RuleOutcome]) -> RuleEngineResult:
        return RuleEngineResult(
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            confidence=outcome.confidence,
            reason_codes=outcome.reason_codes,
            outcomes=tuple(outcomes),
            decisive_rule_id=outcome.rule_id,
            # The verdict stands on its own, but the rater *may* still be
            # consulted; whether it is belongs to the pipeline profile
            # (`rater.invoke_on_missing_evidence`), not to this layer.
            escalate_to_rater=True,
        )

    def _reconcile(self, claim: NormalizedClaim, outcomes: list[RuleOutcome]) -> RuleEngineResult:
        fired = [outcome for outcome in outcomes if outcome.fired]
        contradictions = [o for o in fired if o.verdict is Verdict.CONTRADICTED]
        insufficient = [o for o in fired if o.verdict is Verdict.INSUFFICIENT_EVIDENCE]
        supports = [o for o in fired if o.verdict is Verdict.SUPPORTED]

        if contradictions:
            decisive = max(contradictions, key=lambda outcome: outcome.confidence)
            return self._decide(
                claim,
                decisive=decisive,
                codes=_merge_codes(contradictions),
                outcomes=outcomes,
                allowed=self._config.allow_terminal_contradicted,
            )

        if insufficient:
            decisive = max(insufficient, key=lambda outcome: outcome.confidence)
            return self._abstained(decisive, outcomes)

        if supports:
            decisive = min(supports, key=lambda outcome: outcome.confidence)
            return self._decide(
                claim,
                decisive=decisive,
                codes=_merge_codes(supports),
                outcomes=outcomes,
                allowed=self._config.allow_terminal_supported,
            )

        # Every rule declined to apply: the claim parsed, but nothing in the
        # library covers it. Escalate rather than invent a verdict.
        return RuleEngineResult(
            verdict=None,
            confidence=0.0,
            reason_codes=(),
            outcomes=tuple(outcomes),
            escalate_to_rater=True,
        )

    def _decide(
        self,
        claim: NormalizedClaim,
        *,
        decisive: RuleOutcome,
        codes: tuple[ReasonCode, ...],
        outcomes: list[RuleOutcome],
        allowed: bool,
    ) -> RuleEngineResult:
        """Apply the confidence gate and the profile's termination permissions."""
        effective = min(decisive.confidence, claim.parse_confidence)
        terminal = allowed and effective >= self._config.min_terminal_confidence

        reason_codes = codes
        if not terminal:
            reason_codes = (*codes, ReasonCode.LOW_CONFIDENCE_ABSTAIN)

        return RuleEngineResult(
            verdict=decisive.verdict,
            confidence=effective,
            reason_codes=reason_codes,
            outcomes=tuple(outcomes),
            decisive_rule_id=decisive.rule_id,
            escalate_to_rater=not terminal,
        )


def _merge_codes(outcomes: list[RuleOutcome]) -> tuple[ReasonCode, ...]:
    """Union of reason codes, order preserved and deduplicated."""
    merged: list[ReasonCode] = []
    for outcome in outcomes:
        for code in outcome.reason_codes:
            if code not in merged:
                merged.append(code)
    return tuple(merged)
