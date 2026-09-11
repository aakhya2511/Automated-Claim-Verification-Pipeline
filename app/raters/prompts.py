"""Versioned prompt loading with path traversal protection."""

from __future__ import annotations

from pathlib import Path

from app.core.exceptions import ConfigurationError
from app.core.resources import DEFAULT_PROMPTS_DIR

PROMPTS_ROOT = DEFAULT_PROMPTS_DIR


def load_prompt(kind: str, version: str, *, root: Path = PROMPTS_ROOT) -> str:
    if not kind.replace("_", "").isalnum() or not version.replace("_", "").isalnum():
        raise ConfigurationError("prompt kind and version must be simple identifiers")
    path = root / kind / f"{version}.txt"
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigurationError(f"prompt not available: {kind}/{version}") from exc
    if not prompt:
        raise ConfigurationError(f"prompt is empty: {kind}/{version}")
    return prompt
