"""CLI for one cumulative Phase 7 development candidate."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.evaluation.phase7 import run_candidate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment-output", type=Path, required=True)
    parser.add_argument("--parent-name", required=True)
    parser.add_argument("--parent-predictions", type=Path, required=True)
    parser.add_argument("--parent-metrics", type=Path, required=True)
    parser.add_argument("--intervention", required=True)
    parser.add_argument("--affected-claim-types", default="")
    parser.add_argument("--rerun-parent-llm-only", action="store_true")
    args = parser.parse_args()
    affected = frozenset(
        value.strip() for value in args.affected_claim_types.split(",") if value.strip()
    )
    summary = asyncio.run(
        run_candidate(
            dataset=Path("data/evaluation/dev/v1/diagnostic.jsonl"),
            config_path=args.config,
            output=args.output,
            experiment_output=args.experiment_output,
            parent_name=args.parent_name,
            parent_predictions=args.parent_predictions,
            parent_metrics_path=args.parent_metrics,
            intervention=args.intervention,
            affected_claim_types=affected or None,
            rerun_parent_llm_only=args.rerun_parent_llm_only,
        )
    )
    print(f"candidate={summary['candidate']}")
    print(f"predictions={summary['prediction_count']}")
    print("status=COMPLETE")


if __name__ == "__main__":
    main()
