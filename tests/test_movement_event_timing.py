from lib.movement_events import (
    build_event_from_signal,
    movement_window_start,
    parse_iso,
    timing_bucket,
)
from lib.x_time import isoformat_z, tweet_id_from_url, tweet_time_from_id


def test_tweet_time_from_x_snowflake_id():
    tweet_id = "2064469984655339864"

    assert tweet_id_from_url(f"https://x.com/CryptoJunzy/status/{tweet_id}") == tweet_id
    assert isoformat_z(tweet_time_from_id(tweet_id)) == "2026-06-09T22:09:34Z"


def test_timing_bucket_uses_detected_window():
    detected_at = parse_iso("2026-06-09T22:09:57Z")
    window_start = movement_window_start(detected_at, "h6")

    assert timing_bucket(parse_iso("2026-06-09T15:00:00Z"), window_start, detected_at) == "pre_window"
    assert timing_bucket(parse_iso("2026-06-09T20:00:00Z"), window_start, detected_at) == "during_window"
    assert timing_bucket(parse_iso("2026-06-09T23:00:00Z"), window_start, detected_at) == "post_detection"
    assert timing_bucket(None, window_start, detected_at) == "unknown"


def test_build_event_from_signal_adds_reference_timing():
    signal = {
        "id": "signal_test",
        "detected_at": "2026-06-09T22:09:57Z",
        "token": {"symbol": "Jotchua", "address": "abc"},
        "movement": {"direction": "pump", "window": "h6", "change_pct": 83.3},
        "references": [
            {
                "author_handle": "@CryptoJunzy",
                "type": "x_post",
                "text": "$Jotchua update",
                "url": "https://x.com/CryptoJunzy/status/2064469984655339864",
            }
        ],
        "watcher_clues": {
            "accounts": ["@CryptoJunzy"],
            "keywords": ["Jotchua"],
            "phrases": [],
            "catalysts": [],
        },
        "links": {"dexscreener": "https://dexscreener.com/solana/abc"},
    }

    event = build_event_from_signal(signal)

    assert event["movement"]["window_start_at"] == "2026-06-09T16:09:57Z"
    assert event["evidence"][0]["tweet_at"] == "2026-06-09T22:09:34Z"
    assert event["evidence"][0]["timing_bucket"] == "during_window"
    assert event["candidate_watch_rules"][0]["account"] == "@cryptojunzy"
