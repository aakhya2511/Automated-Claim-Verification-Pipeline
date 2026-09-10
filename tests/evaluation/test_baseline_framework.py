from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.domain.enums import ClaimType, Verdict, VerificationPath
from app.domain.models import LatencyBreakdown
from app.evaluation.baseline import canonical_hash
from app.evaluation.dev_corpus import (
    DEV_SAMPLE_COUNT,
    DEV_SEED,
    generate_diagnostic,
    overlap_report,
    validate_dev_bundle,
)
from app.evaluation.generator import load_catalog
from app.evaluation.metrics import (
    ErrorCategory,
    PredictionRow,
    calculate_metrics,
    percentile,
    wilson_interval,
)
from app.evaluation.models import Difficulty
from app.evaluation.reporting import error_analysis
from app.evaluation.runner import (
    EvaluationRunError,
    assert_not_holdout,
    load_snapshot,
    run_evaluation,
)
from app.evaluation.validator import load_samples

CATALOG = Path("data/reference/catalog.jsonl")
HOLDOUT = Path("data/evaluation/v1/benchmark_500.jsonl")
DIAGNOSTIC = Path("data/evaluation/dev/v1/diagnostic.jsonl")


def row(
    sample_id: str,
    expected: Verdict,
    predicted: Verdict | None,
    *,
    latency: float,
    path: VerificationPath | None = VerificationPath.DETERMINISTIC,
    llm: bool = False,
) -> PredictionRow:
    return PredictionRow(
        sample_id=sample_id,
        expected_verdict=expected,
        predicted_verdict=predicted,
        correct=expected is predicted,
        claim_type=ClaimType.PRICE,
        difficulty=Difficulty.MODERATE,
        verification_path=path if predicted else None,
        latency_ms=LatencyBreakdown(total=latency),
        llm_invoked=llm,
        execution_error=None if predicted else "provider_timeout",
        error_category=(
            None
            if expected is predicted
            else ErrorCategory.EXECUTION_FAILURE
            if predicted is None
            else ErrorCategory.REQUIRES_REVIEW
        ),
    )


@pytest.fixture
def prediction_rows() -> list[PredictionRow]:
    return [
        row("dev-000001", Verdict.SUPPORTED, Verdict.SUPPORTED, latency=10),
        row("dev-000002", Verdict.CONTRADICTED, Verdict.CONTRADICTED, latency=20),
        row("dev-000003", Verdict.CONTRADICTED, Verdict.SUPPORTED, latency=30),
        row(
            "dev-000004",
            Verdict.INSUFFICIENT_EVIDENCE,
            Verdict.CONTRADICTED,
            latency=40,
            path=VerificationPath.LLM_RATER,
            llm=True,
        ),
        row("dev-000005", Verdict.INVALID_CLAIM, None, latency=50),
    ]


def test_core_metrics_confusion_fp_fn_and_mismatch_reconcile(prediction_rows) -> None:
    metrics = calculate_metrics(prediction_rows)
    assert metrics["sample_count"] == 5
    assert metrics["completed"] == 4
    assert metrics["correct"] == 2
    assert metrics["accuracy"] == pytest.approx(0.4)
    assert metrics["mismatch"] == {
        "total": 2,
        "detected": 1,
        "recall": 0.5,
        "wilson_95": wilson_interval(1, 2),
        "false_negatives": 1,
        "missed_by_outcome": {"SUPPORTED": 1},
    }
    assert metrics["false_positive"]["count"] == 1
    assert metrics["false_positive"]["eligible_samples"] == 3
    assert metrics["false_positive"]["rate"] == pytest.approx(1 / 3)
    matrix_total = sum(
        count for expected in metrics["confusion_matrix"].values() for count in expected.values()
    )
    assert matrix_total == 4
    assert metrics["execution_failures"] == 1


def test_routing_latency_and_per_category_aggregation(prediction_rows) -> None:
    metrics = calculate_metrics(prediction_rows)
    assert sum(metrics["routing"]["counts"].values()) == 5
    assert metrics["routing"]["llm_invocation_rate"] == pytest.approx(0.2)
    assert metrics["latency"]["overall"]["p50"] == 30
    assert metrics["latency"]["overall"]["p95"] == pytest.approx(48)
    assert metrics["by_claim_type"]["price"]["samples"] == 5
    assert metrics["by_difficulty"]["MODERATE"]["false_positives"] == 1


def test_percentiles_and_wilson_intervals() -> None:
    assert percentile([], 0.95) == 0
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert wilson_interval(0, 0) == {"low": 0.0, "high": 0.0}
    interval = wilson_interval(50, 100)
    assert interval["low"] < 0.5 < interval["high"]


def test_error_taxonomy_and_failure_funnel_reconcile(prediction_rows) -> None:
    analysis = error_analysis(prediction_rows)
    assert analysis["total_incorrect"] == 3
    assert sum(analysis["by_taxonomy"].values()) == 3
    assert sum(analysis["by_stage"].values()) == 3


def test_prediction_serialization_keeps_evaluation_data_outside_request() -> None:
    samples = load_samples(DIAGNOSTIC)
    request = samples[0].to_verification_request()
    prediction = row("dev-999999", Verdict.SUPPORTED, Verdict.SUPPORTED, latency=1)
    assert "expected_verdict" not in request.model_dump()
    assert "expected_verdict" in prediction.model_dump()
    assert PredictionRow.model_validate_json(prediction.model_dump_json()) == prediction


def test_diagnostic_generation_is_deterministic_and_has_no_holdout_overlap() -> None:
    records, _fingerprint = load_catalog(CATALOG)
    holdout = load_samples(HOLDOUT)
    first = generate_diagnostic(records, holdout, seed=DEV_SEED)
    second = generate_diagnostic(records, holdout, seed=DEV_SEED)
    assert first == second
    assert len(first) == DEV_SAMPLE_COUNT
    assert overlap_report(first, holdout).is_clear
    assert sum(sample.generation.template_id.startswith("semantic_") for sample in first) >= 20


def test_frozen_diagnostic_bundle_validates() -> None:
    manifest, overlap = validate_dev_bundle(DIAGNOSTIC, catalog_path=CATALOG, holdout_path=HOLDOUT)
    assert manifest.sample_count == DEV_SAMPLE_COUNT
    assert overlap.is_clear


def test_holdout_guard_uses_content_hash(tmp_path: Path) -> None:
    copied = tmp_path / "renamed.jsonl"
    copied.write_bytes(HOLDOUT.read_bytes())
    with pytest.raises(EvaluationRunError, match="frozen Phase 5 holdout"):
        assert_not_holdout(copied)
    assert hashlib.sha256(HOLDOUT.read_bytes()).hexdigest() == (
        "0868111bf882b702738fc82720c2ccf51bbe2776645740a7fc20a93f724af9c7"
    )


def test_baseline_config_hash_detects_tampering(tmp_path: Path) -> None:
    snapshot = {"provider": "openai", "model": "test-model"}
    snapshot["config_hash"] = canonical_hash(snapshot)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    assert load_snapshot(path)["model"] == "test-model"
    path.write_text(path.read_text().replace("test-model", "tampered"), encoding="utf-8")
    with pytest.raises(EvaluationRunError, match="hash"):
        load_snapshot(path)


async def test_official_runner_rejects_fake_provider(tmp_path: Path) -> None:
    with pytest.raises(EvaluationRunError, match="real LLM provider"):
        await run_evaluation(
            dataset=DIAGNOSTIC,
            config_path=Path("experiments/baseline/v1/config.json"),
            output=tmp_path,
            settings=Settings(_env_file=None),
        )
