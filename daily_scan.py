"""
daily_scan.py — main entrypoint for Send.Trade verification monitor.

Modes:
  --backfill  : exhaustive GeckoTerminal scan (one-time; rerun if sheet rebuilt)
  default     : DexScreener incremental — refresh known tokens + discover new
                launches (pairs < 7 days old) via DS profiles + boosts

Steps:
  1. Fetch Send.Trade verified list
  2. Discover candidates (mode-dependent)
  3. Fetch decimals (cached)
  4. Upsert into Google Sheet
  5. Save daily snapshot
  6. Send Telegram summary
"""

import argparse
import datetime as dt
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

from lib.dexscreener import (
    fetch_candidates,
    fetch_new_candidates_ds,
    refresh_addresses_ds,
)
from lib.send_trade import fetch_verified
from lib.decimals import get_decimals
from lib.sheets import load_sheet, upsert
from lib.telegram import send_summary as send_telegram
from lib.discord import send_summary as send_discord

NEW_TOKEN_MAX_AGE_DAYS = 7


def load_config() -> dict:
    return json.loads((SCRIPT_DIR / "config.json").read_text())


def main():
    parser = argparse.ArgumentParser(description="Send.Trade verification monitor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data but don't write to sheet or send Telegram")
    parser.add_argument("--backfill", action="store_true",
                        help="Exhaustive backfill via GeckoTerminal (use for first run)")
    args = parser.parse_args()

    config = load_config()
    today = dt.date.today().isoformat()
    mode = "backfill" if args.backfill else "incremental"
    print(f"=== Send.Trade monitor run: {today} (mode={mode}) ===")

    # 1. fetch Send.Trade verified list
    print("1. fetching Send.Trade verified list...")
    try:
        verified_set = fetch_verified(config["endpoints"]["send_trade_verified"])
        print(f"   {len(verified_set)} verified assets on Send.Trade")
    except Exception as e:
        print(f"   WARNING: could not fetch Send.Trade verified list: {e}")
        verified_set = set()

    # 2. load sheet state (needed for incremental refresh)
    ws = None
    existing_rows = []
    sheet_id = config["google_sheet"]["sheet_id"]
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    if not args.dry_run:
        try:
            print("2a. loading Google Sheet...")
            ws, existing_rows = load_sheet(config)
            print(f"    {len(existing_rows)} existing rows")
        except Exception as e:
            print(f"    WARNING: couldn't load sheet: {e}")
            if not args.backfill:
                print("    sheet unavailable in incremental mode; aborting")
                sys.exit(1)

    # 3. discover candidates
    if args.backfill:
        print("3. backfill: fetching all candidates from GeckoTerminal...")
        candidates = fetch_candidates(config)
    else:
        print(f"3. incremental: refresh known + discover new (<{NEW_TOKEN_MAX_AGE_DAYS}d)...")
        candidates = _incremental_discovery(config, existing_rows)

    print(f"   {len(candidates)} candidates")

    # 4. resolve decimals
    print("4. fetching decimals...")
    candidates = _resolve_decimals(candidates)

    # 5. save snapshot
    _save_snapshot(candidates, today)

    # mark _is_new for telegram summary
    existing_keys = {
        (str(row.get("chain_id", "")), row.get("address", "").lower())
        for row in existing_rows
    }
    for c in candidates:
        key = (str(c["chain_id"]), c["address"].lower())
        c["_is_new"] = key not in existing_keys

    if args.dry_run:
        print("\n[dry-run] skipping sheet update and Telegram")
        stats = {
            "new_pending": sum(1 for c in candidates if c["_is_new"]),
            "still_pending": sum(1 for c in candidates if not c["_is_new"]),
            "auto_verified": 0,
            "dismissed": 0,
        }
        send_telegram(stats, candidates, "https://docs.google.com/spreadsheets/d/DRY_RUN",
                      top_n=config["telegram"].get("top_n", 5), dry_run=True)
        send_discord(stats, candidates, "https://docs.google.com/spreadsheets/d/DRY_RUN",
                     dry_run=True)
        print("done (dry-run)")
        return

    # 6. upsert into sheet (with dismissed-list as sticky skip filter)
    print("5. upserting into sheet...")
    dismissed_set = _load_dismissed()
    stats = upsert(ws, existing_rows, candidates, verified_set, today, dismissed_set)
    _save_dismissed(dismissed_set)
    print(f"   new={stats['new_pending']}, updated={stats['updated']}, "
          f"verified={stats['auto_verified']}, dismissed={stats['dismissed']}")

    # 7. notifications
    print("6. sending Telegram summary...")
    send_telegram(stats, candidates, sheet_url,
                  top_n=config["telegram"].get("top_n", 5))
    print("7. sending Discord summary...")
    send_discord(stats, candidates, sheet_url)

    print("=== done ===")


def _incremental_discovery(config: dict, existing_rows: list) -> list[dict]:
    """Refresh known pending tokens via DS, then discover new tokens (<7d).

    Only chains active in config.chains are processed — disabled chains are
    skipped during both refresh and new-token discovery.
    """
    active_chains = {c["slug"] for c in config["chains"]}
    refresh_addrs = defaultdict(list)
    existing_set = set()
    for row in existing_rows:
        chain = (row.get("chain_name") or "").lower()
        addr = row.get("address", "")
        status = (row.get("status") or "").lower().strip()
        if chain not in active_chains or not addr:
            continue
        existing_set.add((chain, addr.lower() if chain == "base" else addr))
        if status == "pending":
            refresh_addrs[chain].append(addr)

    refreshed = refresh_addresses_ds(dict(refresh_addrs)) if refresh_addrs else []
    new_candidates = fetch_new_candidates_ds(
        config, max_age_days=NEW_TOKEN_MAX_AGE_DAYS, skip_addresses=existing_set
    )

    seen = set()
    out = []
    for c in refreshed + new_candidates:
        k = (c["chain_slug"], c["address"].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(c)

    out.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    return out


def _resolve_decimals(candidates: list[dict]) -> list[dict]:
    """Resolve token decimals via cache → GeckoTerminal token endpoint → on-chain RPC."""
    decimals_cache = _load_decimals_cache()
    need_fetch = []

    for c in candidates:
        cache_key = f"{c['chain_slug']}:{c['address']}"
        if c.get("decimals") not in (None, ""):
            continue
        if cache_key in decimals_cache:
            c["decimals"] = decimals_cache[cache_key]
        else:
            need_fetch.append(c)

    for c in need_fetch:
        dec, img = _fetch_gt_token_info(c["chain_slug"], c["address"])
        if dec is not None:
            c["decimals"] = dec
            decimals_cache[f"{c['chain_slug']}:{c['address']}"] = dec
        if img and not c.get("logo_uri"):
            c["logo_uri"] = img
        time.sleep(2.5)

    for c in [c for c in need_fetch if c.get("decimals", "") == ""]:
        dec = get_decimals(c["address"], c["chain_slug"])
        if dec is not None:
            c["decimals"] = dec
            decimals_cache[f"{c['chain_slug']}:{c['address']}"] = dec
        else:
            c["decimals"] = ""
        time.sleep(0.5)

    _save_decimals_cache(decimals_cache)
    resolved = sum(1 for c in candidates if c.get('decimals') not in (None, ""))
    print(f"   decimals resolved for {resolved}/{len(candidates)} tokens")
    return candidates


def _fetch_gt_token_info(chain_slug: str, address: str) -> tuple:
    """Fetch decimals + image from GeckoTerminal token endpoint."""
    import requests
    import os
    network = {"base": "base", "solana": "solana"}.get(chain_slug, chain_slug)
    key = os.environ.get("GECKOTERMINAL_API_KEY") or os.environ.get("COINGECKO_API_KEY")
    if key:
        url = f"https://pro-api.coingecko.com/api/v3/onchain/networks/{network}/tokens/{address}"
        headers = {"x-cg-pro-api-key": key}
    else:
        url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}"
        headers = {}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        attrs = r.json().get("data", {}).get("attributes", {})
        dec = attrs.get("decimals")
        img = attrs.get("image_url", "")
        return (int(dec) if dec is not None else None, img)
    except Exception:
        return (None, "")


def _load_dismissed() -> set:
    """Load the persistent set of (chain_slug, address_lower) dismissed by Austin."""
    path = SCRIPT_DIR / "data" / "dismissed.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
        return {(item["chain_slug"], item["address"].lower()) for item in data}
    except Exception:
        return set()


def _save_dismissed(dismissed: set):
    """Persist the dismissed set as JSON."""
    path = SCRIPT_DIR / "data" / "dismissed.json"
    items = sorted([{"chain_slug": cs, "address": addr} for cs, addr in dismissed],
                   key=lambda x: (x["chain_slug"], x["address"]))
    path.write_text(json.dumps(items, indent=2))


def _load_decimals_cache() -> dict:
    cache_path = SCRIPT_DIR / "data" / "decimals_cache.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_decimals_cache(cache: dict):
    cache_path = SCRIPT_DIR / "data" / "decimals_cache.json"
    cache_path.write_text(json.dumps(cache, indent=2))


def _save_snapshot(candidates: list[dict], today: str):
    snapshot_dir = SCRIPT_DIR / "data" / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{today}.json"
    clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]
    snapshot_path.write_text(json.dumps(clean, indent=2))
    print(f"   snapshot saved to {snapshot_path.name}")


if __name__ == "__main__":
    main()
