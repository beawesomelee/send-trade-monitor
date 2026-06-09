"""Print desired X filtered-stream rules from data/watcher.json."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.watcher_rules import build_desired_rules, load_watcher_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run watcher X rules")
    parser.add_argument(
        "--state",
        type=Path,
        default=ROOT / "data" / "watcher.json",
        help="Path to watcher state JSON",
    )
    args = parser.parse_args()

    state = load_watcher_state(args.state)
    rules = build_desired_rules(state)

    print(json.dumps({
        "state": str(args.state),
        "rule_count": len(rules),
        "rules": rules,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
