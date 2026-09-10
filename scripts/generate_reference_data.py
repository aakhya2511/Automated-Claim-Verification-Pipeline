#!/usr/bin/env python
"""Write the deterministic reference catalog to JSONL.

    python scripts/generate_reference_data.py --out data/reference/catalog.jsonl

Re-running with the same seed reproduces the file byte for byte, so the catalog
can be committed and reviewed like source.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from app.datagen.catalog import (
    CATALOG_SEED,
    DEFAULT_OFFER_COUNT,
    DEFAULT_PLAN_COUNT,
    DEFAULT_PRODUCT_COUNT,
    generate_catalog,
    serialize_catalog,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/reference/catalog.jsonl"))
    parser.add_argument("--seed", type=int, default=CATALOG_SEED)
    parser.add_argument("--products", type=int, default=DEFAULT_PRODUCT_COUNT)
    parser.add_argument("--plans", type=int, default=DEFAULT_PLAN_COUNT)
    parser.add_argument("--offers", type=int, default=DEFAULT_OFFER_COUNT)
    args = parser.parse_args()

    records = generate_catalog(
        seed=args.seed,
        product_count=args.products,
        plan_count=args.plans,
        offer_count=args.offers,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(serialize_catalog(records), encoding="utf-8")

    by_type = Counter(record.entity_type.value for record in records)
    print(f"wrote {len(records)} reference records -> {args.out}")
    for entity_type, count in sorted(by_type.items()):
        print(f"  {entity_type:<10} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
