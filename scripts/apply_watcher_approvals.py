"""Approve/reject watcher candidates using tweet timing versus movement start."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.movement_events import MOVEMENT_EVENTS_FILE
from lib.watcher_approval import (
    DEFAULT_EARLY_AFTER_START_MINUTES,
    DEFAULT_MAX_PRE_START_LEAD_MINUTES,
    apply_watcher_approvals,
)
from lib.watcher_state import WATCHER_FILE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Label watcher candidates and update watcher approval statuses",
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=MOVEMENT_EVENTS_FILE,
        help="Path to movement events JSON",
    )
    parser.add_argument(
        "--watcher",
        type=Path,
        default=WATCHER_FILE,
        help="Path to watcher state JSON",
    )
    parser.add_argument(
        "--early-after-start-minutes",
        type=float,
        default=DEFAULT_EARLY_AFTER_START_MINUTES,
        help="Approve movement chatter this many minutes after estimated move start",
    )
    parser.add_argument(
        "--max-pre-start-lead-minutes",
        type=float,
        default=DEFAULT_MAX_PRE_START_LEAD_MINUTES,
        help="Reject pre-move tweets older than this freshness window",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write labels/statuses to the events and watcher files",
    )
    args = parser.parse_args()

    result = apply_watcher_approvals(
        events_path=args.events,
        watcher_path=args.watcher,
        early_after_start_minutes=args.early_after_start_minutes,
        max_pre_start_lead_minutes=args.max_pre_start_lead_minutes,
        apply=args.apply,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
