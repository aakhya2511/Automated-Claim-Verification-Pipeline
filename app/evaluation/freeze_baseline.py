"""CLI for recording the current baseline without storing credentials."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import RaterError
from app.evaluation.baseline import (
    BaselineFreezeError,
    official_baseline_settings,
    require_clean_freeze_source,
    write_baseline_snapshot,
)
from app.raters.ollama import preflight_ollama


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/baseline/v1/config.json"))
    args = parser.parse_args()
    settings = official_baseline_settings(Settings())
    try:
        require_clean_freeze_source()
    except BaselineFreezeError as exc:
        print(f"baseline_freeze=FAILED: {exc}")
        return 2
    provider_metadata = None
    if settings.llm.provider == "ollama":
        try:
            provider_metadata = asyncio.run(preflight_ollama(settings.llm)).model_dump(mode="json")
        except RaterError as exc:
            print(f"baseline_freeze=FAILED: {exc.message}")
            return 2
    snapshot = write_baseline_snapshot(args.output, settings, provider_metadata=provider_metadata)
    print(f"baseline_config={args.output}")
    print(f"config_hash={snapshot['config_hash']}")
    print(f"provider={snapshot['provider']}")
    print(f"pipeline_config={snapshot['pipeline_config_path']}")
    print(
        "verification_timeout_seconds="
        f"{snapshot['operational_config']['verification']['timeout_seconds']}"
    )
    print(f"evaluation_concurrency={snapshot['operational_config']['evaluation']['concurrency']}")
    print(f"provider_concurrency={snapshot['operational_config']['provider']['max_concurrency']}")
    print(f"git_commit={snapshot['git_commit'] or 'UNAVAILABLE'}")
    print(f"working_tree_clean={snapshot['working_tree_clean']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
