"""Review pre-move watcher candidates before approving X stream rules."""

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

from lib.movement_events import MOVEMENT_EVENTS_FILE
from lib.watcher_review import (
    WATCHER_REVIEW_FILE,
    build_pre_move_candidates,
    classify_candidates_with_grok,
    review_payload,
    write_review,
)
from lib.watcher_rules import WATCHER_FILE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List and classify pre-move watcher candidates"
    )
    parser.add_argument(
        "--events",
        type=Path,
        default=MOVEMENT_EVENTS_FILE,
        help="Path to data/movement_events.json",
    )
    parser.add_argument(
        "--watcher",
        type=Path,
        default=WATCHER_FILE,
        help="Path to data/watcher.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=WATCHER_REVIEW_FILE,
        help="Path for --write output",
    )
    parser.add_argument(
        "--grok",
        action="store_true",
        help="Classify pre-move candidates with Grok using XAI_API_KEY",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write JSON review output instead of only printing it",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit candidate count, useful before --grok",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Grok HTTP timeout in seconds",
    )
    parser.add_argument(
        "--max-lookback-hours",
        type=float,
        default=24.0,
        help="Only include tweets this many hours before the movement cutoff. Use 0 to disable.",
    )
    args = parser.parse_args()

    candidates = build_pre_move_candidates(
        events_path=args.events,
        watcher_path=args.watcher,
        max_lookback_hours=args.max_lookback_hours,
    )
    if args.limit and args.limit > 0:
        candidates = candidates[:args.limit]

    if args.grok:
        candidates = classify_candidates_with_grok(candidates, timeout=args.timeout)

    if args.write:
        write_review(candidates, path=args.output)

    if args.format == "json":
        print(json.dumps(review_payload(candidates), indent=2, sort_keys=True))
    else:
        print_table(candidates, grok=args.grok)
        if args.write:
            print(f"\nwrote {args.output}")

    return 0


def print_table(candidates: list[dict], *, grok: bool = False) -> None:
    print(f"pre_move_candidates={len(candidates)}")
    if not candidates:
        return

    headers = [
        "candidate",
        "account",
        "token",
        "mins_pre",
        "cutoff",
        "terms",
    ]
    if grok:
        headers.extend(["label", "direction", "recommendation"])

    rows = []
    for candidate in candidates:
        terms = ", ".join(candidate.get("terms") or [])
        row = [
            candidate.get("candidate_id", "").replace("watch_candidate_", ""),
            candidate.get("account", ""),
            candidate.get("token_symbol", ""),
            str(candidate.get("minutes_before_cutoff", "")),
            candidate.get("pre_move_cutoff_source", ""),
            truncate(terms, 46),
        ]
        if grok:
            classification = candidate.get("grok") or {}
            row.extend([
                classification.get("label", ""),
                classification.get("implied_direction", ""),
                candidate.get("recommendation", ""),
            ])
        rows.append(row)

    widths = [
        min(max(len(str(row[idx])) for row in [headers] + rows), 48)
        for idx in range(len(headers))
    ]
    print(format_row(headers, widths))
    print(format_row(["-" * width for width in widths], widths))
    for row in rows:
        print(format_row(row, widths))


def format_row(values: list[str], widths: list[int]) -> str:
    return "  ".join(
        truncate(str(value), widths[idx]).ljust(widths[idx])
        for idx, value in enumerate(values)
    )


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
