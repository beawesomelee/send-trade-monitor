"""
movement_scan.py — Base + Solana ecosystem hourly movement scanner.

Detects tokens with significant 6-hour price movement (pumps ≥ +50%, dumps ≤ -50%)
that meet MC + liquidity + 24h-volume floors AND have a logo set.

Manual phase (current): outputs detected movers + a research prompt for each.
The operator reviews and decides which to alert via --alert flag.

Usage:
    python movement_scan.py             # detect + print, no alerts sent
    python movement_scan.py --alert     # also send to Discord and record cooldown
"""

import argparse
import json
import sys
import time
import urllib.parse
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR / ".env")

from lib.movement import (
    find_movers,
    enrich_movers,
    filter_by_cooldown,
    record_alerts,
)
from lib.send_trade_lore import push_lore
from lib.lore import fetch_lore_packet
from lib.watcher_state import record_signals


def main():
    parser = argparse.ArgumentParser(description="Base movement scanner")
    parser.add_argument("--alert", action="store_true",
                        help="Actually send to Discord and record cooldown")
    parser.add_argument("--no-cooldown", action="store_true",
                        help="Ignore cooldown filter (for testing)")
    parser.add_argument("--h1", action="store_true",
                        help="Use the 1h window instead of the default 6h (testing)")
    args = parser.parse_args()

    config = json.loads((SCRIPT_DIR / "config.json").read_text())
    today = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    print(f"=== Movement scan: {today} ===")

    # h6 is the primary detection window — sustained 6h moves. The 6h rolling
    # window decays slowly, so an hourly cron samples it ~6x per move (no need
    # for the old 15-min cadence the 1h window required). Pass --h1 to test
    # the faster window manually.
    window = "h1" if args.h1 else "h6"
    print(f"1. fetching GT pools for {window} movers...")
    movers = find_movers(config, window=window)
    print(f"   {len(movers)} unique tokens passed MC + liq + vol + {window}-change filters")

    if not movers:
        print("\nno movers this run")
        return

    print("2. enriching with DS data + logo filter...")
    movers = enrich_movers(movers)
    print(f"   {len(movers)} after logo filter")

    if not movers:
        print("\nno movers had a logo set")
        return

    cooldown_hr = config["movement"]["cooldown_hours"]
    if args.no_cooldown:
        print("3. (skipping cooldown check)")
        retained = []
        new_movers = movers
    else:
        print(f"3. applying {cooldown_hr}h cooldown filter...")
        new_movers, retained = filter_by_cooldown(movers, cooldown_hr)
        print(f"   {len(new_movers)} fresh alerts (rest were in cooldown)")

    if not new_movers:
        print("\nall movers within cooldown window")
        return

    # Best-effort lore enrichment via Grok + live X search. Silently skipped
    # if XAI_API_KEY isn't set (returns "").
    print("4. fetching lore for each mover (Grok + live X search)...")
    for m in new_movers:
        m["lore_packet"] = fetch_lore_packet(m)
        m["lore"] = m["lore_packet"].get("lore", "")
        if m["lore"]:
            print(f"   {m.get('symbol')}: lore captured ({len(m['lore'])} chars)")
        else:
            print(f"   {m.get('symbol')}: no lore returned")

    _print_movers(new_movers)

    if args.alert:
        # Push lore to Send.Trade FIRST so Discord alerts can include the
        # resulting lore ID (Austin uses it to delete from Send.Trade admin
        # if the auto-posted lore turns out irrelevant).
        print("\n5. pushing lore to Send.Trade...")
        for m in new_movers:
            resp = push_lore(m)
            m["send_trade_lore"] = resp
            sym = m.get("symbol")
            if resp:
                lid = resp.get("id") or resp.get("_id") or "(no id in response)"
                print(f"   {sym}: posted, lore id={lid}")
            else:
                print(f"   {sym}: skipped or failed")

        print("6. sending Discord alerts...")
        _send_alerts(new_movers, config)
        signals = record_signals(new_movers)
        print(f"   recorded {len(signals)} watcher signals")
        record_alerts(new_movers, retained)
        print("   recorded cooldown state")
    else:
        print("\n(use --alert to send notifications, push send.trade lore, and lock cooldown)")


def _print_movers(movers: list[dict]):
    print()
    print(f"=== {len(movers)} movers ===")
    for m in movers:
        emoji = "🚀" if m["direction"] == "pump" else "🔻"
        sym = m.get("symbol") or "?"
        change = m["price_change_pct"]
        window = m["price_change_window"]
        win_label = window[1:] + "h"
        mc = m["market_cap_usd"]
        liq = m["liquidity_usd"]
        vol = m[f"volume_{window}_usd"]
        print()
        print(f"  {emoji} {sym} {change:+.1f}% in {win_label}  ({m['chain_slug']})")
        print(f"     MC ${mc/1e6:.2f}M · liq ${liq/1e3:.0f}K · {win_label} vol ${vol/1e3:.0f}K")
        print(f"     also: h1={m['price_change_h1_pct']:+.1f}%, h6={m['price_change_h6_pct']:+.1f}%")
        print(f"     {m['dexscreener_url']}")
        if m.get("lore"):
            print(f"     lore: {m['lore']}")
        # research prompts for manual triage (also useful when lore is missing)
        sym_enc = urllib.parse.quote(sym)
        print(f"     X search:   https://twitter.com/search?q=${sym_enc}&src=typed_query&f=live")
        print(f"     Web search: https://www.google.com/search?q=${sym_enc}+crypto+token+base&tbs=qdr:d")


def _send_alerts(movers: list[dict], config: dict):
    sheet_id = config["google_sheet"]["sheet_id"]
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    from lib.discord import send_movement_alert as send_dc

    send_dc(movers, sheet_url)


if __name__ == "__main__":
    main()
