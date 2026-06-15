"""Backfill structured movement events from data/watcher.json."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.movement_events import (
    MOVEMENT_EVENTS_FILE,
    SCHEMA_VERSION,
    build_events_from_watcher_state,
    now_iso,
)
from lib.watcher_rules import load_watcher_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill movement event timing data")
    parser.add_argument(
        "--state",
        type=Path,
        default=ROOT / "data" / "watcher.json",
        help="Path to watcher state JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=MOVEMENT_EVENTS_FILE,
        help="Path to write movement events JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the output file instead of printing a dry-run summary",
    )
    args = parser.parse_args()

    state = load_watcher_state(args.state)
    events = build_events_from_watcher_state(state)
    data = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "source": _display_path(args.state),
        "events": events,
    }

    if args.apply:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(json.dumps({
            "applied": True,
            "output": str(args.output),
            "event_count": len(events),
            "evidence_count": sum(len(event.get("evidence", [])) for event in events),
        }, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "applied": False,
            "output": str(args.output),
            "event_count": len(events),
            "evidence_count": sum(len(event.get("evidence", [])) for event in events),
            "sample_event": events[0] if events else None,
        }, indent=2, sort_keys=True))
    return 0


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
