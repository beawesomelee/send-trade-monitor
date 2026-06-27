from lib import watcher_state
from lib.watcher_rules import build_desired_rules


def _mover():
    return {
        "address": "0xabc",
        "symbol": "TEST",
        "direction": "pump",
        "price_change_window": "h6",
        "price_change_pct": 100,
        "lore": "example movement",
        "dexscreener_url": "https://example.com",
        "lore_packet": {
            "references": [
                {"author_handle": "@RefAccount", "text": "called test", "type": "x_post"}
            ],
            "watcher_clues": {
                "accounts": ["@CandidateKOL"],
                "keywords": ["test"],
                "phrases": ["big launch"],
                "catalysts": ["kol_post"],
            },
        },
    }


def test_model_suggested_accounts_are_pending_not_active(monkeypatch, tmp_path):
    watcher_file = tmp_path / "watcher.json"
    monkeypatch.setattr(watcher_state, "WATCHER_FILE", watcher_file)

    created = watcher_state.record_signals([_mover()], detected_at="2026-06-10T00:00:00Z")

    assert len(created) == 1
    state = watcher_state._load_state()
    assert state["watch_accounts"]["@candidatekol"]["status"] == "pending"
    assert state["watch_accounts"]["@refaccount"]["status"] == "pending"
    assert "reason_added" in state["watch_accounts"]["@candidatekol"]


def test_record_signals_preserves_extra_watcher_metadata(monkeypatch, tmp_path):
    watcher_file = tmp_path / "watcher.json"
    watcher_file.write_text(
        """
{
  "schema_version": "watcher_state_v1",
  "updated_at": "2026-06-10T00:00:00Z",
  "signals": [],
  "watch_accounts": {},
  "rules": [],
  "rules_synced_at": "2026-06-10T00:00:00Z",
  "watcher_approval": {"method": "existing"}
}
"""
    )
    monkeypatch.setattr(watcher_state, "WATCHER_FILE", watcher_file)

    watcher_state.record_signals([_mover()], detected_at="2026-06-10T01:00:00Z")
    state = watcher_state._load_state()

    assert state["rules_synced_at"] == "2026-06-10T00:00:00Z"
    assert state["watcher_approval"] == {"method": "existing"}


def test_rule_builder_uses_only_approved_watch_accounts():
    state = {
        "watch_accounts": {
            "@pending": {"status": "pending", "terms": ["alpha"]},
            "@active_legacy": {"status": "active", "terms": ["alpha"]},
            "@approved": {"status": "approved", "terms": ["alpha"]},
            "@disabled": {"status": "disabled", "terms": ["alpha"]},
        }
    }

    rules = build_desired_rules(state)

    assert [rule["tag"] for rule in rules] == ["send_watcher:community:approved"]
    assert rules[0]["value"] == "from:approved (alpha) -is:retweet"


def test_rule_builder_uses_account_only_rules_for_official_token_accounts():
    state = {
        "watch_accounts": {
            "@velvet_capital": {
                "status": "approved",
                "account_type": "official_token_account",
                "rule_mode": "account_only",
                "terms": ["velvet"],
                "token": {
                    "symbol": "VELVET",
                    "address": "0xbf927b841994731c573bdf09ceb0c6b0aa887cdd",
                    "chain_slug": "base",
                },
            },
        }
    }

    rules = build_desired_rules(state)

    assert rules == [
        {
            "tag": "send_watcher:official:1",
            "value": "(from:velvet_capital) -is:retweet",
        }
    ]
