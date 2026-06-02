"""
Push a movement alert's lore directly to Send.Trade via the admin lore-logs
endpoint, so the trader-voice blurb auto-appears in Send.Trade's UI without
manual intervention.

Endpoint: POST https://api.send.trade/admin/lore/logs
Auth:     HTTP Basic with EMPTY username and DOCS_PASSWORD as password
          (base64(":<DOCS_PASSWORD>"))

Request body shape:
  {
    "tokenAddress": "...",      // required, 40-char Base hex OR up to 44-char Solana base58
    "description":  "...",      // required, 1-4000 chars
    "image":        "https://...",  // optional, ≤1024 chars
    "category":     "...",      // optional, one of: genesis|team|cto|partnership|
                                //                  milestone|listing|incident|
                                //                  update|community
    "sortOrder":    0           // optional, int
  }

Behavior:
- Best-effort. If DOCS_PASSWORD is unset or the POST fails for any reason,
  returns None and logs. Never raises (so the scanner can't be broken by
  a transient Send.Trade outage).
- Returns the parsed response dict on success (caller usually wants the
  resulting `id` so it can be surfaced in Discord for review/delete).
"""

import os
import base64
import requests

ENDPOINT = "https://api.send.trade/admin/lore/logs"
CATEGORIES = {
    "genesis", "team", "cto", "partnership", "milestone",
    "listing", "incident", "update", "community",
}


def push_lore(mover: dict, timeout: int = 15) -> dict | None:
    """Push a single mover's Grok-generated lore to Send.Trade.

    mover dict expected keys: address, lore, direction, symbol, logo_uri (optional).
    Returns the response JSON on success (often {"id": "...", ...}), else None.
    """
    password = os.environ.get("DOCS_PASSWORD", "")
    if not password:
        print("    DOCS_PASSWORD not set, skipping send.trade lore push")
        return None

    description = (mover.get("lore") or "").strip()
    if not description:
        # No lore = nothing to post. (Grok returned empty or a fallback we
        # don't want surfaced on Send.Trade.)
        return None
    if len(description) > 4000:
        description = description[:4000]

    address = (mover.get("address") or "").strip()
    if not address:
        return None

    payload = {
        "tokenAddress": address,
        "description": description,
        "category": _pick_category(mover),
    }
    image = mover.get("logo_uri") or ""
    if image and len(image) <= 1024:
        payload["image"] = image

    # Basic auth: username is empty, password is DOCS_PASSWORD.
    token = base64.b64encode(f":{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(ENDPOINT, json=payload, headers=headers, timeout=timeout)
        if r.status_code in (200, 201):
            try:
                return r.json()
            except Exception:
                return {"raw": r.text}
        print(f"    send.trade lore POST HTTP {r.status_code}: {r.text[:300]}")
        return None
    except Exception as e:
        print(f"    send.trade lore POST err: {e}")
        return None


def _pick_category(mover: dict) -> str:
    """Map a mover to one of Send.Trade's lore categories.

    Heuristic for now (the dev can tune this):
      - dump (negative price_change_pct) -> 'incident'
      - lore mentions a notable @handle (jkrdoc, Uniswap, Coinbase, etc.) -> 'community'
      - everything else -> 'update'
    """
    direction = (mover.get("direction") or "").lower()
    if direction == "dump" or (mover.get("price_change_pct") or 0) < 0:
        return "incident"

    lore = (mover.get("lore") or "").lower()
    community_signals = ("@jkrdoc", "@uniswap", "@coinbase", "@base",
                         "shouted out", "shoutout", "co-sign", "co sign",
                         "thread", "kol", "founder posted")
    if any(s in lore for s in community_signals):
        return "community"

    return "update"
