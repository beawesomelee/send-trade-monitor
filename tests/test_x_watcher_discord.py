import json
from pathlib import Path

from lib import discord as discord_module
from lib import x_watcher


def _payload(tweet_id, text="send it", rules=None, username="alice"):
    return {
        "data": {
            "id": str(tweet_id),
            "text": text,
            "author_id": "42",
            "created_at": "2026-06-10T08:00:00.000Z",
        },
        "includes": {
            "users": [
                {
                    "id": "42",
                    "username": username,
                    "name": "Alice Example",
                }
            ]
        },
        "matching_rules": rules or [{"id": "r1", "tag": "alpha"}],
    }


def _run_stream(tmp_path, monkeypatch, payloads, **kwargs):
    monkeypatch.setattr(x_watcher, "iter_stream_payloads", lambda *args, **kw: iter(payloads))
    monkeypatch.setattr(x_watcher, "_now_iso", lambda: "2026-06-10T08:01:00Z")
    tweets_path = tmp_path / "watcher_tweets.jsonl"
    state_path = tmp_path / "watcher_ingest_state.json"
    lock_path = tmp_path / "watcher_stream.lock"
    kwargs.setdefault("outcomes_path", tmp_path / "watcher_outcomes.json")
    result = x_watcher.stream_watcher_posts(
        "token",
        tweets_path=tweets_path,
        state_path=state_path,
        lock_path=lock_path,
        read_timeout=1,
        **kwargs,
    )
    return result, tweets_path, state_path


def test_unique_watcher_tweet_stores_without_discord_when_unverified(tmp_path, monkeypatch):
    sent = []

    def fake_post(webhook, json, timeout):
        sent.append(json)

        class Response:
            status_code = 204
            text = ""

        return Response()

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(discord_module.requests, "post", fake_post)

    result, tweets_path, state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("100", text="alpha watcher says @everyone buy the dip @here")],
        max_posts=1,
    )

    assert result["stored"] == 1
    assert result["verified"] == 0
    assert result["unverified"] == 1
    assert sent == []
    assert tweets_path.exists()
    assert json.loads(tweets_path.read_text().splitlines()[0])["tweet_id"] == "100"
    assert json.loads(state_path.read_text())["last_seen_tweet_id"] == "100"


def test_verified_watcher_tweet_posts_discord_after_storage(tmp_path, monkeypatch):
    sent = []

    def fake_post(webhook, json, timeout):
        sent.append(json)

        class Response:
            status_code = 204
            text = ""

        return Response()

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(discord_module.requests, "post", fake_post)
    monkeypatch.setattr(
        x_watcher,
        "verify_watcher_hit",
        lambda payload: {
            "verified": True,
            "reason": "verified_price_movement",
            "direction": "pump",
            "token": {"symbol": "ALPHA", "dexscreener_url": "https://dexscreener.com/base/0x1"},
            "market": {
                "symbol": "ALPHA",
                "price_change_h1_pct": 25,
                "price_change_h6_pct": 40,
                "dexscreener_url": "https://dexscreener.com/base/0x1",
            },
        },
    )

    result, tweets_path, state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("100", text="$ALPHA up 25%")],
        max_posts=1,
    )

    assert result["stored"] == 1
    assert result["verified"] == 1
    assert len(sent) == 1
    assert sent[0]["allowed_mentions"] == {"parse": []}
    content = sent[0]["content"]
    assert "Verified X watcher hit" in content
    assert "**ALPHA**" in content
    assert "h1 `+25.0%`" in content
    assert "https://x.com/alice/status/100" in content
    assert tweets_path.exists()
    assert json.loads(state_path.read_text())["last_seen_tweet_id"] == "100"
    outcome = json.loads((tmp_path / "watcher_outcomes.json").read_text())["outcomes"][0]
    assert outcome["tweet_id"] == "100"
    assert outcome["account"] == "@alice"
    assert outcome["token"]["symbol"] == "ALPHA"
    assert outcome["market_at_ingest"]["price_change_h1_pct"] == 25.0


def test_verified_watcher_tweet_records_outcome_without_discord(tmp_path, monkeypatch):
    monkeypatch.setattr(
        x_watcher,
        "verify_watcher_hit",
        lambda payload: {
            "verified": True,
            "reason": "verified_price_movement",
            "direction": "pump",
            "market": {
                "symbol": "ALPHA",
                "address": "0x1",
                "chain_slug": "base",
                "price_change_h1_pct": 25,
                "price_change_h6_pct": 40,
                "liquidity_usd": 1_500_000,
                "market_cap_usd": 25_000_000,
                "volume_24h_usd": 2_000_000,
                "dexscreener_url": "https://dexscreener.com/base/0x1",
            },
        },
    )

    result, _tweets_path, _state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("101", text="$ALPHA up 25%")],
        discord=False,
        max_posts=1,
    )

    assert result["verified"] == 1
    outcome = json.loads((tmp_path / "watcher_outcomes.json").read_text())["outcomes"][0]
    assert outcome["tweet_id"] == "101"
    assert outcome["token"]["address"] == "0x1"
    assert outcome["market_at_ingest"]["liquidity_usd"] == 1_500_000.0


def test_duplicate_tweet_id_is_stored_and_posted_only_once(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(
        discord_module.requests,
        "post",
        lambda webhook, json, timeout: sent.append(json["content"])
        or type("Response", (), {"status_code": 204, "text": ""})(),
    )

    result, tweets_path, _state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("100"), _payload("100", text="duplicate"), _payload("101")],
        max_posts=2,
    )

    assert result["stored"] == 2
    assert result["verified"] == 0
    assert result["skipped_duplicates"] == 1
    assert sent == []
    assert len(tweets_path.read_text().splitlines()) == 2


def test_missing_discord_webhook_does_not_fail_ingest(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    result, tweets_path, state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("200")],
        max_posts=1,
    )

    assert result["stored"] == 1
    assert tweets_path.exists()
    assert json.loads(state_path.read_text())["last_seen_tweet_id"] == "200"


def test_discord_post_error_does_not_fail_ingest(tmp_path, monkeypatch):
    def fail_post(*args, **kwargs):
        raise discord_module.requests.exceptions.RequestException("discord down")

    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(discord_module.requests, "post", fail_post)

    result, tweets_path, state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("250")],
        max_posts=1,
    )

    assert result["stored"] == 1
    assert result["connection_errors"] == 0
    assert tweets_path.exists()
    assert json.loads(state_path.read_text())["last_seen_tweet_id"] == "250"


def test_raw_discord_can_be_enabled_for_debug_runs(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(discord_module.requests, "post", lambda webhook, json, timeout: sent.append(json["content"]) or type("Response", (), {"status_code": 204, "text": ""})())

    result, tweets_path, _state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("300")],
        max_posts=1,
        raw_discord=True,
        verify_hits=False,
    )

    assert result["stored"] == 1
    assert tweets_path.exists()
    assert len(sent) == 1
    assert "Raw X watcher hit" in sent[0]
