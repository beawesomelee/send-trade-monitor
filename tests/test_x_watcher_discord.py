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
    result = x_watcher.stream_watcher_posts(
        "token",
        tweets_path=tweets_path,
        state_path=state_path,
        lock_path=lock_path,
        read_timeout=1,
        **kwargs,
    )
    return result, tweets_path, state_path


def test_unique_watcher_tweet_posts_discord_once_after_storage(tmp_path, monkeypatch):
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
    assert len(sent) == 1
    assert sent[0]["allowed_mentions"] == {"parse": []}
    content = sent[0]["content"]
    assert "Raw X watcher hit" in content
    assert "not yet AI-classified" in content
    assert "alpha watcher says @everyone buy the dip @here" in content
    assert "https://x.com/alice/status/100" in content
    assert "alpha" in content
    assert "Alice Example (@alice)" in content
    assert "2026-06-10T08:01:00Z" in content
    assert tweets_path.exists()
    assert json.loads(tweets_path.read_text().splitlines()[0])["tweet_id"] == "100"
    assert json.loads(state_path.read_text())["last_seen_tweet_id"] == "100"


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
    assert result["skipped_duplicates"] == 1
    assert len(sent) == 2
    assert "status/100" in sent[0]
    assert "duplicate" not in "\n".join(sent)
    assert "status/101" in sent[1]
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


def test_discord_can_be_disabled_for_local_runs(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.invalid/webhook")
    monkeypatch.setattr(discord_module.requests, "post", lambda *args, **kwargs: sent.append(args))

    result, tweets_path, _state_path = _run_stream(
        tmp_path,
        monkeypatch,
        [_payload("300")],
        max_posts=1,
        discord=False,
    )

    assert result["stored"] == 1
    assert tweets_path.exists()
    assert sent == []
