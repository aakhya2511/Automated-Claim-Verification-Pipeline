"""Freeze a redacted, hash-stable baseline configuration and source identity."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import REPO_ROOT, Settings
from app.core.pipeline_config import PipelineConfig, load_pipeline_config

BASELINE_VERSION = "v1"
SOURCE_PATTERNS = ("*.py", "*.yaml", "*.txt", "pyproject.toml", "Makefile")


def canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_tree_hash(root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    files: set[Path] = set()
    for pattern in SOURCE_PATTERNS:
        files.update(root.glob(pattern))
        files.update((root / "app").rglob(pattern))
        files.update((root / "configs").rglob(pattern))
        files.update((root / "prompts").rglob(pattern))
    for path in sorted(path for path in files if path.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def git_identity(root: Path = REPO_ROOT) -> tuple[str | None, str, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None, "git repository unavailable", None
    return commit, status if status else "clean", not bool(status)


def build_baseline_snapshot(
    settings: Settings,
    pipeline: PipelineConfig,
    *,
    root: Path = REPO_ROOT,
    provider_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog_bytes = settings.data.reference_catalog_path.read_bytes()
    commit, status, clean = git_identity(root)
    snapshot: dict[str, Any] = {
        "baseline_version": BASELINE_VERSION,
        "git_commit": commit,
        "working_tree_status": status,
        "working_tree_clean": clean,
        "source_tree_sha256": source_tree_hash(root),
        "provider": settings.llm.provider,
        "provider_base_url": settings.llm.base_url,
        "model": settings.llm.model,
        "temperature": settings.llm.temperature,
        "rater_prompt_version": pipeline.rater.prompt_version,
        "rater_prompt_sha256": _prompt_hash(
            settings.data.prompts_dir, "rater", pipeline.rater.prompt_version
        ),
        "extraction_prompt_version": settings.llm.extraction_prompt_version,
        "extraction_prompt_sha256": _prompt_hash(
            settings.data.prompts_dir, "extractor", settings.llm.extraction_prompt_version
        ),
        "request_schema_version": pipeline.rater.schema_version,
        "retry": {
            "timeout_seconds": settings.llm.timeout_seconds,
            "connect_timeout_seconds": settings.llm.connect_timeout_seconds,
            "max_retries": settings.llm.max_retries,
            "retry_base_delay_seconds": settings.llm.retry_base_delay_seconds,
        },
        "catalog_fingerprint": hashlib.sha256(catalog_bytes).hexdigest(),
        "pipeline_config_file": settings.pipeline_config_file,
        "pipeline_config": pipeline.model_dump(mode="json"),
        "provider_metadata": provider_metadata,
    }
    snapshot["config_hash"] = canonical_hash(snapshot)
    return snapshot


def write_baseline_snapshot(
    output: Path,
    settings: Settings | None = None,
    *,
    provider_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = settings or Settings()
    pipeline = load_pipeline_config(resolved.pipeline_config_path)
    snapshot = build_baseline_snapshot(resolved, pipeline, provider_metadata=provider_metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return snapshot


def _prompt_hash(root: Path, kind: str, version: str) -> str:
    return hashlib.sha256((root / kind / f"{version}.txt").read_bytes()).hexdigest()
