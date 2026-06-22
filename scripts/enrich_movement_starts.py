"""Estimate movement start times for data/movement_events.json."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(ROOT / ".env")

from lib.movement_events import MOVEMENT_EVENTS_FILE, load_events, now_iso
from lib.movement_start import DEFAULT_THRESHOLD_PCT, enrich_events_with_estimated_starts


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich movement events with OHLCV start estimates")
    parser.add_argument(
        "--input",
        type=Path,
        default=MOVEMENT_EVENTS_FILE,
        help="Path to movement events JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=MOVEMENT_EVENTS_FILE,
        help="Path to write enriched movement events JSON",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=DEFAULT_THRESHOLD_PCT,
        help="Percent move from local extreme used as start threshold",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the enriched dataset",
    )
    args = parser.parse_args()

    data = load_events(args.input)
    events, stats = enrich_events_with_estimated_starts(
        data["events"],
        threshold_pct=args.threshold_pct,
    )

    out = {
        **data,
        "events": events,
        "updated_at": now_iso(),
        "movement_start_enrichment": {
            "threshold_pct": args.threshold_pct,
            "method": "ohlcv_local_extreme_threshold",
            "stats": stats,
        },
    }

    if args.apply:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")

    print(json.dumps({
        "applied": args.apply,
        "input": str(args.input),
        "output": str(args.output),
        **stats,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
