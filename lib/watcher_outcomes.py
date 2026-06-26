"""Persistent outcome records for verified X watcher hits."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lib.x_time import isoformat_z, tweet_time_from_id


ROOT = Path(__file__).resolve().parent.parent
OUTCOMES_FILE = ROOT / "data" / "watcher_outcomes.json"
SCHEMA_VERSION = "watcher_outcomes_v1"


def record_watcher_outcome(
    payload: dict,
    verification: dict,
    ingested_at: str,
    *,
    path: Path = OUTCOMES_FILE,
    notes: str = "",
) -> dict:
    """Upsert one verified watcher outcome into the outcomes dataset."""
    record = build_watcher_outcome(payload, verification, ingested_at, notes=notes)
    return upsert_watcher_outcome(record, path=path)


def build_watcher_outcome(
    payload: dict,
    verification: dict,
    ingested_at: str,
    *,
    notes: str = "",
) -> dict:
    """Build a normalized watcher outcome record."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    tweet_id = str(data.get("id") or "")
    tweet_at = _tweet_at(data, tweet_id)
    author = _author(payload)
    username = author.get("username") or ""
    tweet_url = f"https://x.com/{username}/status/{tweet_id}" if username and tweet_id else ""
    rules = payload.get("matching_rules") if isinstance(payload.get("matching_rules"), list) else []
    token = verification.get("token") if isinstance(verification.get("token"), dict) else {}
    market = verification.get("market") if isinstance(verification.get("market"), dict) else {}

    return {
        "tweet_id": tweet_id,
        "tweet_url": tweet_url,
        "tweet_at": tweet_at,
        "ingested_at": ingested_at,
        "account": f"@{username}" if username else "",
        "author_name": author.get("name") or "",
        "rule_tags": [str(rule.get("tag")) for rule in rules if isinstance(rule, dict) and rule.get("tag")],
        "matching_rules": rules,
        "token": {
            "symbol": market.get("symbol") or token.get("symbol") or "",
            "address": market.get("address") or token.get("address") or "",
            "chain_slug": market.get("chain_slug") or token.get("chain_slug") or "",
            "dexscreener_url": market.get("dexscreener_url") or token.get("dexscreener_url") or "",
        },
        "direction": verification.get("direction") or "",
        "verification_reason": verification.get("reason") or "",
        "market_at_ingest": market_snapshot(market, checked_at=verification.get("checked_at") or ingested_at),
        "followups": [],
        "label": "candidate_positive",
        "notes": notes,
        "tweet_text": data.get("text") or verification.get("text") or "",
    }


def market_snapshot(market: dict, *, checked_at: str) -> dict:
    """Return compact market fields for outcome snapshots."""
    return {
        "checked_at": checked_at,
        "price_usd": _float_or_none(market.get("price_usd") or market.get("priceUsd")),
        "price_change_h1_pct": _float_or_none(market.get("price_change_h1_pct")),
        "price_change_h6_pct": _float_or_none(market.get("price_change_h6_pct")),
        "price_change_h24_pct": _float_or_none(market.get("price_change_h24_pct")),
        "volume_h24_usd": _float_or_none(market.get("volume_24h_usd")),
        "liquidity_usd": _float_or_none(market.get("liquidity_usd")),
        "market_cap_usd": _float_or_none(market.get("market_cap_usd")),
        "dexscreener_url": market.get("dexscreener_url") or "",
    }


def add_followup(
    tweet_id: str,
    followup: dict,
    *,
    path: Path = OUTCOMES_FILE,
) -> dict:
    """Append or replace a follow-up snapshot for a watcher outcome."""
    data = load_outcomes(path)
    found = False
    for outcome in data["outcomes"]:
        if outcome.get("tweet_id") != str(tweet_id):
            continue
        found = True
        snapshots = outcome.setdefault("followups", [])
        key = followup.get("label") or followup.get("checked_at")
        snapshots[:] = [
            item for item in snapshots
            if (item.get("label") or item.get("checked_at")) != key
        ]
        snapshots.append(followup)
        snapshots.sort(key=lambda item: item.get("checked_at") or "")
        outcome["updated_at"] = now_iso()
        break
    if not found:
        raise ValueError(f"outcome not found for tweet_id={tweet_id}")
    data["updated_at"] = now_iso()
    save_outcomes(data, path)
    return data


def upsert_watcher_outcome(record: dict, *, path: Path = OUTCOMES_FILE) -> dict:
    data = load_outcomes(path)
    outcomes = [
        item for item in data["outcomes"]
        if item.get("tweet_id") != record.get("tweet_id")
    ]
    existing = next(
        (item for item in data["outcomes"] if item.get("tweet_id") == record.get("tweet_id")),
        {},
    )
    merged = {**existing, **record, "updated_at": now_iso()}
    if existing.get("followups") and not record.get("followups"):
        merged["followups"] = existing["followups"]
    outcomes.append(merged)
    outcomes.sort(key=lambda item: item.get("tweet_at") or item.get("ingested_at") or "")
    data["outcomes"] = outcomes
    data["updated_at"] = now_iso()
    save_outcomes(data, path)
    return data


def load_outcomes(path: Path = OUTCOMES_FILE) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "outcomes": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "outcomes": []}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "outcomes": []}
    return {
        "schema_version": data.get("schema_version") or SCHEMA_VERSION,
        "updated_at": data.get("updated_at") or "",
        "outcomes": data.get("outcomes") if isinstance(data.get("outcomes"), list) else [],
    }


def save_outcomes(data: dict, path: Path = OUTCOMES_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _tweet_at(data: dict, tweet_id: str) -> str:
    created_at = str(data.get("created_at") or "")
    if created_at:
        return created_at.replace(".000Z", "Z")
    return isoformat_z(tweet_time_from_id(tweet_id))


def _author(payload: dict) -> dict:
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes.get("users"), list) else []
    author_id = str((payload.get("data") or {}).get("author_id") or "")
    for user in users:
        if isinstance(user, dict) and str(user.get("id") or "") == author_id:
            return user
    return {}


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
