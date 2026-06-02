"""Fetch Send.Trade verified asset list and match against candidates.

Address case rules:
- Base (EVM, chain_id 8453): hex addresses are case-insensitive on chain, so
  we lowercase for comparison.
- Solana (chain_id 501474): base58 addresses are CASE-SENSITIVE. Lowercasing
  them breaks matching. We preserve case.
"""

import requests

CHAIN_ID_MAP = {
    "base": 8453,
    "solana": 501474,
}

SOLANA_CHAIN_ID = 501474


def _norm_addr(chain_id, address: str) -> str:
    """Normalize an address for set-membership comparison.

    Solana base58 addresses keep their case; everything else (Base / EVM)
    gets lowercased.
    """
    if not address:
        return ""
    try:
        cid = int(chain_id)
    except (TypeError, ValueError):
        cid = chain_id
    if cid == SOLANA_CHAIN_ID:
        return address
    return address.lower()


def fetch_verified(endpoint: str) -> set[tuple]:
    """Return a set of (chain_id, normalized_address) for all verified assets."""
    r = requests.get(endpoint, timeout=15)
    r.raise_for_status()
    data = r.json()

    assets = data if isinstance(data, list) else data.get("assets", data.get("data", []))

    verified = set()
    for a in assets:
        chain_id = a.get("chainId")
        addr = a.get("address") or ""
        if chain_id and addr:
            verified.add((chain_id, _norm_addr(chain_id, addr)))
    return verified


def is_verified(candidate: dict, verified_set: set[tuple]) -> bool:
    """Check if a candidate token is in the verified set."""
    chain_id = candidate["chain_id"]
    addr = _norm_addr(chain_id, candidate["address"])
    return (chain_id, addr) in verified_set
