"""CLI for the official real-provider development baseline."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.evaluation.runner import EvaluationRunError, run_evaluation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("data/evaluation/dev/v1/diagnostic.jsonl")
    )
    parser.add_argument("--config", type=Path, default=Path("experiments/baseline/v1/config.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/baseline/v1"))
    args = parser.parse_args()
    try:
        summary = asyncio.run(
            run_evaluation(dataset=args.dataset, config_path=args.config, output=args.output)
        )
    except EvaluationRunError as exc:
        print(f"baseline_evaluation=NOT_COMPLETE: {exc}")
        return 2
    print("baseline_evaluation=COMPLETE")
    print(f"predictions={summary['prediction_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
