"""Pure metric, routing, latency, and reconciliation calculations."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    ClaimType,
    EscalationReason,
    ExtractionMethod,
    ReasonCode,
    Verdict,
    VerificationPath,
)
from app.domain.models import (
    LatencyBreakdown,
    NormalizedClaim,
    RaterResult,
    ReferenceEvidence,
    RuleOutcome,
    VerificationFailure,
    VerificationResult,
)
from app.evaluation.models import Difficulty, EvaluationSample, MutationType


class ErrorCategory(StrEnum):
    PARSER_ERROR = "PARSER_ERROR"
    ENTITY_RESOLUTION_ERROR = "ENTITY_RESOLUTION_ERROR"
    EVIDENCE_SELECTION_ERROR = "EVIDENCE_SELECTION_ERROR"
    ESCALATION_ERROR = "ESCALATION_ERROR"
    DETERMINISTIC_RULE_ERROR = "DETERMINISTIC_RULE_ERROR"
    QUALIFIER_INTERPRETATION_ERROR = "QUALIFIER_INTERPRETATION_ERROR"
    NEGATION_ERROR = "NEGATION_ERROR"
    NUMERIC_REASONING_ERROR = "NUMERIC_REASONING_ERROR"
    TEMPORAL_REASONING_ERROR = "TEMPORAL_REASONING_ERROR"
    REGION_REASONING_ERROR = "REGION_REASONING_ERROR"
    FEATURE_SEMANTICS_ERROR = "FEATURE_SEMANTICS_ERROR"
    MISSING_EVIDENCE_ERROR = "MISSING_EVIDENCE_ERROR"
    LLM_SEMANTIC_ERROR = "LLM_SEMANTIC_ERROR"
    LLM_OVER_INFERENCE = "LLM_OVER_INFERENCE"
    LLM_UNDER_INFERENCE = "LLM_UNDER_INFERENCE"
    CONFIDENCE_ERROR = "CONFIDENCE_ERROR"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class PredictionRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sample_id: str
    expected_verdict: Verdict
    predicted_verdict: Verdict | None = None
    correct: bool = False
    claim_type: ClaimType
    difficulty: Difficulty
    mutation_type: MutationType | None = None
    verification_path: VerificationPath | None = None
    escalation_reason: EscalationReason | None = None
    confidence: float | None = None
    latency_ms: LatencyBreakdown = LatencyBreakdown()
    llm_invoked: bool = False
    extraction_method: ExtractionMethod | None = None
    reason_codes: tuple[ReasonCode, ...] = ()
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    attempt_count: int = 0
    provider_latency_ms: float = 0.0
    usage: dict[str, int | None] | None = None
    normalized_claim: NormalizedClaim | None = None
    evidence: ReferenceEvidence | None = None
    rule_outcomes: tuple[RuleOutcome, ...] = ()
    rater_result: RaterResult | None = None
    execution_error: str | None = None
    error_category: ErrorCategory | None = None


def prediction_from_result(
    sample: EvaluationSample, result: VerificationResult | VerificationFailure
) -> PredictionRow:
    if isinstance(result, VerificationFailure):
        return PredictionRow(
            sample_id=sample.sample_id,
            expected_verdict=sample.expected_verdict,
            claim_type=sample.claim_type,
            difficulty=sample.difficulty,
            mutation_type=sample.mutation.type if sample.mutation else None,
            latency_ms=LatencyBreakdown(total=result.latency_ms),
            execution_error=result.code,
            error_category=ErrorCategory.EXECUTION_FAILURE,
        )
    trace = result.rater_result.trace if result.rater_result else None
    row = PredictionRow(
        sample_id=sample.sample_id,
        expected_verdict=sample.expected_verdict,
        predicted_verdict=result.verdict,
        correct=result.verdict is sample.expected_verdict,
        claim_type=sample.claim_type,
        difficulty=sample.difficulty,
        mutation_type=sample.mutation.type if sample.mutation else None,
        verification_path=result.verification_path,
        escalation_reason=result.audit.escalation_reason,
        confidence=result.confidence,
        latency_ms=result.latency_ms,
        llm_invoked=result.audit.llm_invoked,
        extraction_method=result.audit.extraction_method,
        reason_codes=result.reason_codes,
        provider=result.audit.provider,
        model=result.audit.model,
        prompt_version=result.audit.prompt_version,
        schema_version=result.audit.schema_version,
        attempt_count=trace.attempt_count if trace else 0,
        provider_latency_ms=trace.provider_latency_ms if trace else 0.0,
        usage=trace.usage.model_dump() if trace and trace.usage else None,
        normalized_claim=result.normalized_claim,
        evidence=result.evidence,
        rule_outcomes=result.rule_outcomes,
        rater_result=result.rater_result,
    )
    if not row.correct:
        return row.model_copy(update={"error_category": classify_error(sample, row)})
    return row


def classify_error(sample: EvaluationSample, row: PredictionRow) -> ErrorCategory:
    if row.execution_error:
        return ErrorCategory.EXECUTION_FAILURE
    if row.normalized_claim and (
        sample.claim_type is not ClaimType.UNKNOWN
        and row.normalized_claim.claim_type is not sample.claim_type
    ):
        return ErrorCategory.PARSER_ERROR
    if row.evidence and row.evidence.record_id != sample.reference_id:
        return ErrorCategory.ENTITY_RESOLUTION_ERROR
    if sample.expected_verdict is Verdict.INSUFFICIENT_EVIDENCE:
        return (
            ErrorCategory.LLM_OVER_INFERENCE
            if row.llm_invoked
            else ErrorCategory.MISSING_EVIDENCE_ERROR
        )
    if row.llm_invoked:
        if (
            sample.expected_verdict is Verdict.CONTRADICTED
            and row.predicted_verdict is not Verdict.CONTRADICTED
        ):
            return ErrorCategory.LLM_UNDER_INFERENCE
        return ErrorCategory.LLM_SEMANTIC_ERROR
    if " up to " in f" {sample.claim.casefold()} " or "at least" in sample.claim.casefold():
        return ErrorCategory.QUALIFIER_INTERPRETATION_ERROR
    if sample.claim_type is ClaimType.PROMOTION_DATES:
        return ErrorCategory.TEMPORAL_REASONING_ERROR
    if sample.claim_type is ClaimType.GEO_ELIGIBILITY:
        return ErrorCategory.REGION_REASONING_ERROR
    if sample.claim_type in {ClaimType.FEATURE_INCLUSION, ClaimType.FEATURE_EXCLUSION}:
        return ErrorCategory.FEATURE_SEMANTICS_ERROR
    if sample.claim_type in {
        ClaimType.PRICE,
        ClaimType.DISCOUNT,
        ClaimType.SUBSCRIPTION_TERMS,
        ClaimType.TRIAL_DURATION,
        ClaimType.MINIMUM_PURCHASE,
    }:
        return ErrorCategory.NUMERIC_REASONING_ERROR
    if row.verification_path is VerificationPath.DETERMINISTIC:
        return ErrorCategory.DETERMINISTIC_RULE_ERROR
    return ErrorCategory.REQUIRES_REVIEW


def wilson_interval(
    successes: int, total: int, *, z: float = 1.959963984540054
) -> dict[str, float]:
    if total == 0:
        return {"low": 0.0, "high": 0.0}
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return {"low": max(0.0, center - margin), "high": min(1.0, center + margin)}


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def calculate_metrics(rows: list[PredictionRow]) -> dict[str, Any]:
    labels = tuple(Verdict)
    matrix = {expected.value: {predicted.value: 0 for predicted in labels} for expected in labels}
    failures: Counter[str] = Counter()
    for row in rows:
        if row.predicted_verdict is None:
            failures[row.expected_verdict.value] += 1
        else:
            matrix[row.expected_verdict.value][row.predicted_verdict.value] += 1
    per_verdict: dict[str, dict[str, float | int]] = {}
    for label in labels:
        true_positive = matrix[label.value][label.value]
        predicted_total = sum(matrix[expected.value][label.value] for expected in labels)
        expected_total = sum(matrix[label.value].values()) + failures[label.value]
        precision = true_positive / predicted_total if predicted_total else 0.0
        recall = true_positive / expected_total if expected_total else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_verdict[label.value] = {
            "support": expected_total,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    correct = sum(row.correct for row in rows)
    mismatch_rows = [row for row in rows if row.expected_verdict is Verdict.CONTRADICTED]
    detected = sum(row.predicted_verdict is Verdict.CONTRADICTED for row in mismatch_rows)
    missed_by_outcome = Counter(
        row.predicted_verdict.value if row.predicted_verdict else "EXECUTION_FAILURE"
        for row in mismatch_rows
        if row.predicted_verdict is not Verdict.CONTRADICTED
    )
    eligible_fp = [row for row in rows if row.expected_verdict is not Verdict.CONTRADICTED]
    false_positives = sum(row.predicted_verdict is Verdict.CONTRADICTED for row in eligible_fp)

    def macro(key: str) -> float:
        return statistics.fmean(float(item[key]) for item in per_verdict.values())

    result: dict[str, Any] = {
        "sample_count": len(rows),
        "completed": sum(row.predicted_verdict is not None for row in rows),
        "execution_failures": sum(failures.values()),
        "correct": correct,
        "incorrect": len(rows) - correct,
        "accuracy": correct / len(rows) if rows else 0.0,
        "accuracy_wilson_95": wilson_interval(correct, len(rows)),
        "macro_precision": macro("precision"),
        "macro_recall": macro("recall"),
        "macro_f1": macro("f1"),
        "per_verdict": per_verdict,
        "mismatch": {
            "total": len(mismatch_rows),
            "detected": detected,
            "recall": detected / len(mismatch_rows) if mismatch_rows else 0.0,
            "wilson_95": wilson_interval(detected, len(mismatch_rows)),
            "false_negatives": len(mismatch_rows) - detected,
            "missed_by_outcome": dict(sorted(missed_by_outcome.items())),
        },
        "false_positive": {
            "count": false_positives,
            "eligible_samples": len(eligible_fp),
            "rate": false_positives / len(eligible_fp) if eligible_fp else 0.0,
            "wilson_95": wilson_interval(false_positives, len(eligible_fp)),
        },
        "confusion_matrix": matrix,
        "execution_failures_by_expected": dict(sorted(failures.items())),
    }
    result["routing"] = routing_metrics(rows)
    result["latency"] = latency_metrics(rows)
    result["by_claim_type"] = grouped_metrics(rows, lambda row: row.claim_type.value)
    result["by_difficulty"] = grouped_metrics(rows, lambda row: row.difficulty.value)
    result["by_verification_path"] = grouped_metrics(
        rows,
        lambda row: row.verification_path.value if row.verification_path else "EXECUTION_FAILURE",
    )
    result["by_mutation"] = grouped_metrics(
        [row for row in rows if row.mutation_type],
        lambda row: row.mutation_type.value if row.mutation_type else "NONE",
    )
    reconcile_metrics(rows, result)
    return result


def grouped_metrics(
    rows: list[PredictionRow], key: Callable[[PredictionRow], str]
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[PredictionRow]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    output = {}
    for name, group in sorted(groups.items()):
        contradictions = [row for row in group if row.expected_verdict is Verdict.CONTRADICTED]
        detected = sum(row.predicted_verdict is Verdict.CONTRADICTED for row in contradictions)
        output[name] = {
            "samples": len(group),
            "accuracy": sum(row.correct for row in group) / len(group),
            "mismatch_total": len(contradictions),
            "mismatch_detected": detected,
            "mismatch_recall": detected / len(contradictions) if contradictions else 0.0,
            "false_positives": sum(
                row.expected_verdict is not Verdict.CONTRADICTED
                and row.predicted_verdict is Verdict.CONTRADICTED
                for row in group
            ),
            "false_negatives": len(contradictions) - detected,
            "llm_routing_rate": sum(row.llm_invoked for row in group) / len(group),
        }
    return output


def routing_metrics(rows: list[PredictionRow]) -> dict[str, Any]:
    counts = Counter(
        row.verification_path.value if row.verification_path else "EXECUTION_FAILURE"
        for row in rows
    )
    total = len(rows)
    llm_extraction = sum(row.extraction_method is ExtractionMethod.LLM_ASSISTED for row in rows)
    return {
        "counts": dict(sorted(counts.items())),
        "rates": {key: value / total if total else 0.0 for key, value in sorted(counts.items())},
        "llm_invocation_rate": sum(row.llm_invoked for row in rows) / total if total else 0.0,
        "llm_extraction_rate": llm_extraction / total if total else 0.0,
        "llm_rating_rate": sum(row.llm_invoked for row in rows) / total if total else 0.0,
    }


def latency_metrics(rows: list[PredictionRow]) -> dict[str, Any]:
    def summary(values: list[float]) -> dict[str, float | int]:
        return {
            "count": len(values),
            "mean": statistics.fmean(values) if values else 0.0,
            "p50": percentile(values, 0.50),
            "p90": percentile(values, 0.90),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "minimum": min(values, default=0.0),
            "maximum": max(values, default=0.0),
        }

    completed = [row for row in rows if row.predicted_verdict is not None]
    deterministic = [row for row in completed if not row.llm_invoked]
    llm = [row for row in completed if row.llm_invoked]
    stages = {
        field: summary([float(getattr(row.latency_ms, field)) for row in completed])
        for field in LatencyBreakdown.model_fields
    }
    provider = [row.provider_latency_ms for row in llm]
    attempts = [row.attempt_count for row in llm]
    return {
        "overall": summary([row.latency_ms.total for row in rows]),
        "deterministic": summary([row.latency_ms.total for row in deterministic]),
        "llm_routed": summary([row.latency_ms.total for row in llm]),
        "stages": stages,
        "provider": summary(provider),
        "mean_attempts": statistics.fmean(attempts) if attempts else 0.0,
        "retry_count": sum(max(0, attempts_count - 1) for attempts_count in attempts),
        "provider_error_count": sum(bool(row.execution_error) for row in rows),
        "tokens": _token_totals(llm),
    }


def _token_totals(rows: list[PredictionRow]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "total_tokens")
    return {
        key: sum(int(row.usage[key] or 0) for row in rows if row.usage and key in row.usage)
        for key in keys
    }


def reconcile_metrics(rows: list[PredictionRow], metrics: dict[str, Any]) -> None:
    total = len(rows)
    if metrics["correct"] + metrics["incorrect"] != total:
        raise ValueError("correct + incorrect does not equal total")
    matrix_total = sum(
        value for predicted in metrics["confusion_matrix"].values() for value in predicted.values()
    )
    if matrix_total + metrics["execution_failures"] != total:
        raise ValueError("confusion matrix and failures do not reconcile")
    mismatch = metrics["mismatch"]
    if mismatch["detected"] + mismatch["false_negatives"] != mismatch["total"]:
        raise ValueError("mismatch TP + FN does not reconcile")
    if sum(metrics["routing"]["counts"].values()) != total:
        raise ValueError("routing counts do not reconcile")
