"""CLI for recording the current baseline without storing credentials."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.baseline import write_baseline_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("experiments/baseline/v1/config.json"))
    args = parser.parse_args()
    snapshot = write_baseline_snapshot(args.output)
    print(f"baseline_config={args.output}")
    print(f"config_hash={snapshot['config_hash']}")
    print(f"provider={snapshot['provider']}")
    print(f"git_commit={snapshot['git_commit'] or 'UNAVAILABLE'}")
    print(f"working_tree_clean={snapshot['working_tree_clean']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
