"""Manual smoke test for Grok lore packets.

This avoids GT/DS scans and Discord. It only checks whether Grok can return
structured lore, source references, and watcher clues for one sample mover.
"""

import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from lib.lore import fetch_lore_packet


load_dotenv(ROOT / ".env")


SAMPLE_MOVERS = {
    "dickbutt": {
        "chain_slug": "base",
        "address": "",
        "symbol": "DICKBUTT",
        "direction": "pump",
        "price_change_pct": 773,
        "price_change_window": "h1",
        "market_cap_usd": 1_000_000,
        "dexscreener_url": "https://dexscreener.com/base",
        "x_handle": "",
        "website_url": "",
    },
}


def main() -> int:
    if not os.environ.get("XAI_API_KEY"):
        print("XAI_API_KEY is not set")
        return 1

    sample = os.environ.get("LORE_TEST_SAMPLE", "dickbutt")
    mover = SAMPLE_MOVERS.get(sample)
    if not mover:
        print(f"unknown LORE_TEST_SAMPLE={sample!r}")
        return 1

    packet = fetch_lore_packet(mover)
    print(json.dumps(packet, indent=2))

    lore = packet.get("lore") or ""
    references = packet.get("references") or []
    clues = packet.get("watcher_clues") or {}

    print()
    print("=== summary ===")
    print(f"lore_chars={len(lore)}")
    print(f"reference_count={len(references)}")
    print(f"accounts={clues.get('accounts') or []}")
    print(f"phrases={clues.get('phrases') or []}")
    print(f"keywords={clues.get('keywords') or []}")
    print(f"catalysts={clues.get('catalysts') or []}")

    if not lore:
        print("FAIL: no lore returned")
        return 1
    if not references:
        print("WARN: no references returned")
    if not any(clues.get(k) for k in ("accounts", "phrases", "keywords", "catalysts")):
        print("WARN: no watcher clues returned")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
