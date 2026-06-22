import json

from lib.watcher_review import (
    build_pre_move_candidates,
    build_pre_move_candidates_from_events,
    upsert_review_candidates,
)


def test_build_pre_move_candidates_uses_estimated_start(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "events": [
            _event(
                estimated_start_at="2026-06-10T12:00:00Z",
                evidence=[
                    _evidence("@Alpha", "2026-06-10T11:00:00Z", "https://x.com/Alpha/status/1"),
                    _evidence("@Late", "2026-06-10T12:30:00Z", "https://x.com/Late/status/2"),
                ],
            )
        ],
    }))
    watcher_path.write_text(json.dumps({
        "watch_accounts": {
            "@alpha": {"status": "pending", "terms": ["ALPHA", "$ALPHA", "pump"]},
            "@late": {"status": "pending", "terms": ["LATE"]},
        }
    }))

    candidates = build_pre_move_candidates(events_path=events_path, watcher_path=watcher_path)

    assert [candidate["account"] for candidate in candidates] == ["@alpha"]
    assert candidates[0]["pre_move_cutoff_source"] == "estimated_start_at"
    assert candidates[0]["minutes_before_cutoff"] == 60
    assert candidates[0]["terms"] == ["alpha", "$alpha"]


def test_build_pre_move_candidates_falls_back_to_window_start(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "events": [
            _event(
                estimated_start_at="",
                evidence=[
                    _evidence("@Early", "2026-06-10T05:30:00Z", "https://x.com/Early/status/1"),
                    _evidence("@During", "2026-06-10T06:30:00Z", "https://x.com/During/status/2"),
                ],
            )
        ],
    }))
    watcher_path.write_text(json.dumps({"watch_accounts": {}}))

    candidates = build_pre_move_candidates(events_path=events_path, watcher_path=watcher_path)

    assert [candidate["account"] for candidate in candidates] == ["@early"]
    assert candidates[0]["pre_move_cutoff_source"] == "window_start_at"


def test_build_pre_move_candidates_applies_max_lookback(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "events": [
            _event(
                estimated_start_at="2026-06-10T12:00:00Z",
                evidence=[
                    _evidence("@TooOld", "2026-06-08T11:00:00Z", "https://x.com/TooOld/status/1"),
                    _evidence("@Timely", "2026-06-10T11:00:00Z", "https://x.com/Timely/status/2"),
                ],
            )
        ],
    }))
    watcher_path.write_text(json.dumps({"watch_accounts": {}}))

    candidates = build_pre_move_candidates(
        events_path=events_path,
        watcher_path=watcher_path,
        max_lookback_hours=24,
    )

    assert [candidate["account"] for candidate in candidates] == ["@timely"]


def test_build_pre_move_candidates_from_events_uses_supplied_events_only():
    events = [
        _event(
            estimated_start_at="2026-06-10T12:00:00Z",
            evidence=[
                _evidence("@Timely", "2026-06-10T11:00:00Z", "https://x.com/Timely/status/2"),
            ],
        )
    ]

    candidates = build_pre_move_candidates_from_events(events, watch_accounts={})

    assert len(candidates) == 1
    assert candidates[0]["account"] == "@timely"


def test_upsert_review_candidates_writes_compact_rows(tmp_path):
    review_path = tmp_path / "watcher_review.json"
    candidate = {
        "candidate_id": "watch_candidate_abc",
        "account": "@timely",
        "token_symbol": "ALPHA",
        "token_address": "0xalpha",
        "chain_slug": "base",
        "event_id": "signal_test",
        "source_signal_id": "signal_test",
        "movement_direction": "pump",
        "movement_change_pct": 80,
        "tweet_at": "2026-06-10T11:00:00Z",
        "pre_move_cutoff_at": "2026-06-10T12:00:00Z",
        "source_evidence_url": "https://x.com/Timely/status/2",
        "tweet_text": "x" * 400,
        "terms": ["alpha", "$alpha"],
        "grok": {
            "label": "maybe_alpha",
            "implied_direction": "pump",
            "reason": "specific catalyst before move",
            "suggested_terms": ["alpha"],
        },
        "recommendation": "manual_review",
    }

    assert upsert_review_candidates([candidate], path=review_path) == 1
    assert upsert_review_candidates([candidate], path=review_path) == 1
    data = json.loads(review_path.read_text())

    assert data["candidate_count"] == 1
    row = data["candidates"][0]
    assert row["candidate_id"] == "watch_candidate_abc"
    assert row["grok_label"] == "maybe_alpha"
    assert len(row["tweet_text"]) <= 280


def _event(*, estimated_start_at: str, evidence: list[dict]) -> dict:
    return {
        "event_id": "signal_test",
        "source_signal_id": "signal_test",
        "detected_at": "2026-06-10T12:00:00Z",
        "token": {
            "symbol": "ALPHA",
            "address": "0xalpha",
            "chain_slug": "base",
        },
        "movement": {
            "direction": "pump",
            "change_pct": 80,
            "window": "h6",
            "window_start_at": "2026-06-10T06:00:00Z",
            "window_end_at": "2026-06-10T12:00:00Z",
            "estimated_start_at": estimated_start_at,
        },
        "evidence": evidence,
        "watcher_clues": {
            "accounts": [],
            "keywords": ["ALPHA"],
            "phrases": ["alpha launch"],
            "catalysts": [],
        },
    }


def _evidence(author: str, tweet_at: str, url: str) -> dict:
    return {
        "author_handle": author,
        "tweet_at": tweet_at,
        "url": url,
        "text": "alpha launch teased",
        "relevance": "pre-move catalyst",
    }
