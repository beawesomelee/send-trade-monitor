"""Diff and optionally sync watcher X filtered-stream rules."""

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.watcher_rules import build_desired_rules, load_watcher_state
from lib.x_rules import (
    MANAGED_TAG_PREFIX,
    XRulesError,
    bearer_token_from_env,
    list_rules,
    store_rule_snapshot,
    sync_rules,
)


def load_env(path: Path) -> None:
    """Load KEY=VALUE entries from a local .env file without overriding env."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync watcher rules to X filtered stream")
    parser.add_argument(
        "--state",
        type=Path,
        default=ROOT / "data" / "watcher.json",
        help="Path to watcher state JSON",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete stale managed rules and add missing rules",
    )
    parser.add_argument(
        "--api-dry-run",
        action="store_true",
        help="Use X API dry_run=true when applying add/delete calls",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout in seconds",
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

    state = load_watcher_state(args.state)
    desired = build_desired_rules(state)

    try:
        result = sync_rules(
            desired,
            token,
            apply=args.apply,
            api_dry_run=args.api_dry_run,
            timeout=args.timeout,
        )
        if args.apply and not args.api_dry_run:
            current = [
                rule for rule in list_rules(token, timeout=args.timeout)
                if (rule.get("tag") or "").startswith(MANAGED_TAG_PREFIX)
            ]
            store_rule_snapshot(args.state, current)
            result["stored_snapshot_count"] = len(current)
    except XRulesError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
