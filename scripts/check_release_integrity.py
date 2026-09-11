"""Check frozen public identities without executing any evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "data/evaluation/v1/benchmark_500.jsonl": (
        "0868111bf882b702738fc82720c2ccf51bbe2776645740a7fc20a93f724af9c7"
    ),
    "configs/phase7/optimized_final.yaml": (
        "a53d8c6558b11f3f8094e3288ce3e2e5169b8baae5cc2b37367e117b781314b5"
    ),
    "prompts/rater/v1.txt": ("2cf1068ba424f99a42f3e8defe640c2cf847d5a284f5dd8a39a912b50371c6b4"),
    "prompts/extractor/v1.txt": (
        "560b48153dc8a5f96043f7c6ac0787ce13771bca732c80d13614782166cfc289"
    ),
    "data/reference/catalog.jsonl": (
        "dab11bd68087b4771b85ac301aeea601fd539c456cc371140f60d7247c197def"
    ),
}


def main() -> int:
    for relative, expected in EXPECTED.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"frozen identity changed: {relative}")
    finality = json.loads(
        (ROOT / "artifacts/phase8/holdout/finality.json").read_text(encoding="utf-8")
    )
    if finality.get("holdout_evaluated") is not True:
        raise RuntimeError("holdout finality is not recorded")
    if finality.get("holdout_dataset_sha256") != EXPECTED["data/evaluation/v1/benchmark_500.jsonl"]:
        raise RuntimeError("holdout finality hash differs")
    print("release_integrity=PASS holdout_evaluated=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
