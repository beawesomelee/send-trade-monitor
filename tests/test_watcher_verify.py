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


def test_match_token_from_payload_uses_approved_alias(monkeypatch):
    monkeypatch.setattr(
        watcher_verify,
        "load_token_index",
        lambda: {
            "by_address": {},
            "by_symbol": {},
            "by_alias": {
                "opengradient": [
                    {
                        "chain_slug": "base",
                        "address": "0xopg",
                        "symbol": "OPG",
                        "liquidity_usd": 100000,
                        "volume_24h_usd": 1000000,
                    }
                ]
            },
        },
    )

    token = watcher_verify.match_token_from_payload(_payload("OpenGradient is running hard"))

    assert token["symbol"] == "OPG"
    assert token["address"] == "0xopg"


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


def test_verify_watcher_hit_uses_official_account_token_without_movement_language(monkeypatch):
    monkeypatch.setattr(
        watcher_verify,
        "official_token_for_payload",
        lambda payload: {
            "chain_slug": "base",
            "address": "0xvelvet",
            "symbol": "VELVET",
            "account_type": "official_token_account",
            "source_account": "@velvet_capital",
        },
    )
    monkeypatch.setattr(
        watcher_verify,
        "fetch_token_market",
        lambda token: {
            **token,
            "market_cap_usd": 290_000_000,
            "liquidity_usd": 4_500_000,
            "volume_h1_usd": 193_000,
            "volume_h6_usd": 1_300_000,
            "volume_24h_usd": 3_100_000,
            "price_change_h1_pct": 22.1,
            "price_change_h6_pct": 29.3,
        },
    )

    result = watcher_verify.verify_watcher_hit(
        _payload("chain abstraction now lets traders move across chains")
    )

    assert result["verified"] is True
    assert result["direction"] == "pump"
    assert result["account_type"] == "official_token_account"
    assert result["score"] == 89.5
    assert result["token"]["symbol"] == "VELVET"


def test_low_liquidity_score_uses_h24_move_and_turnover():
    market = {
        "market_cap_usd": 2_000_000,
        "liquidity_usd": 60_000,
        "volume_h1_usd": 75_000,
        "volume_h6_usd": 300_000,
        "volume_24h_usd": 900_000,
        "price_change_h1_pct": 25,
        "price_change_h6_pct": 80,
        "price_change_h24_pct": 650,
    }

    score = watcher_verify.watcher_score(market, "pump")
    signal = watcher_verify.price_reliability(market)

    assert score >= 70
    assert signal["priceReliability"] == "medium"
    assert signal["trendState"] == "early_momentum"
    assert signal["volumeToLiquidity"] == 15.0


def test_extreme_low_liquidity_reset_is_watch_only():
    market = {
        "market_cap_usd": 2_000_000,
        "liquidity_usd": 55_000,
        "volume_24h_usd": 3_300_000,
        "price_change_h1_pct": -100,
        "price_change_h6_pct": -100,
        "price_change_h24_pct": 357_577,
    }

    result = watcher_verify.price_reliability(market)

    assert result["priceReliability"] == "low"
    assert result["trendState"] == "volatile_price_reset"
    assert result["watchOnly"] is True


def test_verified_result_carries_signal_metadata():
    market = {
        "market_cap_usd": 2_000_000,
        "liquidity_usd": 55_000,
        "volume_24h_usd": 3_300_000,
        "price_change_h1_pct": -100,
        "price_change_h6_pct": -100,
        "price_change_h24_pct": 357_577,
    }

    result = watcher_verify._result(
        True,
        "verified_price_movement",
        text="$ALPHA update",
        token={"symbol": "ALPHA"},
        market=market,
        direction="pump",
    )

    assert result["priceReliability"] == "low"
    assert result["trendState"] == "volatile_price_reset"
    assert result["watchOnly"] is True
    assert result["market"]["priceReliability"] == "low"
