"""Real-service diagnostic runner with holdout and fake-provider guards."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from app.bootstrap import build_container
from app.core.config import Settings
from app.core.pipeline_config import PipelineConfig
from app.domain.enums import Verdict
from app.evaluation.baseline import (
    canonical_hash,
    file_hash,
    git_identity,
    prompt_hash,
    source_tree_hash,
    unexpected_git_changes,
)
from app.evaluation.dev_corpus import validate_dev_bundle
from app.evaluation.metrics import PredictionRow, calculate_metrics, prediction_from_result
from app.evaluation.models import EvaluationSample
from app.evaluation.reporting import (
    error_analysis,
    error_markdown,
    hypotheses_markdown,
    summary_markdown,
)
from app.evaluation.validator import load_samples
from app.raters.ollama import preflight_ollama

FROZEN_HOLDOUT_SHA256 = "0868111bf882b702738fc82720c2ccf51bbe2776645740a7fc20a93f724af9c7"
CHECKPOINT_BATCH_SIZE = 10


class EvaluationRunError(RuntimeError):
    pass


def validate_real_provider(settings: Settings) -> None:
    if settings.llm.provider == "fake":
        raise EvaluationRunError("official baseline requires a configured real LLM provider")
    if settings.llm.provider == "openai" and settings.llm.api_key is None:
        raise EvaluationRunError("official OpenAI baseline requires a configured credential")


def assert_not_holdout(dataset: Path) -> None:
    if hashlib.sha256(dataset.read_bytes()).hexdigest() == FROZEN_HOLDOUT_SHA256:
        raise EvaluationRunError("refusing to evaluate the frozen Phase 5 holdout")


def load_snapshot(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    claimed_hash = value.get("config_hash")
    unhashed = {key: item for key, item in value.items() if key != "config_hash"}
    if claimed_hash != canonical_hash(unhashed):
        raise EvaluationRunError("baseline configuration hash does not verify")
    return value


def validate_baseline_integrity(
    snapshot: dict[str, Any],
    *,
    dataset: Path,
    settings: Settings,
    root: Path | None = None,
) -> None:
    """Validate frozen inputs while permitting only named generated outputs."""
    repository = root or Path(__file__).resolve().parents[2]
    commit, _status, _clean = git_identity(repository)
    changes = unexpected_git_changes(repository)
    if commit is None or changes is None:
        raise EvaluationRunError("official baseline requires a valid Git repository")
    if snapshot.get("git_commit") != commit:
        raise EvaluationRunError("current Git commit does not match frozen baseline")
    if changes:
        changed = ", ".join(sorted(changes))
        raise EvaluationRunError(f"non-generated files changed after baseline freeze: {changed}")
    if snapshot.get("source_tree_sha256") != source_tree_hash(repository):
        raise EvaluationRunError("current source tree does not match frozen baseline")

    rater_version = snapshot.get("rater_prompt_version")
    extraction_version = snapshot.get("extraction_prompt_version")
    if not isinstance(rater_version, str) or snapshot.get("rater_prompt_sha256") != prompt_hash(
        settings.data.prompts_dir, "rater", rater_version
    ):
        raise EvaluationRunError("rater prompt does not match frozen baseline")
    if not isinstance(extraction_version, str) or snapshot.get(
        "extraction_prompt_sha256"
    ) != prompt_hash(settings.data.prompts_dir, "extractor", extraction_version):
        raise EvaluationRunError("extraction prompt does not match frozen baseline")
    if snapshot.get("catalog_fingerprint") != file_hash(settings.data.reference_catalog_path):
        raise EvaluationRunError("reference catalog does not match frozen baseline")
    if snapshot.get("diagnostic_dataset_sha256") != file_hash(dataset):
        raise EvaluationRunError("diagnostic dataset does not match frozen baseline")

    validate_real_provider(settings)
    if (
        snapshot.get("provider") != settings.llm.provider
        or snapshot.get("model") != settings.llm.model
    ):
        raise EvaluationRunError("runtime provider/model does not match frozen baseline")
    if snapshot.get("provider_base_url") != settings.llm.base_url:
        raise EvaluationRunError("runtime provider endpoint does not match frozen baseline")
    if snapshot.get("temperature") != settings.llm.temperature:
        raise EvaluationRunError("runtime temperature does not match frozen baseline")


async def run_evaluation(
    *, dataset: Path, config_path: Path, output: Path, settings: Settings | None = None
) -> dict[str, Any]:
    assert_not_holdout(dataset)
    resolved = settings or Settings()
    snapshot = load_snapshot(config_path)
    validate_baseline_integrity(snapshot, dataset=dataset, settings=resolved)
    if resolved.llm.provider == "ollama":
        identity = await preflight_ollama(resolved.llm)
        frozen_identity = snapshot.get("provider_metadata")
        if not isinstance(frozen_identity, dict) or (
            frozen_identity.get("model_digest") != identity.model_digest
        ):
            raise EvaluationRunError("Ollama model digest does not match frozen baseline")
    validate_dev_bundle(
        dataset,
        catalog_path=resolved.data.reference_catalog_path,
        holdout_path=resolved.data.evaluation_dataset_path,
    )
    pipeline = PipelineConfig.model_validate(snapshot["pipeline_config"])
    samples = load_samples(dataset)
    dataset_sha256 = await asyncio.to_thread(_file_hash, dataset)
    checkpoint_rows = await asyncio.to_thread(
        load_or_initialize_checkpoint,
        output,
        samples,
        dataset_sha256=dataset_sha256,
        config_hash=str(snapshot["config_hash"]),
        provider=resolved.llm.provider,
        model=resolved.llm.model,
    )
    container = build_container(settings=resolved, pipeline_config=pipeline)
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
            checkpoint_rows = _order_rows(checkpoint_rows, samples)
            await asyncio.to_thread(write_checkpoint, output, checkpoint_rows)
    finally:
        await container.aclose()
    rows = _order_rows(checkpoint_rows, samples)
    if len(rows) != len(samples):
        raise EvaluationRunError("prediction count does not match diagnostic sample count")
    metrics = calculate_metrics(rows)
    analysis = error_analysis(rows)
    summary = {
        "status": "COMPLETE",
        "dataset_sha256": dataset_sha256,
        "config_hash": snapshot["config_hash"],
        "prediction_count": len(rows),
        "elapsed_ms": (perf_counter() - started) * 1000,
        "metrics_reconciliation": "PASS",
        "error_analysis_reconciliation": "PASS",
    }
    await asyncio.to_thread(_write_artifacts, output, rows, metrics, analysis, summary)
    return summary


def _false_positives(rows: list[PredictionRow]) -> list[PredictionRow]:
    return [
        row
        for row in rows
        if row.expected_verdict is not Verdict.CONTRADICTED
        and row.predicted_verdict is Verdict.CONTRADICTED
    ]


def _missed_mismatches(rows: list[PredictionRow]) -> list[PredictionRow]:
    return [
        row
        for row in rows
        if row.expected_verdict is Verdict.CONTRADICTED
        and row.predicted_verdict is not Verdict.CONTRADICTED
    ]


def _write_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[PredictionRow]) -> None:
    content = "\n".join(row.model_dump_json() for row in rows)
    _atomic_text(path, content + ("\n" if rows else ""))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_artifacts(
    output: Path,
    rows: list[PredictionRow],
    metrics: dict[str, Any],
    analysis: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "predictions.jsonl", rows)
    _write_json(output / "metrics.json", metrics)
    _write_json(output / "confusion_matrix.json", metrics["confusion_matrix"])
    _write_json(output / "routing.json", metrics["routing"])
    _write_json(output / "latency.json", metrics["latency"])
    _write_json(output / "error_analysis.json", analysis)
    _atomic_text(output / "summary.md", summary_markdown(metrics, analysis))
    _atomic_text(output / "error_analysis.md", error_markdown(analysis))
    _atomic_text(output / "phase7_hypotheses.md", hypotheses_markdown(analysis))
    _write_jsonl(output / "false_positives.jsonl", _false_positives(rows))
    _write_jsonl(output / "missed_mismatches.jsonl", _missed_mismatches(rows))
    _write_json(output / "summary.json", summary)


def load_or_initialize_checkpoint(
    output: Path,
    samples: list[EvaluationSample],
    *,
    dataset_sha256: str,
    config_hash: str,
    provider: str,
    model: str,
) -> list[PredictionRow]:
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / "checkpoint.meta.json"
    predictions_path = output / "checkpoint.jsonl"
    expected_metadata = {
        "dataset_sha256": dataset_sha256,
        "config_hash": config_hash,
        "provider": provider,
        "model": model,
    }
    if metadata_path.exists():
        actual_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if actual_metadata != expected_metadata:
            raise EvaluationRunError("checkpoint identity does not match this baseline run")
    else:
        if predictions_path.exists():
            raise EvaluationRunError("checkpoint predictions exist without identity metadata")
        _write_json(metadata_path, expected_metadata)
    rows = (
        [
            PredictionRow.model_validate_json(line)
            for line in predictions_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if predictions_path.exists()
        else []
    )
    sample_ids = {sample.sample_id for sample in samples}
    row_ids = [row.sample_id for row in rows]
    if len(row_ids) != len(set(row_ids)):
        raise EvaluationRunError("checkpoint contains duplicate sample IDs")
    if not set(row_ids) <= sample_ids:
        raise EvaluationRunError("checkpoint contains samples outside the diagnostic corpus")
    return rows


def write_checkpoint(output: Path, rows: list[PredictionRow]) -> None:
    _write_jsonl(output / "checkpoint.jsonl", rows)


def _order_rows(rows: list[PredictionRow], samples: list[EvaluationSample]) -> list[PredictionRow]:
    order = {sample.sample_id: index for index, sample in enumerate(samples)}
    return sorted(rows, key=lambda row: order[row.sample_id])


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
