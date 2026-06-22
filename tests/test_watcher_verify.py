from lib import watcher_verify


def _payload(text):
    return {"data": {"text": text, "id": "1", "author_id": "42"}}


def test_movement_language_direction_detects_pump_and_dump():
    assert watcher_verify.movement_language_direction("$ABC up 25% and breaking out") == "pump"
    assert watcher_verify.movement_language_direction("$ABC exploit, down hard") == "dump"
    assert watcher_verify.movement_language_direction("$ABC normal product update") == "none"


def test_match_token_from_payload_uses_unique_cashtag(monkeypatch):
    monkeypatch.setattr(
        watcher_verify,
        "load_token_index",
        lambda: {
            "by_address": {},
            "by_symbol": {
                "alpha": [
                    {
                        "chain_slug": "base",
                        "address": "0xabc",
                        "symbol": "ALPHA",
                        "liquidity_usd": 100000,
                        "volume_24h_usd": 1000000,
                    }
                ]
            },
        },
    )

    token = watcher_verify.match_token_from_payload(_payload("$ALPHA up 30%"))

    assert token["symbol"] == "ALPHA"
    assert token["address"] == "0xabc"


def test_verify_watcher_hit_requires_price_confirmation(monkeypatch):
    monkeypatch.setattr(
        watcher_verify,
        "match_token_from_payload",
        lambda payload: {"chain_slug": "base", "address": "0xabc", "symbol": "ALPHA"},
    )
    monkeypatch.setattr(
        watcher_verify,
        "fetch_token_market",
        lambda token: {
            **token,
            "market_cap_usd": 2_000_000,
            "liquidity_usd": 100_000,
            "volume_24h_usd": 1_000_000,
            "price_change_h1_pct": 25,
            "price_change_h6_pct": 40,
        },
    )

    result = watcher_verify.verify_watcher_hit(_payload("$ALPHA up 25%"))

    assert result["verified"] is True
    assert result["direction"] == "pump"


def test_verify_watcher_hit_rejects_below_threshold(monkeypatch):
    monkeypatch.setattr(
        watcher_verify,
        "match_token_from_payload",
        lambda payload: {"chain_slug": "base", "address": "0xabc", "symbol": "ALPHA"},
    )
    monkeypatch.setattr(
        watcher_verify,
        "fetch_token_market",
        lambda token: {
            **token,
            "market_cap_usd": 2_000_000,
            "liquidity_usd": 100_000,
            "volume_24h_usd": 1_000_000,
            "price_change_h1_pct": 5,
            "price_change_h6_pct": 10,
        },
    )

    result = watcher_verify.verify_watcher_hit(_payload("$ALPHA up 25%"))

    assert result["verified"] is False
    assert result["reason"] == "price_movement_below_threshold"
