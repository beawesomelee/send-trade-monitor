"""Import Send.Trade verified token X accounts into watcher state."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.dexscreener import DS_BATCH, DS_DELAY, DS_TOKENS, _norm


CHAIN_BY_ID = {
    8453: "base",
    501474: "solana",
}

EXCLUDED_SYMBOLS = {
    "aave",
    "bio",
    "btc",
    "cbbtc",
    "cbada",
    "cbeth",
    "cbltc",
    "cbxrp",
    "eurc",
    "frxusd",
    "icp",
    "jito",
    "jto",
    "lbtc",
    "link",
    "mseth",
    "pyusd",
    "usdc",
    "usdt",
    "w",
    "wbtc",
    "weth",
    "xcn",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Import verified Send token official X accounts")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--state", type=Path, default=ROOT / "data" / "watcher.json")
    parser.add_argument("--include-majors", action="store_true", help="Do not skip known majors/stables/wrapped assets")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing watcher state")
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    assets = fetch_send_verified_assets(config["endpoints"]["send_trade_verified"])
    enriched = enrich_assets_with_x_handles(assets)
    rows = [
        row for row in enriched
        if args.include_majors or not is_excluded_asset(row)
    ]

    state = load_state(args.state)
    result = merge_official_watchers(state, rows)
    output = {
        "verified_assets": len(assets),
        "with_x_handle": len(enriched),
        "importable": len(rows),
        **result,
    }

    if not args.dry_run:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def fetch_send_verified_assets(endpoint: str) -> list[dict]:
    response = requests.get(endpoint, timeout=20)
    response.raise_for_status()
    payload = response.json()
    assets = payload if isinstance(payload, list) else payload.get("assets", payload.get("data", []))
    return [asset for asset in assets if isinstance(asset, dict)]


def enrich_assets_with_x_handles(assets: list[dict]) -> list[dict]:
    assets_by_chain = defaultdict(list)
    indexed = {}
    for asset in assets:
        chain_slug = CHAIN_BY_ID.get(asset.get("chainId"))
        address = asset.get("address") or ""
        if not chain_slug or not address:
            continue
        assets_by_chain[chain_slug].append(address)
        indexed[(chain_slug, _norm(address, chain_slug))] = asset

    rows = []
    for chain_slug, addresses in sorted(assets_by_chain.items()):
        for i in range(0, len(addresses), DS_BATCH):
            batch = addresses[i:i + DS_BATCH]
            response = requests.get(
                DS_TOKENS.format(chain=chain_slug, addresses=",".join(batch)),
                timeout=20,
            )
            response.raise_for_status()
            pairs = response.json()
            seen = set()
            for pair in pairs if isinstance(pairs, list) else []:
                base_token = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
                address = base_token.get("address") or ""
                key = (chain_slug, _norm(address, chain_slug))
                if key in seen:
                    continue
                seen.add(key)
                asset = indexed.get(key)
                if not asset:
                    continue
                handle = x_handle_from_pair(pair)
                if not handle:
                    continue
                rows.append({
                    "account": normalize_handle(handle),
                    "symbol": asset.get("symbol") or base_token.get("symbol") or "",
                    "name": asset.get("name") or base_token.get("name") or "",
                    "address": asset.get("address") or address,
                    "chain_slug": chain_slug,
                    "chain_id": asset.get("chainId"),
                    "dexscreener_url": f"https://dexscreener.com/{chain_slug}/{asset.get('address') or address}",
                })
            time.sleep(DS_DELAY)
    return rows


def x_handle_from_pair(pair: dict) -> str:
    info = pair.get("info") if isinstance(pair.get("info"), dict) else {}
    socials = info.get("socials") if isinstance(info.get("socials"), list) else []
    for social in socials:
        if not isinstance(social, dict):
            continue
        if (social.get("type") or "").lower() not in {"twitter", "x"}:
            continue
        value = social.get("url") or social.get("handle") or ""
        if value:
            return value.rstrip("/").rsplit("/", 1)[-1]
    return ""


def merge_official_watchers(state: dict, rows: list[dict]) -> dict:
    watch_accounts = state.setdefault("watch_accounts", {})
    now = now_iso()
    imported_accounts = {row.get("account") for row in rows if row.get("account")}
    added = 0
    updated = 0
    skipped = 0
    pruned = 0

    for row in rows:
        account = row.get("account") or ""
        if not account:
            skipped += 1
            continue
        existing = watch_accounts.get(account)
        if not isinstance(existing, dict):
            existing = {}
            added += 1
        else:
            updated += 1

        symbol = clean_term(row.get("symbol"))
        terms = [term for term in [symbol, f"${symbol}" if symbol else ""] if term]
        watch_accounts[account] = {
            **existing,
            "account_type": "official_token_account",
            "rule_mode": "account_only",
            "status": "approved",
            "source": "send_verified_dexscreener_socials",
            "terms": terms,
            "token": {
                "symbol": row.get("symbol") or "",
                "name": row.get("name") or "",
                "address": row.get("address") or "",
                "chain_slug": row.get("chain_slug") or "",
                "dexscreener_url": row.get("dexscreener_url") or "",
            },
            "verified_source_updated_at": now,
        }

    for account, config in list(watch_accounts.items()):
        if not isinstance(config, dict):
            continue
        if config.get("source") != "send_verified_dexscreener_socials":
            continue
        if config.get("account_type") != "official_token_account":
            continue
        if account not in imported_accounts:
            del watch_accounts[account]
            pruned += 1

    state["updated_at"] = now
    return {
        "added": added,
        "updated": updated,
        "pruned": pruned,
        "skipped": skipped,
        "official_accounts_total": sum(
            1 for item in watch_accounts.values()
            if isinstance(item, dict) and item.get("account_type") == "official_token_account"
        ),
    }


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": "watcher_state_v1", "updated_at": "", "signals": [], "watch_accounts": {}, "rules": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"schema_version": "watcher_state_v1", "updated_at": "", "signals": [], "watch_accounts": {}, "rules": []}
    return data if isinstance(data, dict) else {"schema_version": "watcher_state_v1", "updated_at": "", "signals": [], "watch_accounts": {}, "rules": []}


def is_excluded_asset(row: dict) -> bool:
    symbol = clean_term(row.get("symbol"))
    name = clean_term(row.get("name"))
    if symbol in EXCLUDED_SYMBOLS:
        return True
    return any(term in name for term in ["wrapped", "usd", "tether", "paypal", "coinbase wrapped"])


def normalize_handle(value) -> str:
    handle = str(value or "").strip().lstrip("@")
    handle = "".join(c for c in handle if c.isalnum() or c == "_")
    if not handle or len(handle) > 15:
        return ""
    return f"@{handle.lower()}" if handle else ""


def clean_term(value) -> str:
    return str(value or "").strip().lower()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
