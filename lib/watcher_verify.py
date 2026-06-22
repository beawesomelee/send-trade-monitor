"""Verify X watcher hits against token movement before Discord alerts."""

from __future__ import annotations

import glob
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from lib.dexscreener import (
    _aggregate_token_pairs,
    _ds_batch_fetch_pairs,
    _fetch_all_token_pairs_ds,
    _norm,
)
from lib.movement_events import MOVEMENT_EVENTS_FILE


ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = ROOT / "data" / "snapshots"

BASE_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])\$([A-Za-z][A-Za-z0-9_]{1,20})\b")

PUMP_WORDS = {
    "pump", "pumped", "pumping", "run", "running", "runner", "ripping",
    "breakout", "broke out", "ath", "send", "sending", "moon", "mooning",
    "gainer", "gainers", "up", "green", "bid", "bidded", "leg up",
}
DUMP_WORDS = {
    "dump", "dumped", "dumping", "rug", "rugged", "exploit", "hacked",
    "drain", "drained", "selloff", "sell off", "down", "red", "bleed",
    "bleeding", "crash", "crashed", "scam", "delist", "delisted",
}


def verify_watcher_hit(
    payload: dict,
    *,
    min_market_cap_usd: float = 1_000_000,
    min_liquidity_usd: float = 30_000,
    min_volume_24h_usd: float = 500_000,
    pump_h1_pct: float = 20.0,
    pump_h6_pct: float = 35.0,
    dump_h1_pct: float = -20.0,
    dump_h6_pct: float = -35.0,
) -> dict:
    """Return verification result for one X stream payload."""
    text = tweet_text(payload)
    direction_hint = movement_language_direction(text)
    if direction_hint == "none":
        return _result(False, "no_price_movement_language", text=text)

    token = match_token_from_payload(payload)
    if not token:
        return _result(False, "no_unique_token_match", text=text, direction=direction_hint)

    market = fetch_token_market(token)
    if not market:
        return _result(False, "market_fetch_failed", text=text, token=token, direction=direction_hint)

    quality_reason = token_quality_failure(
        market,
        min_market_cap_usd=min_market_cap_usd,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_24h_usd=min_volume_24h_usd,
    )
    if quality_reason:
        return _result(False, quality_reason, text=text, token=token, market=market, direction=direction_hint)

    direction = verified_direction(market, direction_hint, pump_h1_pct, pump_h6_pct, dump_h1_pct, dump_h6_pct)
    if not direction:
        return _result(False, "price_movement_below_threshold", text=text, token=token, market=market, direction=direction_hint)

    return _result(True, "verified_price_movement", text=text, token=token, market=market, direction=direction)


def match_token_from_payload(payload: dict) -> dict | None:
    text = tweet_text(payload)
    index = load_token_index()
    direct = match_direct_address(text, index)
    if direct:
        return direct

    symbols = {symbol.lower() for symbol in CASHTAG_RE.findall(text)}
    candidates = []
    for symbol in symbols:
        candidates.extend(index["by_symbol"].get(symbol, []))

    if not candidates:
        words = set(re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,20}\b", text.lower()))
        for word in words:
            candidates.extend(index["by_symbol"].get(word, []))

    return choose_unique_token(candidates)


def load_token_index() -> dict:
    tokens = {}
    for token in latest_snapshot_tokens() + movement_event_tokens():
        key = token_key(token)
        if not key:
            continue
        existing = tokens.get(key, {})
        tokens[key] = {**existing, **{k: v for k, v in token.items() if v not in ("", None)}}

    by_address = {}
    by_symbol = defaultdict(list)
    for token in tokens.values():
        key = token_key(token)
        if not key:
            continue
        by_address[key] = token
        symbol = str(token.get("symbol") or "").strip().lower().lstrip("$")
        if symbol:
            by_symbol[symbol].append(token)
    return {"by_address": by_address, "by_symbol": dict(by_symbol)}


def latest_snapshot_tokens() -> list[dict]:
    paths = sorted(glob.glob(str(SNAPSHOTS_DIR / "*.json")))
    if not paths:
        return []
    try:
        data = json.loads(Path(paths[-1]).read_text())
    except Exception:
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def movement_event_tokens(path: Path = MOVEMENT_EVENTS_FILE) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    out = []
    for event in data.get("events", []) if isinstance(data, dict) else []:
        token = event.get("token") if isinstance(event, dict) else {}
        if isinstance(token, dict):
            out.append(token)
    return out


def match_direct_address(text: str, index: dict) -> dict | None:
    for address in BASE_ADDRESS_RE.findall(text):
        key = ("base", _norm(address, "base"))
        if key in index["by_address"]:
            return index["by_address"][key]
    for address in SOLANA_ADDRESS_RE.findall(text):
        key = ("solana", _norm(address, "solana"))
        if key in index["by_address"]:
            return index["by_address"][key]
    return None


def choose_unique_token(candidates: list[dict]) -> dict | None:
    by_key = {}
    for token in candidates:
        key = token_key(token)
        if key:
            by_key[key] = token
    unique = list(by_key.values())
    if len(unique) == 1:
        return unique[0]
    if not unique:
        return None
    ranked = sorted(
        unique,
        key=lambda token: (
            float(token.get("liquidity_usd") or 0),
            float(token.get("volume_24h_usd") or 0),
        ),
        reverse=True,
    )
    if len(ranked) >= 2:
        top_liq = float(ranked[0].get("liquidity_usd") or 0)
        second_liq = float(ranked[1].get("liquidity_usd") or 0)
        if second_liq and top_liq < second_liq * 5:
            return None
    return ranked[0]


def fetch_token_market(token: dict) -> dict | None:
    chain = token.get("chain_slug") or ""
    address = token.get("address") or ""
    if not chain or not address:
        return None
    pairs_by_addr = _ds_batch_fetch_pairs(chain, [address])
    pairs = pairs_by_addr.get(_norm(address, chain), [])
    if chain == "solana":
        full_pairs = _fetch_all_token_pairs_ds(chain, address)
        if full_pairs:
            pairs = full_pairs
    agg = _aggregate_token_pairs(pairs)
    if not agg:
        return None

    h1_values = [_safe_float((pair.get("priceChange") or {}).get("h1")) for pair in pairs]
    h6_values = [_safe_float((pair.get("priceChange") or {}).get("h6")) for pair in pairs]
    return {
        **token,
        **agg,
        "price_change_h1_pct": max(h1_values, key=abs) if h1_values else 0.0,
        "price_change_h6_pct": max(h6_values, key=abs) if h6_values else 0.0,
        "dexscreener_url": f"https://dexscreener.com/{chain}/{address}",
    }


def movement_language_direction(text: str) -> str:
    lowered = text.lower()
    pump = any(contains_term(lowered, term) for term in PUMP_WORDS) or bool(re.search(r"\b\d+(?:\.\d+)?x\b", lowered)) or "%" in lowered
    dump = any(contains_term(lowered, term) for term in DUMP_WORDS)
    if pump and not dump:
        return "pump"
    if dump and not pump:
        return "dump"
    if pump and dump:
        return "unknown"
    return "none"


def contains_term(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    if " " in term:
        return term in text
    return re.search(rf"\b{re.escape(term)}\b", text) is not None


def verified_direction(
    market: dict,
    hint: str,
    pump_h1_pct: float,
    pump_h6_pct: float,
    dump_h1_pct: float,
    dump_h6_pct: float,
) -> str:
    h1 = _safe_float(market.get("price_change_h1_pct"))
    h6 = _safe_float(market.get("price_change_h6_pct"))
    pump = h1 >= pump_h1_pct or h6 >= pump_h6_pct
    dump = h1 <= dump_h1_pct or h6 <= dump_h6_pct
    if hint == "pump" and pump:
        return "pump"
    if hint == "dump" and dump:
        return "dump"
    if hint == "unknown":
        if pump and not dump:
            return "pump"
        if dump and not pump:
            return "dump"
    return ""


def token_quality_failure(
    market: dict,
    *,
    min_market_cap_usd: float,
    min_liquidity_usd: float,
    min_volume_24h_usd: float,
) -> str:
    if _safe_float(market.get("market_cap_usd")) < min_market_cap_usd:
        return "market_cap_below_threshold"
    if _safe_float(market.get("liquidity_usd")) < min_liquidity_usd:
        return "liquidity_below_threshold"
    if _safe_float(market.get("volume_24h_usd")) < min_volume_24h_usd:
        return "volume_below_threshold"
    return ""


def tweet_text(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("text") or "")


def token_key(token: dict) -> tuple[str, str] | None:
    chain = str(token.get("chain_slug") or "").strip().lower()
    address = str(token.get("address") or "").strip()
    if not chain or not address:
        return None
    return (chain, _norm(address, chain))


def _result(
    verified: bool,
    reason: str,
    *,
    text: str,
    direction: str = "",
    token: dict | None = None,
    market: dict | None = None,
) -> dict:
    return {
        "verified": verified,
        "reason": reason,
        "direction": direction,
        "text": text,
        "token": token or {},
        "market": market or {},
        "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
