import json

from lib.watcher_approval import (
    APPROVED_ALPHA_SOURCE,
    APPROVED_MOVEMENT_CHATTER,
    REJECTED_NO_APPROVED_TERMS,
    REJECTED_LATE_REACTION,
    REJECTED_NO_TWEET_EVIDENCE,
    apply_watcher_approvals,
    label_event,
)


def _event(tweet_at: str, *, account: str = "@Alpha", estimated_start: str = "2026-06-10T12:00:00Z"):
    return {
        "event_id": "signal_alpha",
        "source_signal_id": "signal_alpha",
        "detected_at": "2026-06-10T13:00:00Z",
        "token": {"symbol": "ALPHA", "address": "0xalpha", "chain_slug": "base"},
        "movement": {
            "direction": "pump",
            "window": "h6",
            "change_pct": 70,
            "estimated_start_at": estimated_start,
        },
        "evidence": [
            {
                "author_handle": account,
                "text": "$ALPHA looks ready",
                "tweet_at": tweet_at,
                "url": "https://x.com/Alpha/status/1",
            }
        ],
        "watcher_clues": {
            "accounts": [account],
            "keywords": ["alpha"],
            "phrases": ["generic narrative"],
            "catalysts": ["kol_post"],
        },
        "candidate_watch_rules": [
            {
                "account": account,
                "terms": ["alpha", "$alpha", "generic narrative", "kol post"],
                "source_evidence_urls": ["https://x.com/Alpha/status/1"],
                "timing_bucket": "during_window",
            }
        ],
    }


def test_label_event_approves_pre_start_tweet():
    event = label_event(_event("2026-06-10T11:45:00Z"))

    candidate = event["candidate_watch_rules"][0]

    assert candidate["approval_label"] == APPROVED_ALPHA_SOURCE
    assert candidate["approval_status"] == "approved"
    assert candidate["minutes_from_estimated_start"] == -15.0
    assert candidate["approved_terms"] == ["$alpha"]


def test_label_event_approves_early_movement_chatter():
    event = label_event(_event("2026-06-10T12:20:00Z"))

    candidate = event["candidate_watch_rules"][0]

    assert candidate["approval_label"] == APPROVED_MOVEMENT_CHATTER
    assert candidate["approval_status"] == "approved"


def test_label_event_rejects_late_reaction():
    event = label_event(_event("2026-06-10T12:45:00Z"))

    candidate = event["candidate_watch_rules"][0]

    assert candidate["approval_label"] == REJECTED_LATE_REACTION
    assert candidate["approval_status"] == "rejected"
    assert candidate["approved_terms"] == []


def test_label_event_rejects_candidate_without_direct_tweet():
    event = _event("2026-06-10T11:45:00Z")
    event["candidate_watch_rules"].append({
        "account": "@MentionedOnly",
        "terms": ["alpha", "$alpha"],
        "source_evidence_urls": [],
    })

    labeled = label_event(event)

    assert labeled["candidate_watch_rules"][1]["approval_label"] == REJECTED_NO_TWEET_EVIDENCE


def test_label_event_rejects_timely_tweet_without_grounded_terms():
    event = _event("2026-06-10T11:45:00Z", account="@RandomCaller")
    event["evidence"][0]["text"] = "generic market looks ready"
    event["candidate_watch_rules"][0]["account"] = "@RandomCaller"

    labeled = label_event(event)

    assert labeled["candidate_watch_rules"][0]["approval_label"] == REJECTED_NO_APPROVED_TERMS
    assert labeled["candidate_watch_rules"][0]["approval_status"] == "rejected"


def test_apply_watcher_approvals_updates_watcher_statuses(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "updated_at": "",
        "events": [_event("2026-06-10T11:45:00Z")],
    }))
    watcher_path.write_text(json.dumps({
        "schema_version": "watcher_state_v1",
        "updated_at": "",
        "signals": [],
        "rules": [],
        "watch_accounts": {
            "@alpha": {
                "status": "pending",
                "terms": ["alpha", "$alpha", "kol post"],
                "source_signal_ids": ["signal_alpha"],
            }
        },
    }))

    result = apply_watcher_approvals(
        events_path=events_path,
        watcher_path=watcher_path,
        apply=True,
    )
    watcher = json.loads(watcher_path.read_text())

    assert result["watch_accounts_approved"] == 1
    assert watcher["watch_accounts"]["@alpha"]["status"] == "approved"
    assert watcher["watch_accounts"]["@alpha"]["terms"] == ["$alpha"]


def test_apply_watcher_approvals_does_not_downgrade_existing_approved_account(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    late_event = _event("2026-06-10T12:45:00Z")
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "updated_at": "",
        "events": [late_event],
    }))
    watcher_path.write_text(json.dumps({
        "schema_version": "watcher_state_v1",
        "updated_at": "",
        "signals": [],
        "rules": [],
        "watch_accounts": {
            "@alpha": {
                "status": "approved",
                "terms": ["$alpha", "curated phrase"],
                "source_signal_ids": ["signal_alpha"],
            }
        },
    }))

    apply_watcher_approvals(
        events_path=events_path,
        watcher_path=watcher_path,
        apply=True,
    )
    watcher = json.loads(watcher_path.read_text())

    assert watcher["watch_accounts"]["@alpha"]["status"] == "approved"
    assert watcher["watch_accounts"]["@alpha"]["terms"] == ["$alpha", "curated phrase"]


def test_apply_watcher_approvals_preserves_manually_approved_terms(tmp_path):
    events_path = tmp_path / "movement_events.json"
    watcher_path = tmp_path / "watcher.json"
    events_path.write_text(json.dumps({
        "schema_version": "movement_events_v1",
        "updated_at": "",
        "events": [_event("2026-06-10T11:45:00Z")],
    }))
    watcher_path.write_text(json.dumps({
        "schema_version": "watcher_state_v1",
        "updated_at": "",
        "signals": [],
        "rules": [],
        "watch_accounts": {
            "@alpha": {
                "status": "approved",
                "terms": ["$alpha", "curated phrase"],
                "source_signal_ids": ["signal_alpha"],
            }
        },
    }))

    apply_watcher_approvals(
        events_path=events_path,
        watcher_path=watcher_path,
        apply=True,
    )
    watcher = json.loads(watcher_path.read_text())

    assert watcher["watch_accounts"]["@alpha"]["status"] == "approved"
    assert watcher["watch_accounts"]["@alpha"]["terms"] == ["$alpha", "curated phrase"]
