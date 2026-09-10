from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from app.core.config import Settings
from app.evaluation.baseline import write_baseline_snapshot
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
    _write(tmp_path / "configs/optimized.yaml", "name: baseline\nrater:\n  prompt_version: v1\n")
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

    settings = Settings(
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
