from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from app.core.config import LLMSettings, ServerSettings, Settings
from app.evaluation.baseline import (
    BASELINE_BATCH_CONCURRENCY,
    BASELINE_EVALUATION_CONCURRENCY,
    BASELINE_LLM_TIMEOUT_SECONDS,
    BASELINE_VERIFICATION_TIMEOUT_SECONDS,
    canonical_hash,
    file_hash,
    official_baseline_settings,
    operational_config,
    validate_official_timeout_hierarchy,
    write_baseline_snapshot,
)
from app.evaluation.runner import (
    EvaluationRunError,
    load_snapshot,
    validate_baseline_integrity,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def frozen_repository(tmp_path: Path) -> tuple[Path, Path, Path, Settings]:
    diagnostic = tmp_path / "data/evaluation/dev/v1/diagnostic.jsonl"
    catalog = tmp_path / "data/reference/catalog.jsonl"
    holdout = tmp_path / "data/evaluation/v1/benchmark_500.jsonl"
    config = tmp_path / "experiments/baseline/v1/config.json"
    _write(tmp_path / "app/verification/service.py", "BEHAVIOR = 'v1'\n")
    _write(tmp_path / "prompts/rater/v1.txt", "rate exactly\n")
    _write(tmp_path / "prompts/extractor/v1.txt", "extract exactly\n")
    _write(tmp_path / "configs/baseline.yaml", "name: baseline\nrater:\n  prompt_version: v1\n")
    _write(catalog, '{"id":"catalog-1"}\n')
    _write(diagnostic, '{"id":"diagnostic-1"}\n')
    _write(holdout, '{"id":"holdout-1"}\n')
    _write(config, "{}\n")
    _write(tmp_path / "experiments/baseline/v1/metadata.json", "{}\n")
    _write(tmp_path / "pyproject.toml", "[project]\nname='fixture'\nversion='1.0.0'\n")

    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Baseline Test")
    _git(tmp_path, "config", "user.email", "baseline@example.test")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "clean source")

    settings = official_baseline_settings(
        Settings(
            _env_file=None,
            llm={
                "provider": "ollama",
                "model": "qwen2.5:7b",
                "base_url": "http://127.0.0.1:11434/api",
                "temperature": 0.0,
            },
            data={
                "reference_catalog_path": catalog,
                "evaluation_dataset_path": holdout,
                "prompts_dir": tmp_path / "prompts",
                "configs_dir": tmp_path / "configs",
                "artifacts_dir": tmp_path / "artifacts",
            },
        )
    )
    write_baseline_snapshot(
        config,
        settings,
        root=tmp_path,
        diagnostic_path=diagnostic,
        provider_metadata={"model_digest": "pinned-digest"},
    )
    return tmp_path, config, diagnostic, settings


def _validate(frozen_repository: tuple[Path, Path, Path, Settings]) -> None:
    root, config, diagnostic, settings = frozen_repository
    validate_baseline_integrity(
        load_snapshot(config), dataset=diagnostic, settings=settings, root=root
    )


def test_freeze_then_generated_baseline_changes_pass_integrity_preflight(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    root, _config, _diagnostic, _settings = frozen_repository
    _write(root / "experiments/baseline/v1/metadata.json", '{"status":"started"}\n')
    _write(root / "artifacts/baseline/v1/checkpoint.jsonl", "")
    _validate(frozen_repository)


def test_post_freeze_application_change_fails(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    root, _config, _diagnostic, _settings = frozen_repository
    _write(root / "app/verification/service.py", "BEHAVIOR = 'changed'\n")
    with pytest.raises(EvaluationRunError, match=r"service\.py"):
        _validate(frozen_repository)


def test_post_freeze_prompt_change_fails(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    root, _config, _diagnostic, _settings = frozen_repository
    _write(root / "prompts/rater/v1.txt", "changed prompt\n")
    with pytest.raises(EvaluationRunError, match=r"prompts/rater/v1\.txt"):
        _validate(frozen_repository)


def test_post_freeze_diagnostic_change_fails(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    _root, _config, diagnostic, _settings = frozen_repository
    _write(diagnostic, '{"id":"changed"}\n')
    with pytest.raises(EvaluationRunError, match=r"diagnostic\.jsonl"):
        _validate(frozen_repository)


def test_post_freeze_config_tampering_fails(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    _root, config, _diagnostic, _settings = frozen_repository
    value = json.loads(config.read_text(encoding="utf-8"))
    value["temperature"] = 0.5
    config.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvaluationRunError, match="configuration hash"):
        _validate(frozen_repository)


def test_official_settings_bind_baseline_independently_of_application_default() -> None:
    application = Settings(_env_file=None, pipeline_config_file="optimized.yaml")
    baseline = official_baseline_settings(application)

    assert application.pipeline_config_file == "optimized.yaml"
    assert baseline.pipeline_config_file == "baseline.yaml"
    assert baseline.llm.timeout_seconds == BASELINE_LLM_TIMEOUT_SECONDS
    assert baseline.llm.max_concurrency == BASELINE_EVALUATION_CONCURRENCY
    assert baseline.server.batch_concurrency == BASELINE_BATCH_CONCURRENCY
    assert baseline.server.verification_timeout_seconds == BASELINE_VERIFICATION_TIMEOUT_SECONDS


def test_frozen_profile_hash_matches_exact_baseline_yaml(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    root, config, _diagnostic, _settings = frozen_repository
    snapshot = load_snapshot(config)
    assert snapshot["pipeline_profile_name"] == "baseline"
    assert snapshot["pipeline_config_path"] == "configs/baseline.yaml"
    assert snapshot["pipeline_config_sha256"] == file_hash(root / "configs/baseline.yaml")
    assert snapshot["pipeline_effective_config_sha256"] == canonical_hash(
        snapshot["pipeline_config"]
    )


def test_post_freeze_baseline_profile_change_fails(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    root, _config, _diagnostic, _settings = frozen_repository
    _write(root / "configs/baseline.yaml", "name: changed\nrater:\n  prompt_version: v1\n")
    with pytest.raises(EvaluationRunError, match=r"configs/baseline\.yaml"):
        _validate(frozen_repository)


def test_timeout_hierarchy_rejects_outer_deadline_below_provider_budget() -> None:
    settings = Settings(
        _env_file=None,
        server=ServerSettings(verification_timeout_seconds=59),
        llm=LLMSettings(timeout_seconds=60),
    )
    with pytest.raises(RuntimeError, match="must exceed"):
        validate_official_timeout_hierarchy(settings)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("evaluation", "concurrency", 16),
        ("verification", "timeout_seconds", 76.0),
        ("verification", "batch_concurrency", 7),
        ("provider", "total_timeout_seconds", 59.0),
        ("provider", "max_concurrency", 3),
        ("provider", "max_output_tokens", 512),
    ],
)
def test_operational_changes_change_baseline_config_identity(
    frozen_repository: tuple[Path, Path, Path, Settings],
    section: str,
    field: str,
    value: float | int,
) -> None:
    _root, config, _diagnostic, _settings = frozen_repository
    snapshot = load_snapshot(config)
    changed = json.loads(json.dumps(snapshot))
    changed.pop("config_hash")
    changed["operational_config"][section][field] = value
    assert canonical_hash(changed) != snapshot["config_hash"]


def test_operational_snapshot_captures_all_execution_limits(
    frozen_repository: tuple[Path, Path, Path, Settings],
) -> None:
    _root, config, _diagnostic, settings = frozen_repository
    snapshot = load_snapshot(config)
    assert snapshot["operational_config"] == operational_config(settings)
    assert snapshot["operational_config"]["evaluation"] == {"concurrency": 1}
    assert set(snapshot["operational_config"]["verification"]) == {
        "timeout_seconds",
        "batch_concurrency",
        "max_batch_items",
    }
    assert set(snapshot["operational_config"]["provider"]) == {
        "total_timeout_seconds",
        "connect_timeout_seconds",
        "max_retries",
        "retry_base_delay_seconds",
        "max_concurrency",
        "max_connections",
        "max_keepalive_connections",
        "max_output_tokens",
        "schema_version",
    }
