"""CLI for validating the diagnostic corpus and holdout separation."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.dev_corpus import distribution, validate_dev_bundle
from app.evaluation.validator import load_samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("data/reference/catalog.jsonl"))
    parser.add_argument(
        "--holdout", type=Path, default=Path("data/evaluation/v1/benchmark_500.jsonl")
    )
    args = parser.parse_args()
    manifest, overlap = validate_dev_bundle(
        args.dataset, catalog_path=args.catalog, holdout_path=args.holdout
    )
    samples = load_samples(args.dataset)
    print("diagnostic_validation=PASS")
    print("overlap_validation=PASS")
    print(f"samples={len(samples)}")
    print(f"sha256={manifest.sha256}")
    print(f"verdicts={distribution(samples, 'expected_verdict')}")
    print(f"difficulty={distribution(samples, 'difficulty')}")
    print(f"overlap={overlap.model_dump()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
