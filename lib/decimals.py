"""Fetch on-chain decimals for Base (EVM) and Solana (SPL) tokens.

Uses public RPC endpoints (free, no auth):
  - Base:   https://mainnet.base.org
  - Solana: https://api.mainnet-beta.solana.com

If public RPCs start rate-limiting or returning errors, you can add a
provider-specific path here (Alchemy for Base, Helius for Solana) and read
the API key from env. This file used to have those paths but they were
dropped during cleanup since the public endpoints handle our volume
(~1-30 lookups per scan, mostly cached) just fine.
"""

import time

import requests

ERC20_DECIMALS_SIG = "0x313ce567"
MAX_RETRIES = 3

KNOWN_DECIMALS = {
    "0x0000000000000000000000000000000000000000": 18,  # native ETH placeholder
    "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": 18,  # native ETH sentinel
    "0x4200000000000000000000000000000000000006": 18,  # WETH on Base
}


def get_decimals(address: str, chain_slug: str) -> int | None:
    """Fetch decimals for a token with retries. Returns None on failure."""
    addr_lower = address.lower()
    if addr_lower in KNOWN_DECIMALS:
        return KNOWN_DECIMALS[addr_lower]

    for attempt in range(MAX_RETRIES):
        try:
            if chain_slug == "base":
                result = _base_decimals_public(address)
            elif chain_slug == "solana":
                result = _solana_decimals_public(address)
            else:
                return None
            if result is not None:
                return result
        except Exception:
            pass
        if attempt < MAX_RETRIES - 1:
            time.sleep(1 * (attempt + 1))
    return None


def _base_decimals_public(address: str) -> int | None:
    url = "https://mainnet.base.org"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": address, "data": ERC20_DECIMALS_SIG}, "latest"],
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        result = r.json().get("result", "0x")
        if result and result != "0x":
            return int(result, 16)
    except Exception:
        pass
    return None


def _solana_decimals_public(address: str) -> int | None:
    url = "https://api.mainnet-beta.solana.com"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [address, {"encoding": "jsonParsed"}],
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        result = r.json().get("result", {})
        value = result.get("value", {})
        data = value.get("data", {})
        if isinstance(data, dict):
            parsed = data.get("parsed", {})
            info = parsed.get("info", {})
            return info.get("decimals")
    except Exception:
        pass
    return None
