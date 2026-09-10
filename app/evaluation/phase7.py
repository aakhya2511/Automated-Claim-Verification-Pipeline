"""Provider-pinned Phase 7 candidate evaluation over the development corpus only."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

from app.bootstrap import build_container
from app.core.config import REPO_ROOT, Settings
from app.core.pipeline_config import PipelineConfig, load_pipeline_config
from app.domain.enums import Verdict
from app.evaluation.baseline import canonical_hash, file_hash, prompt_hash, source_tree_hash
from app.evaluation.metrics import PredictionRow, calculate_metrics, prediction_from_result
from app.evaluation.reporting import error_analysis
from app.evaluation.runner import (
    FROZEN_HOLDOUT_SHA256,
    EvaluationRunError,
    _write_artifacts,
    load_or_initialize_checkpoint,
    write_checkpoint,
)
from app.evaluation.validator import load_samples
from app.raters.ollama import preflight_ollama

PHASE7_DATASET_SHA256 = "e1d2c8cd5668eb64a82794a19bc19c9dc760641b31d06e31bc71e161add6ae34"
PHASE7_MODEL = "qwen2.5:7b"
PHASE7_MODEL_DIGEST = "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e"
PHASE7_TEMPERATURE = 0.0
PHASE7_PROVIDER_TIMEOUT_SECONDS = 60.0
PHASE7_VERIFICATION_TIMEOUT_SECONDS = 75.0
CHECKPOINT_BATCH_SIZE = 10


def phase7_settings(settings: Settings, config_file: str) -> Settings:
    return settings.model_copy(
        update={
            "pipeline_config_file": config_file,
            "server": settings.server.model_copy(
                update={
                    "verification_timeout_seconds": PHASE7_VERIFICATION_TIMEOUT_SECONDS,
                    "batch_concurrency": 1,
                }
            ),
            "llm": settings.llm.model_copy(
                update={
                    "timeout_seconds": PHASE7_PROVIDER_TIMEOUT_SECONDS,
                    "max_concurrency": 1,
                    "temperature": PHASE7_TEMPERATURE,
                }
            ),
        }
    )


def _git_revision() -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "git_commit": commit,
        "source_tree_sha256": source_tree_hash(REPO_ROOT),
        "working_tree_status": status or "clean",
    }


def _validate_inputs(dataset: Path, config_path: Path, config: PipelineConfig) -> None:
    if file_hash(dataset) != PHASE7_DATASET_SHA256:
        raise EvaluationRunError("Phase 7 diagnostic corpus hash does not match the frozen input")
    holdout = REPO_ROOT / "data/evaluation/v1/benchmark_500.jsonl"
    if file_hash(holdout) != FROZEN_HOLDOUT_SHA256:
        raise EvaluationRunError("frozen holdout hash changed")
    if file_hash(dataset) == FROZEN_HOLDOUT_SHA256:
        raise EvaluationRunError("refusing to evaluate the frozen holdout")
    if not config_path.resolve().is_relative_to((REPO_ROOT / "configs/phase7").resolve()):
        raise EvaluationRunError("Phase 7 candidates must live under configs/phase7")
    if config.normalization.llm_extraction_fallback:
        raise EvaluationRunError("LLM extraction requires separate residual justification")
    if config.cache.enabled:
        raise EvaluationRunError("Phase 7 quality candidates must keep cache disabled")
    if config.rater.prompt_version != "v1":
        raise EvaluationRunError("prompt changes require a separately justified Phase 7 candidate")


def _candidate_snapshot(
    *,
    config_path: Path,
    config: PipelineConfig,
    settings: Settings,
    provider_metadata: dict[str, Any],
    parent: str,
    intervention: str,
) -> dict[str, Any]:
    effective = config.model_dump(mode="json", exclude_none=True)
    snapshot: dict[str, Any] = {
        "candidate": config.name,
        "parent_candidate": parent,
        "intervention": intervention,
        "config_path": config_path.relative_to(REPO_ROOT).as_posix(),
        "config_file_sha256": file_hash(config_path),
        "effective_config_sha256": canonical_hash(effective),
        "pipeline_config": effective,
        "source_revision": _git_revision(),
        "provider": settings.llm.provider,
        "model": settings.llm.model,
        "model_digest": provider_metadata["model_digest"],
        "ollama_version": provider_metadata["ollama_version"],
        "temperature": settings.llm.temperature,
        "provider_timeout_seconds": settings.llm.timeout_seconds,
        "verification_timeout_seconds": settings.server.verification_timeout_seconds,
        "evaluation_concurrency": 1,
        "provider_concurrency": settings.llm.max_concurrency,
        "service_batch_concurrency": settings.server.batch_concurrency,
        "dataset_sha256": PHASE7_DATASET_SHA256,
        "rater_prompt_version": config.rater.prompt_version,
        "rater_prompt_sha256": prompt_hash(
            settings.data.prompts_dir, "rater", config.rater.prompt_version
        ),
        "schema_version": config.rater.schema_version,
        "catalog_fingerprint": file_hash(settings.data.reference_catalog_path),
    }
    snapshot["config_hash"] = canonical_hash(snapshot)
    return snapshot


def _load_rows(path: Path) -> list[PredictionRow]:
    return [
        PredictionRow.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _reevaluation_ids(
    parent_rows: list[PredictionRow],
    affected_claim_types: frozenset[str] | None,
    rerun_parent_llm_only: bool,
) -> set[str]:
    if rerun_parent_llm_only:
        return {
            row.sample_id
            for row in parent_rows
            if row.llm_invoked or row.execution_error is not None
        }
    if affected_claim_types is None:
        return {row.sample_id for row in parent_rows}
    return {
        row.sample_id
        for row in parent_rows
        if row.claim_type.value in affected_claim_types
        or (
            row.normalized_claim is not None
            and row.normalized_claim.claim_type.value in affected_claim_types
        )
    }


def _comparison(
    parent_rows: list[PredictionRow],
    rows: list[PredictionRow],
    parent_metrics: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    parent_by_id = {row.sample_id: row for row in parent_rows}
    current_by_id = {row.sample_id: row for row in rows}
    if set(parent_by_id) != set(current_by_id):
        raise EvaluationRunError("candidate and parent prediction identities differ")
    fixed = sorted(
        sample_id
        for sample_id, row in current_by_id.items()
        if row.correct and not parent_by_id[sample_id].correct
    )
    regressions = sorted(
        sample_id
        for sample_id, row in current_by_id.items()
        if not row.correct and parent_by_id[sample_id].correct
    )
    unchanged_errors = sorted(
        sample_id
        for sample_id, row in current_by_id.items()
        if not row.correct and not parent_by_id[sample_id].correct
    )

    def delta(key: str) -> dict[str, float]:
        before = float(parent_metrics[key])
        after = float(metrics[key])
        return {
            "parent": before,
            "candidate": after,
            "absolute_change": after - before,
            "relative_change": (after - before) / before if before else 0.0,
        }

    return {
        "fixed_count": len(fixed),
        "regression_count": len(regressions),
        "unchanged_error_count": len(unchanged_errors),
        "fixed_sample_ids": fixed,
        "regression_sample_ids": regressions,
        "unchanged_error_sample_ids": unchanged_errors,
        "accuracy": delta("accuracy"),
        "macro_f1": delta("macro_f1"),
        "mismatch_recall": {
            "parent": parent_metrics["mismatch"]["recall"],
            "candidate": metrics["mismatch"]["recall"],
            "absolute_change": metrics["mismatch"]["recall"] - parent_metrics["mismatch"]["recall"],
        },
        "false_positive_rate": {
            "parent": parent_metrics["false_positive"]["rate"],
            "candidate": metrics["false_positive"]["rate"],
            "absolute_change": metrics["false_positive"]["rate"]
            - parent_metrics["false_positive"]["rate"],
        },
        "llm_invocation_rate": {
            "parent": parent_metrics["routing"]["llm_invocation_rate"],
            "candidate": metrics["routing"]["llm_invocation_rate"],
            "absolute_change": metrics["routing"]["llm_invocation_rate"]
            - parent_metrics["routing"]["llm_invocation_rate"],
        },
    }


def _invalid_recall(rows: list[PredictionRow]) -> float:
    invalid = [row for row in rows if row.expected_verdict is Verdict.INVALID_CLAIM]
    return sum(row.predicted_verdict is Verdict.INVALID_CLAIM for row in invalid) / len(invalid)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def run_candidate(
    *,
    dataset: Path,
    config_path: Path,
    output: Path,
    experiment_output: Path,
    parent_name: str,
    parent_predictions: Path,
    parent_metrics_path: Path,
    intervention: str,
    affected_claim_types: frozenset[str] | None = None,
    rerun_parent_llm_only: bool = False,
    settings: Settings | None = None,
) -> dict[str, Any]:
    config = load_pipeline_config(config_path)
    _validate_inputs(dataset, config_path, config)
    resolved_config_path = await asyncio.to_thread(config_path.resolve)
    resolved = phase7_settings(
        settings or Settings(),
        resolved_config_path.relative_to(REPO_ROOT / "configs").as_posix(),
    )
    if resolved.llm.provider != "ollama" or resolved.llm.model != PHASE7_MODEL:
        raise EvaluationRunError("Phase 7 requires Ollama qwen2.5:7b")
    identity = await preflight_ollama(resolved.llm)
    if identity.model_digest != PHASE7_MODEL_DIGEST:
        raise EvaluationRunError("Phase 7 Ollama model digest does not match the baseline")
    provider_metadata = identity.model_dump(mode="json")
    snapshot = _candidate_snapshot(
        config_path=resolved_config_path,
        config=config,
        settings=resolved,
        provider_metadata=provider_metadata,
        parent=parent_name,
        intervention=intervention,
    )
    snapshot.pop("config_hash")
    snapshot["reuse_policy"] = {
        "affected_claim_types": sorted(affected_claim_types or ()),
        "rerun_parent_llm_only": rerun_parent_llm_only,
    }
    snapshot["config_hash"] = canonical_hash(snapshot)
    samples = load_samples(dataset)
    parent_rows = await asyncio.to_thread(_load_rows, parent_predictions)
    parent_metrics: dict[str, Any] = await asyncio.to_thread(
        lambda: json.loads(parent_metrics_path.read_text(encoding="utf-8"))
    )
    checkpoint_rows = load_or_initialize_checkpoint(
        output,
        samples,
        dataset_sha256=PHASE7_DATASET_SHA256,
        config_hash=str(snapshot["config_hash"]),
        provider=resolved.llm.provider,
        model=resolved.llm.model,
        model_digest=identity.model_digest,
    )
    reused_count = 0
    if not checkpoint_rows and (affected_claim_types is not None or rerun_parent_llm_only):
        reevaluate_ids = _reevaluation_ids(parent_rows, affected_claim_types, rerun_parent_llm_only)
        checkpoint_rows = [row for row in parent_rows if row.sample_id not in reevaluate_ids]
        reused_count = len(checkpoint_rows)
        write_checkpoint(output, checkpoint_rows)
    container = build_container(settings=resolved, pipeline_config=config)
    if container.service is None:
        raise EvaluationRunError("verification service was not constructed")
    started = perf_counter()
    try:
        completed_ids = {row.sample_id for row in checkpoint_rows}
        pending = [sample for sample in samples if sample.sample_id not in completed_ids]
        for offset in range(0, len(pending), CHECKPOINT_BATCH_SIZE):
            batch = pending[offset : offset + CHECKPOINT_BATCH_SIZE]
            results = await container.service.verify_batch(
                [sample.to_verification_request() for sample in batch]
            )
            checkpoint_rows.extend(
                prediction_from_result(sample, result)
                for sample, result in zip(batch, results, strict=True)
            )
            order = {sample.sample_id: index for index, sample in enumerate(samples)}
            checkpoint_rows.sort(key=lambda row: order[row.sample_id])
            write_checkpoint(output, checkpoint_rows)
    finally:
        await container.aclose()
    rows = checkpoint_rows
    if len(rows) != 400:
        raise EvaluationRunError("Phase 7 candidate did not produce 400 prediction records")
    metrics = calculate_metrics(rows)
    metrics["invalid_claim_recall"] = _invalid_recall(rows)
    analysis = error_analysis(rows)
    comparison = _comparison(parent_rows, rows, parent_metrics, metrics)
    summary = {
        "status": "COMPLETE",
        "candidate": config.name,
        "parent_candidate": parent_name,
        "prediction_count": len(rows),
        "dataset_sha256": PHASE7_DATASET_SHA256,
        "config_hash": snapshot["config_hash"],
        "elapsed_ms": (perf_counter() - started) * 1000,
        "metrics_reconciliation": "PASS",
        "error_analysis_reconciliation": "PASS",
        "reused_parent_predictions": reused_count,
    }
    await asyncio.to_thread(_write_artifacts, output, rows, metrics, analysis, summary)
    _write_json(output / "comparison_to_parent.json", comparison)
    _write_json(output / "candidate_identity.json", snapshot)
    _write_json(experiment_output / "config.json", snapshot)
    _write_json(
        experiment_output / "metadata.json",
        {
            **summary,
            "intervention": intervention,
            "metrics": metrics,
            "comparison_to_parent": comparison,
        },
    )
    return summary
