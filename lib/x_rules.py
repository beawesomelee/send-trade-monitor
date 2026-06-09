"""X filtered-stream rule sync helpers for watcher rules."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests


RULES_ENDPOINT = "https://api.x.com/2/tweets/search/stream/rules"
MANAGED_TAG_PREFIX = "send_watcher:"


class XRulesError(RuntimeError):
    """Raised when X rules API calls fail."""


def bearer_token_from_env() -> str:
    """Return the configured X API bearer token, if any."""
    return (
        os.environ.get("X_BEARER_TOKEN")
        or os.environ.get("TWITTER_BEARER_TOKEN")
        or os.environ.get("X_API_BEARER_TOKEN")
        or ""
    ).strip()


def list_rules(bearer_token: str, timeout: int = 20) -> list[dict]:
    """Fetch active filtered-stream rules from X."""
    response = requests.get(
        RULES_ENDPOINT,
        headers=_headers(bearer_token),
        timeout=timeout,
    )
    payload = _parse_response(response)
    return _normalize_rules(payload.get("data") or [])


def add_rules(
    rules: list[dict],
    bearer_token: str,
    *,
    dry_run: bool = False,
    timeout: int = 20,
) -> dict:
    """Add filtered-stream rules to X."""
    if not rules:
        return {"skipped": True, "reason": "no rules to add"}
    response = requests.post(
        RULES_ENDPOINT,
        params={"dry_run": "true"} if dry_run else None,
        json={"add": [_rule_payload(rule) for rule in rules]},
        headers=_headers(bearer_token),
        timeout=timeout,
    )
    return _parse_response(response)


def delete_rules(
    rule_ids: list[str],
    bearer_token: str,
    *,
    dry_run: bool = False,
    timeout: int = 20,
) -> dict:
    """Delete filtered-stream rules from X by rule ID."""
    if not rule_ids:
        return {"skipped": True, "reason": "no rules to delete"}
    response = requests.post(
        RULES_ENDPOINT,
        params={"dry_run": "true"} if dry_run else None,
        json={"delete": {"ids": rule_ids}},
        headers=_headers(bearer_token),
        timeout=timeout,
    )
    return _parse_response(response)


def diff_rules(
    desired_rules: list[dict],
    current_rules: list[dict],
    *,
    managed_tag_prefix: str = MANAGED_TAG_PREFIX,
) -> dict:
    """Return add/delete operations needed to make managed current rules desired."""
    desired = _dedupe_rules(_normalize_rules(desired_rules))
    current = _normalize_rules(current_rules)
    managed_current = [
        rule for rule in current
        if (rule.get("tag") or "").startswith(managed_tag_prefix)
    ]

    desired_keys = {_rule_key(rule) for rule in desired}
    current_keys = {_rule_key(rule) for rule in managed_current}

    to_add = [rule for rule in desired if _rule_key(rule) not in current_keys]
    to_delete = [
        rule for rule in managed_current
        if _rule_key(rule) not in desired_keys
    ]
    to_delete.extend(_duplicate_current_rules(managed_current, desired_keys))

    return {
        "desired": desired,
        "managed_current": managed_current,
        "to_add": to_add,
        "to_delete": to_delete,
        "delete_ids": [rule["id"] for rule in to_delete if rule.get("id")],
    }


def sync_rules(
    desired_rules: list[dict],
    bearer_token: str,
    *,
    apply: bool = False,
    api_dry_run: bool = False,
    timeout: int = 20,
) -> dict:
    """Fetch current rules, diff, and optionally apply the managed-rule sync."""
    current = list_rules(bearer_token, timeout=timeout)
    diff = diff_rules(desired_rules, current)

    delete_response = None
    add_response = None
    if apply:
        delete_response = delete_rules(
            diff["delete_ids"],
            bearer_token,
            dry_run=api_dry_run,
            timeout=timeout,
        )
        add_response = add_rules(
            diff["to_add"],
            bearer_token,
            dry_run=api_dry_run,
            timeout=timeout,
        )

    return {
        "apply": apply,
        "api_dry_run": api_dry_run,
        "current_count": len(current),
        "managed_current_count": len(diff["managed_current"]),
        "desired_count": len(diff["desired"]),
        "delete_count": len(diff["to_delete"]),
        "add_count": len(diff["to_add"]),
        "delete_ids": diff["delete_ids"],
        "to_delete": diff["to_delete"],
        "to_add": diff["to_add"],
        "delete_response": delete_response,
        "add_response": add_response,
    }


def store_rule_snapshot(state_path: Path, rules: list[dict]) -> None:
    """Store the latest managed X rule snapshot in watcher state."""
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except Exception:
        state = {}
    if not isinstance(state, dict):
        state = {}

    state["rules"] = _normalize_rules(rules)
    state["rules_synced_at"] = _now_iso()
    state["updated_at"] = state["rules_synced_at"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _headers(bearer_token: str) -> dict:
    token = (bearer_token or "").strip()
    if not token:
        raise XRulesError("X bearer token is required")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _parse_response(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    if response.status_code < 200 or response.status_code >= 300:
        body = json.dumps(payload)[:1000]
        raise XRulesError(f"X rules API HTTP {response.status_code}: {body}")
    return payload if isinstance(payload, dict) else {"data": payload}


def _normalize_rules(rules: Iterable[dict]) -> list[dict]:
    normalized = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        value = str(rule.get("value") or "").strip()
        tag = str(rule.get("tag") or "").strip()
        if not value:
            continue
        item = {"value": value}
        if tag:
            item["tag"] = tag
        if rule.get("id"):
            item["id"] = str(rule["id"])
        normalized.append(item)
    return normalized


def _dedupe_rules(rules: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for rule in rules:
        key = _rule_key(rule)
        if key in seen:
            continue
        seen.add(key)
        out.append(rule)
    return out


def _duplicate_current_rules(rules: list[dict], desired_keys: set[tuple[str, str]]) -> list[dict]:
    duplicates = []
    kept = set()
    for rule in sorted(rules, key=lambda r: str(r.get("id") or "")):
        key = _rule_key(rule)
        if key not in desired_keys:
            continue
        if key in kept:
            duplicates.append(rule)
        else:
            kept.add(key)
    return duplicates


def _rule_payload(rule: dict) -> dict:
    payload = {"value": rule["value"]}
    if rule.get("tag"):
        payload["tag"] = rule["tag"]
    return payload


def _rule_key(rule: dict) -> tuple[str, str]:
    return (rule.get("value") or "", rule.get("tag") or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
