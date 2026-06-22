"""Estimate movement start times from GeckoTerminal/CoinGecko OHLCV candles."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from lib.dexscreener import NETWORK_MAP
from lib.movement_events import isoformat_z, parse_iso


PRO_BASE_URL = "https://pro-api.coingecko.com/api/v3/onchain"
PUBLIC_BASE_URL = "https://api.geckoterminal.com/api/v2"
DEFAULT_THRESHOLD_PCT = 15.0


def enrich_events_with_estimated_starts(
    events: list[dict],
    *,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    aggregate: int = 5,
    limit: int = 200,
    sleep_seconds: float = 1.5,
) -> tuple[list[dict], dict]:
    """Return events enriched with estimated movement starts."""
    enriched = []
    stats = {
        "events": len(events),
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }

    for event in events:
        result = enrich_event_with_estimated_start(
            event,
            threshold_pct=threshold_pct,
            aggregate=aggregate,
            limit=limit,
        )
        enriched.append(result["event"])
        stats[result["status"]] = stats.get(result["status"], 0) + 1
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return enriched, stats


def enrich_event_with_estimated_start(
    event: dict,
    *,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    aggregate: int = 5,
    limit: int = 200,
) -> dict:
    """Fetch candles for one event and add estimated movement fields."""
    out = dict(event)
    movement = dict(out.get("movement") if isinstance(out.get("movement"), dict) else {})
    token = out.get("token") if isinstance(out.get("token"), dict) else {}
    chain_slug = token.get("chain_slug") or ""
    token_address = token.get("address") or ""
    detected_at = parse_iso(movement.get("detected_at") or out.get("detected_at"))
    window_start = parse_iso(movement.get("window_start_at"))
    direction = movement.get("direction") or ""

    if not chain_slug or not token_address or detected_at is None or window_start is None:
        movement.update(_empty_estimate("missing_event_inputs"))
        out["movement"] = movement
        return {"status": "skipped", "event": out}

    try:
        pool = fetch_top_pool(chain_slug, token_address)
        if not pool:
            movement.update(_empty_estimate("no_pool_found"))
            out["movement"] = movement
            return {"status": "skipped", "event": out}

        candles = fetch_pool_ohlcv(
            pool["network"],
            pool["pool_address"],
            before=detected_at,
            aggregate=aggregate,
            limit=limit,
        )
        estimate = estimate_movement_start(
            candles,
            direction=direction,
            window_start=window_start,
            detected_at=detected_at,
            threshold_pct=threshold_pct,
        )
    except Exception as exc:
        movement.update(_empty_estimate(f"error: {str(exc)[:160]}"))
        out["movement"] = movement
        return {"status": "errors", "event": out}

    movement.update({
        "pool_address": pool["pool_address"],
        "pool_name": pool.get("pool_name") or "",
        "pool_network": pool["network"],
        "estimated_start_at": estimate.get("start_at") or "",
        "estimated_peak_at": estimate.get("peak_at") or "",
        "estimated_trough_at": estimate.get("trough_at") or "",
        "estimated_start_method": estimate.get("method") or "",
        "estimated_start_threshold_pct": threshold_pct,
        "estimated_start_confidence": estimate.get("confidence"),
        "estimated_start_reason": estimate.get("reason") or "",
        "ohlcv_candle_count": len(candles),
    })
    out["movement"] = movement
    return {"status": "updated" if estimate.get("start_at") else "skipped", "event": out}


def fetch_top_pool(chain_slug: str, token_address: str) -> dict | None:
    """Fetch the top GeckoTerminal/CoinGecko pool for a token."""
    network = NETWORK_MAP.get(chain_slug, {}).get("gt_network", chain_slug)
    base_url, headers = _onchain_base()
    payload = _request_json(
        f"{base_url}/networks/{network}/tokens/{token_address}/pools",
        headers=headers,
        params={"page": 1},
        timeout=20,
    )
    pools = payload.get("data") or []
    if not pools:
        return None

    pool = pools[0]
    attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
    return {
        "network": network,
        "pool_address": _pool_address(pool),
        "pool_name": attrs.get("name") or "",
    }


def fetch_pool_ohlcv(
    network: str,
    pool_address: str,
    *,
    before: datetime,
    aggregate: int = 5,
    limit: int = 200,
) -> list[dict]:
    """Fetch 5m-ish OHLCV candles for a pool before a timestamp."""
    base_url, headers = _onchain_base()
    payload = _request_json(
        f"{base_url}/networks/{network}/pools/{pool_address}/ohlcv/minute",
        headers=headers,
        params={
            "aggregate": aggregate,
            "limit": limit,
            "currency": "usd",
            "before_timestamp": int(before.timestamp()) + 60,
            "include_empty_intervals": "true",
        },
        timeout=30,
    )
    rows = (
        payload
        .get("data", {})
        .get("attributes", {})
        .get("ohlcv_list", [])
    )
    candles = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            candles.append({
                "timestamp": datetime.fromtimestamp(float(row[0]), tz=timezone.utc),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return sorted(candles, key=lambda candle: candle["timestamp"])


def estimate_movement_start(
    candles: list[dict],
    *,
    direction: str,
    window_start: datetime,
    detected_at: datetime,
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
) -> dict:
    """Estimate pump/dump start using local extreme plus threshold crossing."""
    window_candles = [
        candle for candle in candles
        if window_start <= candle["timestamp"] <= detected_at
    ]
    if len(window_candles) < 2:
        return _estimate("", "not_enough_candles")

    direction = str(direction or "").lower()
    if direction == "pump":
        return _estimate_pump(window_candles, threshold_pct)
    if direction == "dump":
        return _estimate_dump(window_candles, threshold_pct)
    return _estimate("", "unsupported_direction")


def _estimate_pump(candles: list[dict], threshold_pct: float) -> dict:
    low_idx, low = min(enumerate(candles), key=lambda item: item[1]["close"])
    threshold = low["close"] * (1 + threshold_pct / 100)
    later = candles[low_idx:]
    peak = max(later, key=lambda candle: candle["close"])
    start = next((candle for candle in later if candle["close"] >= threshold), None)
    if not start:
        return _estimate("", "threshold_not_crossed", peak_at=isoformat_z(peak["timestamp"]))
    return _estimate(
        isoformat_z(start["timestamp"]),
        "local_low_threshold_cross",
        peak_at=isoformat_z(peak["timestamp"]),
        confidence=0.65,
    )


def _estimate_dump(candles: list[dict], threshold_pct: float) -> dict:
    high_idx, high = max(enumerate(candles), key=lambda item: item[1]["close"])
    threshold = high["close"] * (1 - threshold_pct / 100)
    later = candles[high_idx:]
    trough = min(later, key=lambda candle: candle["close"])
    start = next((candle for candle in later if candle["close"] <= threshold), None)
    if not start:
        return _estimate("", "threshold_not_crossed", trough_at=isoformat_z(trough["timestamp"]))
    return _estimate(
        isoformat_z(start["timestamp"]),
        "local_high_threshold_cross",
        trough_at=isoformat_z(trough["timestamp"]),
        confidence=0.65,
    )


def _estimate(
    start_at: str,
    reason: str,
    *,
    peak_at: str = "",
    trough_at: str = "",
    confidence: float | None = None,
) -> dict:
    return {
        "start_at": start_at,
        "peak_at": peak_at,
        "trough_at": trough_at,
        "method": "ohlcv_local_extreme_15pct" if start_at else "",
        "confidence": confidence,
        "reason": reason,
    }


def _empty_estimate(reason: str) -> dict:
    return {
        "estimated_start_at": "",
        "estimated_peak_at": "",
        "estimated_trough_at": "",
        "estimated_start_method": "",
        "estimated_start_threshold_pct": None,
        "estimated_start_confidence": None,
        "estimated_start_reason": reason,
    }


def _pool_address(pool: dict) -> str:
    raw_id = str(pool.get("id") or "")
    if "_" in raw_id:
        return raw_id.split("_", 1)[1]
    attrs = pool.get("attributes") if isinstance(pool.get("attributes"), dict) else {}
    return attrs.get("address") or raw_id


def _onchain_base() -> tuple[str, dict]:
    key = os.environ.get("GECKOTERMINAL_API_KEY") or os.environ.get("COINGECKO_API_KEY")
    if key:
        return PRO_BASE_URL, {"x-cg-pro-api-key": key}
    return PUBLIC_BASE_URL, {}


def _request_json(
    url: str,
    *,
    headers: dict,
    params: dict,
    timeout: int,
    max_attempts: int = 4,
) -> dict:
    """Request JSON with basic backoff for public API rate limits."""
    last_response = None
    for attempt in range(1, max_attempts + 1):
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        last_response = response
        if response.status_code != 429:
            response.raise_for_status()
            return response.json()

        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = float(retry_after) if retry_after else 2**attempt
        except ValueError:
            wait_seconds = 2**attempt
        time.sleep(min(wait_seconds, 30))

    assert last_response is not None
    last_response.raise_for_status()
    return {}
