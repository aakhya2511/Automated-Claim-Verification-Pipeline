"""CLI for generating the non-holdout development diagnostic corpus."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.dev_corpus import DEV_SAMPLE_COUNT, DEV_SEED, write_dev_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("data/evaluation/dev/v1/diagnostic.jsonl")
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/reference/catalog.jsonl"))
    parser.add_argument(
        "--holdout", type=Path, default=Path("data/evaluation/v1/benchmark_500.jsonl")
    )
    parser.add_argument("--samples", type=int, default=DEV_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DEV_SEED)
    args = parser.parse_args()
    manifest, overlap = write_dev_bundle(
        args.output,
        catalog_path=args.catalog,
        holdout_path=args.holdout,
        seed=args.seed,
        count=args.samples,
    )
    print(f"wrote {manifest.sample_count} diagnostic samples -> {args.output}")
    print(f"sha256={manifest.sha256}")
    print(f"overlap_clear={overlap.is_clear}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
