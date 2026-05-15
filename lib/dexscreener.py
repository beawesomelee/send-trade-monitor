"""
Token discovery for Send.Trade monitor.

Two modes:
  - Backfill (GeckoTerminal): exhaustive scan of pools sorted by 24h volume.
    Used once on first run; thereafter only when forced via --backfill.
  - Incremental (DexScreener): refresh known tokens + discover new launches
    (pairCreatedAt within last N days). Used for daily runs.
"""

import time
from collections import defaultdict

import requests

GT_POOLS = "https://api.geckoterminal.com/api/v2/networks/{network}/pools"
DS_TOKENS = "https://api.dexscreener.com/tokens/v1/{chain}/{addresses}"
DS_TOKEN_PAIRS = "https://api.dexscreener.com/token-pairs/v1/{chain}/{address}"
DS_SEARCH = "https://api.dexscreener.com/latest/dex/search"
DS_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"
DS_BOOSTS_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1"
DS_BOOSTS_TOP = "https://api.dexscreener.com/token-boosts/top/v1"

DS_WIDE_QUERIES = [
    "base", "solana",
    "WETH", "USDC", "USDT", "DAI", "ETH", "SOL", "WSOL",
    "wstETH", "cbETH", "cbBTC", "stETH", "rETH",
    "FRAX", "frxUSD", "USDS", "PYUSD",
    "ai", "agent", "meme", "depin", "rwa", "defi", "pump",
    "AERO", "BRETT", "TOSHI", "DEGEN", "MOG", "KEYCAT", "VIRTUAL", "B3",
    "BONK", "WIF", "POPCAT", "JTO", "JUP", "PYTH", "RAY", "ORCA",
    "trump", "pepe", "shib", "doge",
]

GT_DELAY = 4.0   # ~15 req/min, conservatively under GeckoTerminal 30/min
DS_DELAY = 1.2   # safely under DexScreener 60/min
DS_BATCH = 30     # DexScreener accepts up to 30 addresses per request
MAX_PAGES = 15

SUPPORTED_DS_CHAINS = {"base", "solana"}
CHAIN_ID_MAP = {"base": 8453, "solana": 501474}

NETWORK_MAP = {
    "base": {"gt_network": "base", "ds_chain": "base"},
    "solana": {"gt_network": "solana", "ds_chain": "solana"},
}


def fetch_candidates(config: dict) -> list[dict]:
    """Backfill (exhaustive) discovery via GeckoTerminal.

    Intentionally permissive on logo presence — many established tokens (USDT,
    cbETH, etc.) don't have logos on DS/GT. The logo filter is reserved for the
    incremental path where it's more useful for filtering new scam launches.
    """
    min_mc = config["thresholds"]["min_market_cap_usd"]
    min_vol = config["thresholds"]["min_volume_24h_usd"]
    min_liq = config["thresholds"].get("min_liquidity_usd", 0)
    max_vol_liq = config["thresholds"].get("max_volume_to_liquidity_ratio", 0)
    max_mc = config["thresholds"].get("max_market_cap_usd", 0)

    raw_pools = []
    for i, chain_cfg in enumerate(config["chains"]):
        if i > 0:
            time.sleep(30)  # long pause between chains to avoid GeckoTerminal 429
        slug = chain_cfg["slug"]
        network = NETWORK_MAP.get(slug, {}).get("gt_network", slug)
        print(f"  fetching GeckoTerminal pools for {slug}...")
        pools = _fetch_gt_pools(network)
        for p in pools:
            p["_chain_slug"] = slug
            p["_chain_id"] = chain_cfg["chain_id"]
        raw_pools.extend(pools)
        print(f"    {len(pools)} total pools collected")

    # DS wide discovery: expand the candidate pool with addresses GT may have missed
    print("  expanding via DexScreener profiles + boosts + search queries...")
    ds_addrs = _ds_wide_discovery()
    print(f"    {sum(len(v) for v in ds_addrs.values())} unique addresses from DS")

    # subtract addresses we already have from GT pool data
    have = set()
    for p in raw_pools:
        have.add((p["_chain_slug"], _norm(p["address"], p["_chain_slug"])))

    new_addrs = defaultdict(list)
    for slug, addrs in ds_addrs.items():
        for a in addrs:
            if (slug, _norm(a, slug)) not in have:
                new_addrs[slug].append(a)
    new_count = sum(len(v) for v in new_addrs.values())
    print(f"    {new_count} new addresses not already in GT pool data")

    if new_count:
        ds_pools = _ds_addresses_to_pools(dict(new_addrs))
        raw_pools.extend(ds_pools)
        print(f"    fetched {len(ds_pools)} pool records from DS")

    aggregated = _aggregate(raw_pools, min_mc, min_vol, min_liq, max_vol_liq, max_mc)
    print(f"  {len(aggregated)} unique tokens after MC + vol + liquidity + wash-trade filters")

    _enrich_from_dexscreener(aggregated)

    aggregated.sort(key=lambda x: x["volume_24h_usd"], reverse=True)
    return aggregated


PAGINATE_VOL_FLOOR = 50_000  # stop paginating when page-top pool falls below this


def _fetch_gt_pools(network: str) -> list[dict]:
    """Paginate GeckoTerminal pools sorted by volume desc.

    Keeps ALL pools (no per-pool volume filter) so token-level aggregation can
    capture tokens whose volume is distributed across many smaller pools.
    Stops paginating only when the top pool on a page is below PAGINATE_VOL_FLOOR.
    """
    all_pools = []
    for page in range(1, MAX_PAGES + 1):
        url = GT_POOLS.format(network=network)
        params = {"page": page, "sort": "h24_volume_usd_desc"}

        r = None
        for attempt in range(3):
            delay = GT_DELAY * (2 ** attempt)
            time.sleep(delay)
            try:
                r = requests.get(url, params=params, timeout=20)
                if r.status_code == 429:
                    print(f"    page {page} rate-limited, retrying in {delay*2:.0f}s...")
                    continue
                r.raise_for_status()
                break
            except requests.RequestException as e:
                if attempt == 2:
                    print(f"    page {page} error after retries: {e}")
        if r is None or r.status_code != 200:
            break

        data = r.json()
        pools = data.get("data", [])
        if not pools:
            break

        page_max_vol = 0
        for p in pools:
            attrs = p.get("attributes", {})
            vol_str = (attrs.get("volume_usd") or {}).get("h24", "0")
            try:
                vol = float(vol_str) if vol_str else 0
            except (TypeError, ValueError):
                vol = 0

            page_max_vol = max(page_max_vol, vol)

            rels = p.get("relationships", {})
            base_id = rels.get("base_token", {}).get("data", {}).get("id", "")
            address = base_id.split("_", 1)[1] if "_" in base_id else ""
            if not address:
                continue

            mc = attrs.get("market_cap_usd")
            fdv = attrs.get("fdv_usd")
            mc_val = float(mc) if mc else (float(fdv) if fdv else 0)

            pool_name = attrs.get("name", "")
            base_symbol = pool_name.split(" / ")[0].strip() if " / " in pool_name else ""

            all_pools.append({
                "address": address,
                "symbol": base_symbol,
                "market_cap_usd": mc_val,
                "volume_24h_usd": vol,
                "liquidity_usd": float(attrs.get("reserve_in_usd") or 0),
            })

        if page_max_vol < PAGINATE_VOL_FLOOR:
            break

    return all_pools


def _aggregate(pools: list[dict], min_mc: float, min_vol: float,
               min_liq: float = 0, max_vol_liq_ratio: float = 0,
               max_mc: float = 0) -> list[dict]:
    """Aggregate pools per unique (chain, address). Sum volumes, max liquidity/MC.

    Drops tokens with vol/liq above max_vol_liq_ratio (catches wash-traded scams
    where volume is structurally impossible given the pool's liquidity).
    Drops tokens with MC above max_mc (catches bad-supply scams claiming $T MC).
    """
    buckets = defaultdict(list)
    for p in pools:
        slug = p.get("_chain_slug", "")
        addr = p["address"].lower() if slug == "base" else p["address"]
        if not addr:
            continue
        key = (slug, addr)
        buckets[key].append(p)

    results = []
    for (slug, addr), group in buckets.items():
        total_vol = sum(p["volume_24h_usd"] for p in group)
        max_liq = max(p["liquidity_usd"] for p in group)
        best_mc = max(p["market_cap_usd"] for p in group)

        if best_mc < min_mc or total_vol < min_vol or max_liq < min_liq:
            continue
        if max_mc > 0 and best_mc > max_mc:
            continue
        if max_vol_liq_ratio > 0 and max_liq > 0 and total_vol / max_liq > max_vol_liq_ratio:
            continue

        first = group[0]
        results.append({
            "chain_slug": slug,
            "chain_id": first["_chain_id"],
            "address": addr,
            "symbol": first.get("symbol", ""),
            "name": "",
            "market_cap_usd": round(best_mc),
            "volume_24h_usd": round(total_vol),
            "liquidity_usd": round(max_liq),
            "logo_uri": "",
            "dexscreener_url": f"https://dexscreener.com/{slug}/{addr}",
        })

    return results


def _enrich_from_dexscreener(candidates: list[dict]):
    """Batch-fetch token details (name, symbol, logo) from DexScreener."""
    by_chain = defaultdict(list)
    for c in candidates:
        by_chain[c["chain_slug"]].append(c)

    for chain_slug, tokens in by_chain.items():
        ds_chain = NETWORK_MAP.get(chain_slug, {}).get("ds_chain", chain_slug)
        addresses = [t["address"] for t in tokens]

        for i in range(0, len(addresses), DS_BATCH):
            batch_addrs = addresses[i:i + DS_BATCH]
            addr_str = ",".join(batch_addrs)
            url = DS_TOKENS.format(chain=ds_chain, addresses=addr_str)

            time.sleep(DS_DELAY)
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                pairs = r.json() if isinstance(r.json(), list) else r.json().get("pairs", [])
            except Exception as e:
                print(f"    DexScreener enrich error: {e}")
                continue

            addr_map = {t["address"].lower(): t for t in tokens}
            seen = set()
            for p in pairs:
                bt = p.get("baseToken", {})
                addr = (bt.get("address") or "").lower()
                if addr in addr_map and addr not in seen:
                    seen.add(addr)
                    t = addr_map[addr]
                    if bt.get("name"):
                        t["name"] = bt["name"]
                    if bt.get("symbol"):
                        t["symbol"] = bt["symbol"]
                    info = p.get("info") or {}
                    if isinstance(info, dict) and info.get("imageUrl"):
                        t["logo_uri"] = info["imageUrl"]

        print(f"    enriched {len(tokens)} {chain_slug} tokens from DexScreener")

    _fallback_logos_from_gt(candidates)


def _fallback_logos_from_gt(candidates: list[dict]):
    """For tokens still missing a logo after DS enrichment, try GeckoTerminal."""
    missing = [c for c in candidates if not c.get("logo_uri")]
    if not missing:
        return
    print(f"    fetching GT logos for {len(missing)} tokens missing DS logo...")
    for c in missing:
        chain = c["chain_slug"]
        url = f"https://api.geckoterminal.com/api/v2/networks/{chain}/tokens/{c['address']}"
        time.sleep(2.5)
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            attrs = r.json().get("data", {}).get("attributes", {})
            img = attrs.get("image_url", "")
            if img and img != "missing.png" and "missing" not in img:
                c["logo_uri"] = img
        except Exception:
            pass


# ============================================================================
# DexScreener-based incremental discovery (for daily runs)
# ============================================================================

GT_DAILY_PAGES = 5  # how many GT pages to scan in daily mode (vs MAX_PAGES for backfill)


def fetch_new_candidates_ds(config: dict, max_age_days: int = 7,
                             skip_addresses: set | None = None) -> list[dict]:
    """
    Discover new tokens via DexScreener profiles + boosts + search queries,
    PLUS a lightweight GeckoTerminal top-pool scan (catches tokens whose volume
    is spread across many pools and don't surface via DS search keywords).

    Filtered to pairs younger than max_age_days, meeting MC + vol + liq thresholds.
    skip_addresses: set of (chain_slug, lowercase_address) to skip (already in sheet).
    """
    min_mc = config["thresholds"]["min_market_cap_usd"]
    min_vol = config["thresholds"]["min_volume_24h_usd"]
    min_liq = config["thresholds"].get("min_liquidity_usd", 0)
    max_mc = config["thresholds"].get("max_market_cap_usd", 0)
    require_logo = config["thresholds"].get("require_logo_uri_incremental", False)
    # Note: max_volume_to_liquidity_ratio intentionally NOT applied here.
    # New tokens going viral can briefly hit very high vol/liq ratios — not necessarily wash-trade.
    skip_addresses = skip_addresses or set()

    discovery_addrs = _ds_wide_discovery()
    ds_total = sum(len(v) for v in discovery_addrs.values())

    # supplement with GT top-pool scan (catches tokens DS search misses)
    gt_addrs = _gt_top_addresses_daily(config["chains"])
    for chain_slug, addrs in gt_addrs.items():
        existing = set(discovery_addrs.get(chain_slug, []))
        new = [a for a in addrs if a not in existing]
        if new:
            discovery_addrs.setdefault(chain_slug, []).extend(new)
    gt_total = sum(len(v) for v in gt_addrs.values())
    print(f"  collected {ds_total} addresses from DS, +{gt_total} from GT top pools "
          f"({sum(len(v) for v in discovery_addrs.values())} unique total)")

    cutoff_ms = int((time.time() - max_age_days * 86400) * 1000)
    candidates = []

    for chain_slug, addrs in discovery_addrs.items():
        if chain_slug not in SUPPORTED_DS_CHAINS:
            continue
        addrs = [a for a in addrs if (chain_slug, _norm(a, chain_slug)) not in skip_addresses]
        if not addrs:
            continue

        pairs_by_addr = _ds_batch_fetch_pairs(chain_slug, addrs)
        for addr, pairs in pairs_by_addr.items():
            agg = _aggregate_token_pairs(pairs)
            if not agg:
                continue

            # Initial filter screen on rough data from /tokens/v1
            if (agg["market_cap_usd"] < min_mc or agg["volume_24h_usd"] < min_vol
                    or agg["liquidity_usd"] < min_liq):
                continue
            if max_mc > 0 and agg["market_cap_usd"] > max_mc:
                continue
            if require_logo and not agg.get("logo_uri"):
                continue

            # Age check: /tokens/v1 returns only the primary pair (often missing
            # pairCreatedAt). Fetch the full pair list to find a valid creation
            # date for the token's oldest known pair.
            full_pairs = _fetch_all_token_pairs_ds(chain_slug, addr)
            check_pairs = full_pairs if full_pairs else pairs
            valid_created = [p.get("pairCreatedAt") for p in check_pairs
                             if p.get("pairCreatedAt")]
            if not valid_created:
                continue  # no creation date anywhere — can't verify recency
            oldest_ms = min(valid_created)
            if oldest_ms < cutoff_ms:
                continue  # too old

            candidates.append({
                "chain_slug": chain_slug,
                "chain_id": CHAIN_ID_MAP[chain_slug],
                "address": addr,
                **agg,
                "dexscreener_url": f"https://dexscreener.com/{chain_slug}/{addr}",
            })

    print(f"  {len(candidates)} new candidates after age + MC + vol filters")
    return candidates


def refresh_addresses_ds(addresses_by_chain: dict) -> list[dict]:
    """Batch-refresh stats for known tokens via DexScreener."""
    results = []
    for chain_slug, addrs in addresses_by_chain.items():
        if chain_slug not in SUPPORTED_DS_CHAINS or not addrs:
            continue
        pairs_by_addr = _ds_batch_fetch_pairs(chain_slug, addrs)
        for addr, pairs in pairs_by_addr.items():
            agg = _aggregate_token_pairs(pairs)
            if not agg:
                continue
            results.append({
                "chain_slug": chain_slug,
                "chain_id": CHAIN_ID_MAP[chain_slug],
                "address": addr,
                **agg,
                "dexscreener_url": f"https://dexscreener.com/{chain_slug}/{addr}",
            })
    print(f"  refreshed {len(results)} known tokens via DexScreener")
    return results


def _collect_discovery_addresses() -> dict:
    """Pull addresses from DS profiles + boosts endpoints. Returns {chain: [addrs]}."""
    addr_by_chain = defaultdict(set)
    for url in (DS_PROFILES, DS_BOOSTS_LATEST, DS_BOOSTS_TOP):
        time.sleep(DS_DELAY)
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            entries = data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            print(f"    {url} error: {e}")
            continue

        for entry in entries:
            chain = (entry.get("chainId") or "").lower()
            addr = entry.get("tokenAddress") or ""
            if chain in SUPPORTED_DS_CHAINS and addr:
                addr_by_chain[chain].add(_norm(addr, chain))

    return {k: list(v) for k, v in addr_by_chain.items()}


def _ds_batch_fetch_pairs(chain_slug: str, addresses: list) -> dict:
    """Batch-fetch pair data via /tokens/v1. Returns {address: [pairs]}."""
    result = defaultdict(list)
    norm_set = {_norm(a, chain_slug) for a in addresses}

    for i in range(0, len(addresses), DS_BATCH):
        batch = addresses[i:i + DS_BATCH]
        url = DS_TOKENS.format(chain=chain_slug, addresses=",".join(batch))
        time.sleep(DS_DELAY)
        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            data = r.json()
            pairs = data if isinstance(data, list) else data.get("pairs", [])
        except Exception as e:
            print(f"    DS batch error: {e}")
            continue

        for p in pairs:
            bt = p.get("baseToken", {})
            addr = _norm(bt.get("address", ""), chain_slug)
            if addr in norm_set:
                result[addr].append(p)

    return dict(result)


def _aggregate_token_pairs(pairs: list) -> dict | None:
    """Aggregate a token's pairs: sum volume, max liquidity, max MC, oldest pair info."""
    if not pairs:
        return None

    first = pairs[0]
    bt = first.get("baseToken", {})

    total_vol = sum(((p.get("volume") or {}).get("h24") or 0) for p in pairs)
    max_liq = max((((p.get("liquidity") or {}).get("usd") or 0) for p in pairs), default=0)
    best_mc = max(((p.get("marketCap") or p.get("fdv") or 0) for p in pairs), default=0)

    logo = ""
    info = first.get("info") or {}
    if isinstance(info, dict):
        logo = info.get("imageUrl", "")

    return {
        "symbol": bt.get("symbol", ""),
        "name": bt.get("name", ""),
        "market_cap_usd": round(best_mc),
        "volume_24h_usd": round(total_vol),
        "liquidity_usd": round(max_liq),
        "logo_uri": logo,
    }


def _norm(address: str, chain_slug: str) -> str:
    """Normalize address: lowercase for Base (EVM hex), preserve case for Solana (base58)."""
    if chain_slug == "base":
        return (address or "").lower()
    return address or ""


def _ds_wide_discovery() -> dict:
    """Cast a wide DexScreener net for candidate addresses.

    Pulls from profiles + boosts (latest + top) and runs many search queries
    across chain names, common quote tokens, popular symbols, and categories.
    Returns {chain_slug: [addresses]} for Base + Solana only.
    """
    addresses_by_chain = defaultdict(set)

    # profiles + boosts
    for url in (DS_PROFILES, DS_BOOSTS_LATEST, DS_BOOSTS_TOP):
        time.sleep(DS_DELAY)
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            entries = data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            print(f"    {url} err: {e}")
            continue
        for entry in entries:
            chain = (entry.get("chainId") or "").lower()
            addr = entry.get("tokenAddress") or ""
            if chain in SUPPORTED_DS_CHAINS and addr:
                addresses_by_chain[chain].add(_norm(addr, chain))

    # search queries (each returns up to 30 pairs across all chains)
    for q in DS_WIDE_QUERIES:
        time.sleep(DS_DELAY)
        try:
            r = requests.get(DS_SEARCH, params={"q": q}, timeout=15)
            r.raise_for_status()
            pairs = r.json().get("pairs", []) or []
        except Exception as e:
            print(f"    search '{q}' err: {e}")
            continue
        for p in pairs:
            chain = (p.get("chainId") or "").lower()
            bt = p.get("baseToken") or {}
            addr = bt.get("address") or ""
            if chain in SUPPORTED_DS_CHAINS and addr:
                addresses_by_chain[chain].add(_norm(addr, chain))

    return {k: list(v) for k, v in addresses_by_chain.items()}


def _ds_addresses_to_pools(addresses_by_chain: dict) -> list[dict]:
    """For each address, fetch its DS pair data and convert to internal pool format."""
    all_pools = []
    for chain_slug, addrs in addresses_by_chain.items():
        if not addrs:
            continue
        for i in range(0, len(addrs), DS_BATCH):
            batch = addrs[i:i + DS_BATCH]
            url = DS_TOKENS.format(chain=chain_slug, addresses=",".join(batch))
            time.sleep(DS_DELAY)
            try:
                r = requests.get(url, timeout=20)
                r.raise_for_status()
                data = r.json()
                pairs = data if isinstance(data, list) else data.get("pairs", [])
            except Exception as e:
                print(f"    DS batch err: {e}")
                continue

            for p in pairs:
                bt = p.get("baseToken") or {}
                addr = _norm(bt.get("address", ""), chain_slug)
                if not addr:
                    continue
                vol = (p.get("volume") or {}).get("h24") or 0
                liq = (p.get("liquidity") or {}).get("usd") or 0
                mc = p.get("marketCap") or p.get("fdv") or 0

                all_pools.append({
                    "address": addr,
                    "symbol": bt.get("symbol", ""),
                    "market_cap_usd": float(mc) if mc else 0,
                    "volume_24h_usd": float(vol) if vol else 0,
                    "liquidity_usd": float(liq) if liq else 0,
                    "_chain_slug": chain_slug,
                    "_chain_id": CHAIN_ID_MAP[chain_slug],
                })
    return all_pools


def _gt_top_addresses_daily(chains: list[dict]) -> dict:
    """Lightweight GT scan for daily runs: top GT_DAILY_PAGES pages per chain.

    Returns {chain_slug: [base_token_addresses]} — token discovery only, no
    aggregation or filtering here. The DS batch fetch downstream gets the actual
    pair metrics.
    """
    addrs_by_chain = defaultdict(set)
    for i, chain_cfg in enumerate(chains):
        if i > 0:
            time.sleep(8)  # spacing between chains (less than backfill's 30s — fewer pages)
        slug = chain_cfg["slug"]
        if slug not in SUPPORTED_DS_CHAINS:
            continue
        network = NETWORK_MAP.get(slug, {}).get("gt_network", slug)

        for page in range(1, GT_DAILY_PAGES + 1):
            url = GT_POOLS.format(network=network)
            params = {"page": page, "sort": "h24_volume_usd_desc"}
            time.sleep(GT_DELAY)
            try:
                r = requests.get(url, params=params, timeout=15)
                if r.status_code == 429:
                    print(f"    GT daily {slug} page {page} rate-limited, skipping rest")
                    break
                if r.status_code != 200:
                    break
                pools = r.json().get("data", [])
                if not pools:
                    break
                for p in pools:
                    rels = p.get("relationships", {})
                    base_id = rels.get("base_token", {}).get("data", {}).get("id", "")
                    addr = base_id.split("_", 1)[1] if "_" in base_id else ""
                    if addr:
                        addrs_by_chain[slug].add(_norm(addr, slug))
            except Exception as e:
                print(f"    GT daily {slug} page {page} err: {e}")
                break

    return {k: list(v) for k, v in addrs_by_chain.items()}


def _fetch_all_token_pairs_ds(chain_slug: str, address: str) -> list:
    """Fetch ALL pairs for a single token via /token-pairs/v1.

    /tokens/v1 only returns the primary pair (sometimes missing pairCreatedAt).
    /token-pairs/v1 returns every pool the token trades in — used to discover
    a valid creation date for age filtering.
    """
    url = DS_TOKEN_PAIRS.format(chain=chain_slug, address=address)
    time.sleep(DS_DELAY)
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("pairs", []) or []
    except Exception as e:
        print(f"    /token-pairs/v1 err for {address[:10]}: {e}")
        return []
