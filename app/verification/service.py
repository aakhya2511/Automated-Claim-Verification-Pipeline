"""End-to-end hybrid verification orchestration, independent of HTTP."""

from __future__ import annotations

import asyncio
import hashlib
from time import perf_counter
from uuid import uuid4

from app.core.exceptions import (
    BatchTooLargeError,
    ClaimVerificationError,
    LLMRequiredUnavailableError,
    RaterError,
    ReferenceNotFoundError,
    VerificationTimeoutError,
)
from app.core.logging import get_logger, request_context
from app.core.metrics import Metrics
from app.core.pipeline_config import PipelineConfig
from app.domain.enums import ExtractionMethod, VerificationPath
from app.domain.interfaces import (
    ClaimNormalizer,
    DecisionEngine,
    EvidenceRetriever,
    LLMRater,
    RuleEngine,
)
from app.domain.models import (
    AuditMetadata,
    LatencyBreakdown,
    RaterResult,
    RatingContext,
    VerificationFailure,
    VerificationRequest,
    VerificationResult,
)
from app.verification.escalation import ConservativeEscalationPolicy

logger = get_logger(__name__)


class HybridVerificationService:
    """Coordinates stages and owns routing, deadlines, audit, and operational telemetry."""

    def __init__(
        self,
        *,
        config: PipelineConfig,
        normalizer: ClaimNormalizer,
        retriever: EvidenceRetriever,
        rules: RuleEngine,
        escalation: ConservativeEscalationPolicy,
        decision: DecisionEngine,
        rater: LLMRater | None,
        metrics: Metrics,
        timeout_seconds: float,
        batch_concurrency: int,
        max_batch_items: int,
    ) -> None:
        self._config = config
        self._normalizer = normalizer
        self._retriever = retriever
        self._rules = rules
        self._escalation = escalation
        self._decision = decision
        self._rater = rater
        self._metrics = metrics
        self._timeout_seconds = timeout_seconds
        self._batch_concurrency = batch_concurrency
        self._max_batch_items = max_batch_items

    @property
    def config(self) -> PipelineConfig:
        return self._config

    async def verify(self, request: VerificationRequest) -> VerificationResult:
        request_id = request.request_id or uuid4().hex
        resolved = request.model_copy(update={"request_id": request_id})
        request_started = perf_counter()
        try:
            async with asyncio.timeout(self._timeout_seconds):
                with request_context(request_id):
                    return await self._verify(resolved)
        except TimeoutError as exc:
            self._metrics.record_error(code=VerificationTimeoutError.code)
            self._metrics.record_request(claim_type="unknown", outcome="failure")
            self._record_failure(
                resolved,
                code=VerificationTimeoutError.code,
                total_ms=_elapsed_ms(request_started),
            )
            raise VerificationTimeoutError(
                "Verification exceeded its execution deadline.",
                details={"request_id": request_id},
            ) from exc
        except ClaimVerificationError as exc:
            self._metrics.record_error(code=exc.code)
            self._metrics.record_request(claim_type="unknown", outcome="failure")
            self._record_failure(
                resolved,
                code=exc.code,
                total_ms=_elapsed_ms(request_started),
            )
            raise

    async def _verify(self, request: VerificationRequest) -> VerificationResult:
        started = perf_counter()

        stage = perf_counter()
        claim = await self._normalizer.normalize(request)
        combined_normalization_ms = _elapsed_ms(stage)
        extraction_ms = claim.extraction_latency_ms
        normalization_ms = max(0.0, combined_normalization_ms - extraction_ms)

        stage = perf_counter()
        evidence = await self._retriever.retrieve(claim, request)
        retrieval_ms = _elapsed_ms(stage)
        if (request.reference_id or request.sku) and evidence.record_id is None:
            identifier = request.reference_id or request.sku or ""
            field = "reference_id" if request.reference_id else "sku"
            reference_error = ReferenceNotFoundError(identifier, field=field)
            raise reference_error

        stage = perf_counter()
        rules = self._rules.evaluate(claim, evidence)
        rules_ms = _elapsed_ms(stage)
        for outcome in rules.outcomes:
            self._metrics.record_rule_decision(rule_id=outcome.rule_id, terminal=outcome.terminal)

        escalation = self._escalation.should_rate(claim, rules, evidence)
        rater_result: RaterResult | None = None
        rating_ms = 0.0
        if escalation.required:
            if not request.allow_llm or self._rater is None:
                unavailable_error = LLMRequiredUnavailableError(
                    "Semantic rating is required but no LLM rater is available.",
                    details={"request_id": request.request_id},
                )
                raise unavailable_error
            if escalation.reason is not None:
                self._metrics.record_escalation(reason=escalation.reason.value)
            self._metrics.record_llm_request(
                model=self._rater.model_name,
                prompt_version=self._rater.prompt_version,
            )
            stage = perf_counter()
            try:
                rater_result = await self._rater.rate(
                    claim,
                    evidence,
                    context=RatingContext(
                        evaluation_date=claim.time_context.as_of,
                        request_id=request.request_id,
                        trace_id=request.request_id,
                    ),
                )
            except RaterError as exc:
                self._metrics.record_llm_failure(
                    model=self._rater.model_name,
                    reason=exc.code,
                )
                raise
            rating_ms = _elapsed_ms(stage)
            if rater_result.trace is not None:
                self._metrics.record_llm_retries(count=max(0, rater_result.trace.attempt_count - 1))

        stage = perf_counter()
        final = self._decision.decide(claim, evidence, rules, rater_result)
        decision_ms = _elapsed_ms(stage)
        total_ms = _elapsed_ms(started)
        path = _verification_path(claim.extraction_method, rater_result is not None)
        latency = LatencyBreakdown(
            normalization=normalization_ms,
            extraction=extraction_ms,
            retrieval=retrieval_ms,
            rules=rules_ms,
            rating=rating_ms,
            decision=decision_ms,
            total=total_ms,
        ).rounded()
        audit = AuditMetadata(
            config_name=self._config.name,
            pipeline_version=self._config.pipeline_version,
            llm_invoked=rater_result is not None,
            provider=self._rater.provider_name if rater_result and self._rater else None,
            model=self._rater.model_name if rater_result and self._rater else None,
            prompt_version=(self._rater.prompt_version if rater_result and self._rater else None),
            schema_version=(
                rater_result.metadata.schema_version
                if rater_result and rater_result.metadata
                else None
            ),
            rules_evaluated=len(rules.outcomes),
            decisive_rule_id=rules.decisive_rule_id,
            extraction_method=claim.extraction_method,
            escalation_reason=escalation.reason,
            reference_version=evidence.reference_version,
        )
        result = VerificationResult(
            request_id=request.request_id or uuid4().hex,
            verdict=final.verdict,
            confidence=final.confidence,
            reason_codes=final.reason_codes,
            explanation=final.explanation,
            verification_path=path,
            escalation=escalation,
            normalized_claim=claim,
            evidence=evidence,
            rule_outcomes=rules.outcomes,
            rater_result=rater_result,
            latency_ms=latency,
            audit=audit,
        )
        self._record_success(result)
        return result

    async def verify_batch(
        self, requests: list[VerificationRequest]
    ) -> list[VerificationResult | VerificationFailure]:
        if len(requests) > self._max_batch_items:
            self._metrics.record_error(code=BatchTooLargeError.code)
            raise BatchTooLargeError(
                f"Batch contains {len(requests)} claims; maximum is {self._max_batch_items}."
            )
        self._metrics.observe_batch_size(size=len(requests))
        semaphore = asyncio.Semaphore(self._batch_concurrency)

        async def run(request: VerificationRequest) -> VerificationResult | VerificationFailure:
            async with semaphore:
                started = perf_counter()
                try:
                    return await self.verify(request)
                except ClaimVerificationError as exc:
                    return VerificationFailure(
                        request_id=request.request_id or uuid4().hex,
                        code=exc.code,
                        message=_safe_error_message(exc),
                        status_code=exc.status_code,
                        latency_ms=_elapsed_ms(started),
                    )

        return list(await asyncio.gather(*(run(request) for request in requests)))

    def _record_success(self, result: VerificationResult) -> None:
        claim = result.normalized_claim
        model = result.audit.model
        attempts = (
            result.rater_result.trace.attempt_count
            if result.rater_result and result.rater_result.trace
            else 0
        )
        self._metrics.record_request(
            claim_type=claim.claim_type.value if claim else "unknown",
            outcome="success",
        )
        self._metrics.record_verdict(
            verdict=result.verdict.value,
            path=result.verification_path.value,
        )
        for name, milliseconds in result.latency_ms.model_dump().items():
            self._metrics.observe_latency(stage=name, seconds=milliseconds / 1000)
        logger.info(
            "verification_completed",
            reference_id=result.evidence.record_id,
            claim_type=claim.claim_type.value if claim else None,
            claim_length=len(claim.raw_text) if claim else 0,
            claim_hash=_claim_hash(claim.raw_text) if claim else None,
            verdict=result.verdict.value,
            verification_path=result.verification_path.value,
            escalation_reason=(
                result.escalation.reason.value if result.escalation.reason else None
            ),
            parse_confidence=claim.parse_confidence if claim else None,
            llm_invoked=result.audit.llm_invoked,
            provider=result.audit.provider,
            model=model,
            prompt_version=result.audit.prompt_version,
            attempts=attempts,
            total_latency_ms=result.latency_ms.total,
        )

    @staticmethod
    def _record_failure(request: VerificationRequest, *, code: str, total_ms: float) -> None:
        with request_context(request.request_id or "unknown"):
            logger.warning(
                "verification_failed",
                reference_id=request.reference_id,
                claim_length=len(request.claim),
                claim_hash=_claim_hash(request.claim),
                error_code=code,
                total_latency_ms=round(total_ms, 3),
            )


def _verification_path(method: ExtractionMethod, rated: bool) -> VerificationPath:
    if method is ExtractionMethod.LLM_ASSISTED:
        return (
            VerificationPath.LLM_EXTRACTION_AND_RATER
            if rated
            else VerificationPath.DETERMINISTIC_WITH_LLM_EXTRACTION
        )
    return VerificationPath.LLM_RATER if rated else VerificationPath.DETERMINISTIC


def _safe_error_message(exc: ClaimVerificationError) -> str:
    if isinstance(exc, RaterError):
        return "The semantic rating provider could not complete this verification."
    return exc.message


def _elapsed_ms(started: float) -> float:
    return max(0.0, (perf_counter() - started) * 1000)


def _claim_hash(claim: str) -> str:
    return hashlib.sha256(claim.encode()).hexdigest()[:16]
