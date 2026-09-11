"""Locate runtime assets in a checkout or an installed wheel.

Hatch places selected immutable assets under ``app/resources`` in wheels.
During source development the same bytes are read from repository paths.
Deployment settings may override every data path explicitly.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2]


def _installed_path(*parts: str) -> Path | None:
    candidate = Path(str(files("app").joinpath("resources", *parts)))
    return candidate if candidate.exists() else None


def runtime_asset(*parts: str, source_path: Path) -> Path:
    """Prefer wheel package data, falling back to the source-checkout asset."""
    return _installed_path(*parts) or SOURCE_ROOT / source_path


DEFAULT_CONFIGS_DIR = runtime_asset("configs", source_path=Path("configs"))
DEFAULT_PROMPTS_DIR = runtime_asset("prompts", source_path=Path("prompts"))
DEFAULT_CATALOG_PATH = runtime_asset(
    "data",
    "reference",
    "catalog.jsonl",
    source_path=Path("data/reference/catalog.jsonl"),
)
