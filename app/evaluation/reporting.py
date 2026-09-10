"""Deterministic error analysis and human-readable baseline reports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from app.domain.enums import Verdict
from app.evaluation.metrics import ErrorCategory, PredictionRow

STAGE_BY_CATEGORY = {
    ErrorCategory.PARSER_ERROR: "normalization",
    ErrorCategory.ENTITY_RESOLUTION_ERROR: "retrieval",
    ErrorCategory.EVIDENCE_SELECTION_ERROR: "retrieval",
    ErrorCategory.ESCALATION_ERROR: "routing",
    ErrorCategory.DETERMINISTIC_RULE_ERROR: "rules",
    ErrorCategory.QUALIFIER_INTERPRETATION_ERROR: "normalization",
    ErrorCategory.NEGATION_ERROR: "normalization",
    ErrorCategory.NUMERIC_REASONING_ERROR: "rules",
    ErrorCategory.TEMPORAL_REASONING_ERROR: "rules",
    ErrorCategory.REGION_REASONING_ERROR: "rules",
    ErrorCategory.FEATURE_SEMANTICS_ERROR: "rules",
    ErrorCategory.MISSING_EVIDENCE_ERROR: "rules",
    ErrorCategory.LLM_SEMANTIC_ERROR: "LLM",
    ErrorCategory.LLM_OVER_INFERENCE: "LLM",
    ErrorCategory.LLM_UNDER_INFERENCE: "LLM",
    ErrorCategory.CONFIDENCE_ERROR: "decision",
    ErrorCategory.EXECUTION_FAILURE: "execution",
    ErrorCategory.REQUIRES_REVIEW: "requires_review",
}


def error_analysis(rows: list[PredictionRow]) -> dict[str, Any]:
    incorrect = [row for row in rows if not row.correct]
    false_positives = [
        row
        for row in rows
        if row.expected_verdict is not Verdict.CONTRADICTED
        and row.predicted_verdict is Verdict.CONTRADICTED
    ]
    missed = [
        row
        for row in rows
        if row.expected_verdict is Verdict.CONTRADICTED
        and row.predicted_verdict is not Verdict.CONTRADICTED
    ]
    categories = Counter(
        (row.error_category or ErrorCategory.REQUIRES_REVIEW).value for row in incorrect
    )
    stages = Counter(
        STAGE_BY_CATEGORY[row.error_category or ErrorCategory.REQUIRES_REVIEW] for row in incorrect
    )
    result = {
        "total_incorrect": len(incorrect),
        "false_positives": len(false_positives),
        "missed_mismatches": len(missed),
        "by_taxonomy": dict(sorted(categories.items())),
        "by_stage": dict(sorted(stages.items())),
        "by_claim_type": _counts(row.claim_type.value for row in incorrect),
        "by_mutation_type": _counts(
            row.mutation_type.value for row in missed if row.mutation_type is not None
        ),
        "by_difficulty": _counts(row.difficulty.value for row in incorrect),
        "by_verification_path": _counts(
            row.verification_path.value if row.verification_path else "EXECUTION_FAILURE"
            for row in incorrect
        ),
        "representative_examples": [
            {
                "sample_id": row.sample_id,
                "expected": row.expected_verdict.value,
                "predicted": row.predicted_verdict.value if row.predicted_verdict else None,
                "claim_type": row.claim_type.value,
                "category": (row.error_category or ErrorCategory.REQUIRES_REVIEW).value,
            }
            for row in incorrect[:20]
        ],
    }
    reconcile_error_analysis(rows, result)
    return result


def reconcile_error_analysis(rows: list[PredictionRow], analysis: dict[str, Any]) -> None:
    if sum(analysis["by_taxonomy"].values()) != analysis["total_incorrect"]:
        raise ValueError("error taxonomy does not reconcile")
    if sum(analysis["by_stage"].values()) != analysis["total_incorrect"]:
        raise ValueError("failure funnel does not reconcile")
    if analysis["total_incorrect"] != sum(not row.correct for row in rows):
        raise ValueError("error total does not reconcile with predictions")


def hypotheses_markdown(analysis: dict[str, Any]) -> str:
    ranked = sorted(analysis["by_taxonomy"].items(), key=lambda item: (-item[1], item[0]))
    lines = [
        "# Phase 7 hypothesis backlog",
        "",
        "Generated from development-corpus baseline errors. No intervention is implemented here.",
        "",
    ]
    for rank, (category, count) in enumerate(ranked, start=1):
        lines.extend(
            [
                f"## {rank}. {category}",
                "",
                f"- Affected samples: {count}",
                "- Affected metric: accuracy; mismatch recall or false-positive rate "
                "where applicable",
                f"- Suspected root cause: {category.lower().replace('_', ' ')}",
                "- Possible intervention: investigate the responsible stage on the "
                "development corpus",
                "- Risk/trade-off: may transfer errors to another verdict class; "
                "validate before adoption",
                "",
            ]
        )
    if not ranked:
        lines.extend(["No baseline errors were observed.", ""])
    return "\n".join(lines)


def summary_markdown(metrics: dict[str, Any], analysis: dict[str, Any]) -> str:
    mismatch = metrics["mismatch"]
    false_positive = metrics["false_positive"]
    return "\n".join(
        [
            "# Development baseline summary",
            "",
            f"- Samples: {metrics['sample_count']}",
            f"- Completed: {metrics['completed']}",
            f"- Accuracy: {metrics['accuracy']:.4%}",
            f"- Macro precision: {metrics['macro_precision']:.4%}",
            f"- Macro recall: {metrics['macro_recall']:.4%}",
            f"- Macro F1: {metrics['macro_f1']:.4%}",
            (
                f"- Mismatch detection: {mismatch['detected']} / {mismatch['total']} "
                f"= {mismatch['recall']:.4%}"
            ),
            (
                f"- False positives: {false_positive['count']} / "
                f"{false_positive['eligible_samples']} = {false_positive['rate']:.4%}"
            ),
            f"- Execution failures: {metrics['execution_failures']}",
            f"- Total incorrect: {analysis['total_incorrect']}",
            "",
            "False positive means a non-CONTRADICTED ground truth predicted CONTRADICTED.",
            "Execution failures remain in the primary accuracy denominator.",
            "",
        ]
    )


def error_markdown(analysis: dict[str, Any]) -> str:
    lines = [
        "# Baseline error analysis",
        "",
        f"- Total incorrect: {analysis['total_incorrect']}",
        f"- False positives: {analysis['false_positives']}",
        f"- Missed mismatches: {analysis['missed_mismatches']}",
        "",
        "## Failure funnel",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in analysis["by_stage"].items())
    lines.extend(["", "## Error taxonomy", ""])
    lines.extend(f"- {key}: {value}" for key, value in analysis["by_taxonomy"].items())
    lines.append("")
    return "\n".join(lines)


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))
