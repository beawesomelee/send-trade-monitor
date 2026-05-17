"""
Base ecosystem movement scanner.

Scans GeckoTerminal pools for tokens with significant 1-hour price movement
(pumps or dumps) that meet MC + liquidity floors.

Designed to run hourly. Uses CoinGecko Pro tier via the existing
GECKOTERMINAL_API_KEY env var.
"""

import json
import time
from collections import defaultdict
from pathlib import Path

import requests

from lib.dexscreener import (
    _gt_endpoint,
    _norm,
    _ds_batch_fetch_pairs,
    NETWORK_MAP,
    SUPPORTED_DS_CHAINS,
)

SCRIPT_DIR = Path(__file__).resolve().parent.parent
ALERTS_FILE = SCRIPT_DIR / "data" / "movement_alerts.json"


def find_movers(config: dict, window: str = "h1") -> list[dict]:
    """Scan GT pools across configured chains, return tokens that pumped or dumped.

    window: which price_change_percentage field to use ("h1", "h6", etc.).
    Default h1. Use h6 as fallback when h1 is empty.
    """
    cfg = config["movement"]
    min_mc = cfg["min_market_cap_usd"]
    min_liq = cfg["min_liquidity_usd"]
    pump_thr = cfg["pump_threshold_pct"]
    dump_thr = cfg["dump_threshold_pct"]
    max_pages = cfg.get("gt_pages", 10)

    url_template, headers, base_delay = _gt_endpoint()
    pools_by_token: dict[tuple, list] = defaultdict(list)

    for chain_cfg in config["chains"]:
        slug = chain_cfg["slug"]
        if slug not in SUPPORTED_DS_CHAINS:
            continue
        network = NETWORK_MAP.get(slug, {}).get("gt_network", slug)
        print(f"  scanning GT pools for {slug} (window={window})...")

        for page in range(1, max_pages + 1):
            url = url_template.format(network=network)
            params = {"page": page, "sort": "h24_volume_usd_desc"}
            time.sleep(base_delay)
            try:
                r = requests.get(url, params=params, headers=headers, timeout=20)
                if r.status_code != 200:
                    print(f"    page {page} HTTP {r.status_code}")
                    break
                pools = r.json().get("data", []) or []
                if not pools:
                    break
            except Exception as e:
                print(f"    page {page} err: {e}")
                break

            for p in pools:
                attrs = p.get("attributes", {})
                rels = p.get("relationships", {})

                base_id = (rels.get("base_token") or {}).get("data", {}).get("id", "")
                addr = base_id.split("_", 1)[1] if "_" in base_id else ""
                if not addr:
                    continue

                price_changes = attrs.get("price_change_percentage") or {}
                pc = _safe_float(price_changes.get(window))
                if pc < dump_thr or (dump_thr < pc < pump_thr):
                    continue  # not enough movement either direction

                mc = _safe_float(attrs.get("market_cap_usd")) or _safe_float(attrs.get("fdv_usd"))
                liq = _safe_float(attrs.get("reserve_in_usd"))
                if mc < min_mc or liq < min_liq:
                    continue

                vol_h1 = _safe_float((attrs.get("volume_usd") or {}).get("h1"))
                vol_h6 = _safe_float((attrs.get("volume_usd") or {}).get("h6"))
                vol_h24 = _safe_float((attrs.get("volume_usd") or {}).get("h24"))

                pools_by_token[(slug, _norm(addr, slug))].append({
                    "chain_slug": slug,
                    "address": _norm(addr, slug),
                    "pool_name": attrs.get("name", ""),
                    "market_cap_usd": mc,
                    "liquidity_usd": liq,
                    "volume_h1_usd": vol_h1,
                    "volume_h6_usd": vol_h6,
                    "volume_h24_usd": vol_h24,
                    "price_change_pct": pc,
                    "price_change_h1_pct": _safe_float(price_changes.get("h1")),
                    "price_change_h6_pct": _safe_float(price_changes.get("h6")),
                    "price_change_window": window,
                    "price_usd": _safe_float(attrs.get("base_token_price_usd")),
                })

    movers = []
    sort_vol_key = "volume_h1_usd" if window == "h1" else "volume_h6_usd"
    for (slug, addr), pool_list in pools_by_token.items():
        best = max(pool_list, key=lambda x: x[sort_vol_key])
        direction = "pump" if best["price_change_pct"] >= pump_thr else "dump"
        movers.append({**best, "direction": direction})

    movers.sort(key=lambda m: abs(m["price_change_pct"]), reverse=True)
    return movers


def enrich_movers(movers: list[dict]) -> list[dict]:
    """Use DS /tokens/v1 to fill in symbol, name, logo_uri.
    Drop any without a logo (per Austin's spec — every alert must have a logo).
    """
    by_chain = defaultdict(list)
    for m in movers:
        by_chain[m["chain_slug"]].append(m["address"])

    enriched_addrs: dict[tuple, dict] = {}
    for chain_slug, addrs in by_chain.items():
        pairs_by_addr = _ds_batch_fetch_pairs(chain_slug, addrs)
        for addr, pairs in pairs_by_addr.items():
            if not pairs:
                continue
            p = pairs[0]
            bt = p.get("baseToken") or {}
            info = p.get("info") or {}
            logo = (info or {}).get("imageUrl") if isinstance(info, dict) else None
            enriched_addrs[(chain_slug, addr.lower())] = {
                "symbol": bt.get("symbol", ""),
                "name": bt.get("name", ""),
                "logo_uri": logo or "",
            }

    out = []
    for m in movers:
        key = (m["chain_slug"], m["address"].lower())
        extra = enriched_addrs.get(key, {})
        if not extra.get("logo_uri"):
            continue
        out.append({
            **m,
            **extra,
            "dexscreener_url": f"https://dexscreener.com/{m['chain_slug']}/{m['address']}",
        })
    return out


def filter_by_cooldown(movers: list[dict], cooldown_hours: float) -> tuple[list[dict], list[dict]]:
    """Filter movers against persisted alert history.

    Returns (new_movers, retained_history) — new_movers are not in cooldown.
    retained_history is the existing entries that are still within the cooldown
    window (caller writes them back along with new ones).
    """
    history = _load_history()
    now = time.time()
    cutoff = now - (cooldown_hours * 3600)

    active = {(h["chain_slug"], h["address"].lower(), h["direction"]): h
              for h in history if h.get("timestamp", 0) >= cutoff}

    new = []
    for m in movers:
        key = (m["chain_slug"], m["address"].lower(), m["direction"])
        if key in active:
            continue
        new.append(m)
    return new, list(active.values())


def record_alerts(new_movers: list[dict], retained_history: list[dict]):
    """Persist alerts to data/movement_alerts.json."""
    now = time.time()
    entries = list(retained_history)
    for m in new_movers:
        entries.append({
            "chain_slug": m["chain_slug"],
            "address": m["address"].lower(),
            "symbol": m.get("symbol", ""),
            "direction": m["direction"],
            "price_change_h1_pct": m["price_change_h1_pct"],
            "timestamp": now,
        })

    # prune anything older than 48h to keep file lean
    cutoff = now - (48 * 3600)
    entries = [e for e in entries if e.get("timestamp", 0) >= cutoff]
    entries.sort(key=lambda e: -e["timestamp"])

    ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ALERTS_FILE.write_text(json.dumps(entries, indent=2))


def _load_history() -> list:
    if not ALERTS_FILE.exists():
        return []
    try:
        return json.loads(ALERTS_FILE.read_text())
    except Exception:
        return []


def _safe_float(v) -> float:
    try:
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0
