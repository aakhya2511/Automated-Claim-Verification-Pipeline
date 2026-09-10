"""CLI that validates a frozen evaluation dataset and prints derived statistics."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.validator import DatasetValidationError, validate_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--catalog", type=Path, default=Path("data/reference/catalog.jsonl"))
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        summary = validate_bundle(
            args.dataset,
            catalog_path=args.catalog,
            manifest_path=args.manifest,
        )
    except (DatasetValidationError, OSError, ValueError) as exc:
        print(f"validation=FAIL: {exc}")
        return 1
    print("validation=PASS")
    print(f"samples={summary.samples}")
    print(f"verdicts={summary.verdicts}")
    print(f"claim_types={summary.claim_types}")
    print(f"mutations={summary.mutations}")
    print(f"difficulty={summary.difficulties}")
    print(
        f"unique_references={summary.coverage.unique_reference_records}/"
        f"{summary.coverage.catalog_records}"
    )
    print(
        "duplicates="
        f"{summary.coverage.duplicate_claims}/"
        f"{summary.coverage.duplicate_claim_reference_pairs}/"
        f"{summary.coverage.duplicate_semantic_mutations}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
