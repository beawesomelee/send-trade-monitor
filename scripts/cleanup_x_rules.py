"""Safely clean X filtered-stream rules."""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sync_watcher_rules import load_env
from lib.x_rules import (
    MANAGED_TAG_PREFIX,
    XRulesError,
    bearer_token_from_env,
    delete_rules,
    list_rules,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean X filtered-stream rules")
    parser.add_argument(
        "--managed-stale",
        action="store_true",
        help="Delete all current rules tagged send_watcher:*",
    )
    parser.add_argument(
        "--rule-id",
        action="append",
        default=[],
        help="Explicit unmanaged/current rule ID to delete. Repeat for multiple IDs.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rules. Without this, uses X API dry_run=true.",
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

    if not args.managed_stale and not args.rule_id:
        print(
            "Choose --managed-stale and/or at least one --rule-id. "
            "This command never deletes every rule implicitly.",
            file=sys.stderr,
        )
        return 2

    try:
        current = list_rules(token, timeout=args.timeout)
        managed_ids = [
            rule["id"]
            for rule in current
            if rule.get("id") and (rule.get("tag") or "").startswith(MANAGED_TAG_PREFIX)
        ] if args.managed_stale else []
        explicit_ids = [str(rule_id).strip() for rule_id in args.rule_id if str(rule_id).strip()]
        delete_ids = sorted(set(managed_ids + explicit_ids))

        response = delete_rules(
            delete_ids,
            token,
            dry_run=not args.apply,
            timeout=args.timeout,
        )
    except XRulesError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps({
        "apply": args.apply,
        "api_dry_run": not args.apply,
        "current_count": len(current),
        "managed_stale_requested": args.managed_stale,
        "managed_delete_ids": managed_ids,
        "explicit_delete_ids": explicit_ids,
        "delete_ids": delete_ids,
        "delete_count": len(delete_ids),
        "delete_response": response,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
