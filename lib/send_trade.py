"""Fetch Send.Trade verified asset list and match against candidates."""

import requests

CHAIN_ID_MAP = {
    "base": 8453,
    "solana": 501474,
}


def fetch_verified(endpoint: str) -> set[tuple]:
    """Return a set of (chain_id, lowercase_address) for all verified assets."""
    r = requests.get(endpoint, timeout=15)
    r.raise_for_status()
    data = r.json()

    assets = data if isinstance(data, list) else data.get("assets", data.get("data", []))

    verified = set()
    for a in assets:
        chain_id = a.get("chainId")
        addr = (a.get("address") or "").lower()
        if chain_id and addr:
            verified.add((chain_id, addr))
    return verified


def is_verified(candidate: dict, verified_set: set[tuple]) -> bool:
    """Check if a candidate token is in the verified set."""
    chain_id = candidate["chain_id"]
    addr = candidate["address"].lower()
    return (chain_id, addr) in verified_set
