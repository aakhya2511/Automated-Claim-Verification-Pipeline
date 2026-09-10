"""CLI for reproducibly generating and freezing evaluation dataset V1."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.generator import DEFAULT_SAMPLE_COUNT, DEFAULT_SEED, write_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/evaluation/v1/benchmark_500.jsonl"),
    )
    parser.add_argument("--catalog", type=Path, default=Path("data/reference/catalog.jsonl"))
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLE_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    manifest, coverage = write_bundle(
        args.output,
        catalog_path=args.catalog,
        seed=args.seed,
        count=args.samples,
    )
    print(f"wrote {manifest.sample_count} samples -> {args.output}")
    print(f"sha256={manifest.sha256}")
    print(
        f"references={coverage.unique_reference_records}/{coverage.catalog_records} "
        f"({coverage.catalog_coverage_percent:.2f}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
