"""Routing, reliability, concurrency, and telemetry at the service boundary."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from app.core.exceptions import VerificationTimeoutError
from app.core.metrics import PrometheusMetrics
from app.domain.enums import (
    Attribute,
    ClaimType,
    EscalationReason,
    ReasonCode,
    Verdict,
    VerificationPath,
)
from app.domain.models import (
    ClaimExtraction,
    NormalizedClaim,
    RaterResult,
    RatingContext,
    ReferenceEvidence,
    RuleEngineResult,
    VerificationRequest,
)
from app.raters.fake import FakeClaimExtractor
from app.verification.escalation import ConservativeEscalationPolicy

from tests.conftest import build_test_service


class ConcurrencyRater:
    provider_name = "instrumented"
    model_name = "fake-concurrent"
    prompt_version = "v1"

    def __init__(self) -> None:
        self.active = 0
        self.maximum = 0
        self.calls = 0

    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult:
        del claim, evidence, context
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return RaterResult(
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            confidence=0.5,
            reason_codes=(ReasonCode.INSUFFICIENT_EVIDENCE,),
            explanation="Semantic evidence remains insufficient.",
        )

    async def aclose(self) -> None:
        return None


class HangingRater(ConcurrencyRater):
    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult:
        del claim, evidence, context
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class DelayedRater(ConcurrencyRater):
    async def rate(
        self,
        claim: NormalizedClaim,
        evidence: ReferenceEvidence,
        *,
        context: RatingContext,
    ) -> RaterResult:
        await asyncio.sleep(0.02)
        return await super().rate(claim, evidence, context=context)


def ambiguous_requests(count: int) -> list[VerificationRequest]:
    return [
        VerificationRequest(
            claim="This is the best product we have ever made.",
            reference_id="prod-headphones-1",
            as_of=date(2026, 9, 15),
            request_id=f"item-{index}",
        )
        for index in range(count)
    ]


async def test_batch_concurrency_is_bounded_and_ordered(
    settings, optimized_config, repository, fixed_clock
) -> None:
    rater = ConcurrencyRater()
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=rater,
        batch_concurrency=2,
    )
    results = await service.verify_batch(ambiguous_requests(6))
    assert rater.maximum == 2
    assert rater.calls == 6
    assert [item.request_id for item in results] == [f"item-{index}" for index in range(6)]


async def test_overall_deadline_cancels_hanging_rater(
    settings, optimized_config, repository, fixed_clock
) -> None:
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=HangingRater(),
        timeout_seconds=0.01,
    )
    with pytest.raises(VerificationTimeoutError):
        await service.verify(ambiguous_requests(1)[0])


async def test_semantic_rater_can_exceed_old_deadline_but_finish_with_new_deadline(
    settings, baseline_config, repository, fixed_clock
) -> None:
    service = build_test_service(
        settings=settings,
        config=baseline_config,
        repository=repository,
        clock=fixed_clock,
        rater=DelayedRater(),
        timeout_seconds=0.075,
    )
    result = await service.verify(ambiguous_requests(1)[0])
    assert result.verification_path is VerificationPath.LLM_RATER


async def test_deterministic_result_is_reproducible(
    settings, optimized_config, repository, fixed_clock, fake_rater
) -> None:
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=fake_rater,
    )
    request = VerificationRequest(
        claim="The Pro plan includes a 30-day free trial.",
        reference_id="plan-pro",
        as_of=date(2026, 9, 15),
    )
    first = await service.verify(request)
    second = await service.verify(request)
    assert first.normalized_claim == second.normalized_claim
    assert first.rule_outcomes == second.rule_outcomes
    assert first.verdict == second.verdict
    assert first.reason_codes == second.reason_codes
    assert first.verification_path is VerificationPath.DETERMINISTIC
    assert fake_rater.call_count == 0


async def test_llm_extraction_can_return_to_deterministic_fast_path(
    settings, optimized_config, repository, fixed_clock, fake_rater
) -> None:
    extractor = FakeClaimExtractor(
        ClaimExtraction(
            claim_type=ClaimType.FEATURE_INCLUSION,
            attribute=Attribute.INCLUDED_FEATURES,
            feature="priority_support",
            confidence=0.95,
        )
    )
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=fake_rater,
        extractor=extractor,
    )
    result = await service.verify(
        VerificationRequest(
            claim="Priority support is part of this tier, in less usual wording.",
            reference_id="plan-pro",
            as_of=date(2026, 9, 15),
        )
    )
    assert result.verdict is Verdict.SUPPORTED
    assert result.verification_path is VerificationPath.DETERMINISTIC_WITH_LLM_EXTRACTION
    assert result.latency_ms.extraction >= 0
    assert fake_rater.call_count == 0


async def test_llm_extraction_and_rating_path_is_explicit(
    settings, optimized_config, repository, fixed_clock, fake_rater
) -> None:
    extractor = FakeClaimExtractor(
        ClaimExtraction(
            claim_type=ClaimType.UNKNOWN,
            attribute=Attribute.UNKNOWN,
            confidence=0.4,
        )
    )
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=fake_rater,
        extractor=extractor,
    )
    result = await service.verify(ambiguous_requests(1)[0])
    assert result.verification_path is VerificationPath.LLM_EXTRACTION_AND_RATER
    assert fake_rater.call_count == 1


async def test_metrics_distinguish_deterministic_and_llm_paths(
    settings, optimized_config, repository, fixed_clock, fake_rater
) -> None:
    metrics = PrometheusMetrics()
    service = build_test_service(
        settings=settings,
        config=optimized_config,
        repository=repository,
        clock=fixed_clock,
        rater=fake_rater,
        metrics=metrics,
    )
    await service.verify(
        VerificationRequest(
            claim="The Pro plan includes a 30-day free trial.",
            reference_id="plan-pro",
            as_of=date(2026, 9, 15),
        )
    )
    await service.verify(ambiguous_requests(1)[0])
    rendered = metrics.render().decode()
    assert 'verification_path_total{path="DETERMINISTIC"} 1.0' in rendered
    assert 'verification_path_total{path="LLM_RATER"} 1.0' in rendered
    assert 'llm_requests_total{model="fake",prompt_version="fake-v1"} 1.0' in rendered
    assert "request_id" not in rendered


def test_terminal_deterministic_contradiction_never_escalates(optimized_config) -> None:
    decision = ConservativeEscalationPolicy(optimized_config.rater).should_rate(
        NormalizedClaim(
            raw_text="Product costs $1",
            claim_type=ClaimType.PRICE,
            attribute=Attribute.PRICE,
        ),
        RuleEngineResult(
            verdict=Verdict.CONTRADICTED,
            confidence=0.99,
            escalate_to_rater=False,
        ),
        ReferenceEvidence(record_id="x", fields={"price": 2}),
    )
    assert decision.required is False


def test_unknown_semantics_with_evidence_have_typed_escalation_reason(
    optimized_config,
) -> None:
    decision = ConservativeEscalationPolicy(optimized_config.rater).should_rate(
        NormalizedClaim(raw_text="An ambiguous commercial assertion"),
        RuleEngineResult(reason_codes=(ReasonCode.UNPARSEABLE_CLAIM,)),
        ReferenceEvidence(record_id="x", fields={"terms": "some supplied evidence"}),
    )
    assert decision.required is True
    assert decision.reason is EscalationReason.UNSUPPORTED_DETERMINISTIC_CLAIM_TYPE
