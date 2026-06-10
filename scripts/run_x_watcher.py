"""Run the X filtered-stream watcher and store matching posts."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.x_rules import XRulesError, bearer_token_from_env
from lib.x_watcher import LOCK_FILE, STATE_FILE, TWEETS_FILE, stream_watcher_posts
from scripts.sync_watcher_rules import load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Run X filtered-stream watcher")
    parser.add_argument(
        "--max-posts",
        type=int,
        default=10,
        help="Stop after storing this many new posts",
    )
    parser.add_argument(
        "--max-seconds",
        type=int,
        default=300,
        help="Stop after this many seconds",
    )
    parser.add_argument(
        "--tweets-file",
        type=Path,
        default=TWEETS_FILE,
        help="JSONL file for raw matching post payloads",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=STATE_FILE,
        help="JSON state file for ingest dedupe/checkpoints",
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=LOCK_FILE,
        help="Lock file preventing multiple simultaneous streams for this app",
    )
    parser.add_argument(
        "--read-timeout",
        type=int,
        default=30,
        help="Stream read timeout in seconds before reconnecting",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Store matching posts without posting raw watcher hits to Discord",
    )
    parser.add_argument(
        "--discord-dry-run",
        action="store_true",
        help="Print raw watcher Discord messages instead of posting them",
    )
    args = parser.parse_args()

    load_env(ROOT / ".env")
    token = bearer_token_from_env()
    if not token:
        print(
            "Missing X bearer token. Set X_BEARER_TOKEN, TWITTER_BEARER_TOKEN, "
            "or X_API_BEARER_TOKEN.",
            file=sys.stderr,
        )
        return 2

    try:
        result = stream_watcher_posts(
            token,
            max_posts=args.max_posts,
            max_seconds=args.max_seconds,
            tweets_path=args.tweets_file,
            state_path=args.state_file,
            lock_path=args.lock_file,
            read_timeout=args.read_timeout,
            discord=not args.no_discord,
            discord_dry_run=args.discord_dry_run,
        )
    except KeyboardInterrupt:
        print("Watcher stopped")
        return 130
    except XRulesError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
