"""Auto-label watcher candidates using tweet timing versus movement start."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from lib.movement_events import (
    MOVEMENT_EVENTS_FILE,
    dedupe,
    load_events,
    normalize_handle,
    now_iso,
    parse_iso,
    upsert_events,
)
from lib.watcher_state import WATCHER_FILE


APPROVED_ALPHA_SOURCE = "approved_alpha_source"
APPROVED_MOVEMENT_CHATTER = "approved_movement_chatter"
REJECTED_LATE_REACTION = "rejected_late_reaction"
REJECTED_STALE_PRE_MOVE = "rejected_stale_pre_move"
REJECTED_NO_TWEET_EVIDENCE = "rejected_no_tweet_evidence"
REJECTED_NO_APPROVED_TERMS = "rejected_no_approved_terms"
UNKNOWN_MOVEMENT_START = "unknown_movement_start"
UNKNOWN_TWEET_TIMING = "unknown_tweet_timing"
UNSUPPORTED_DIRECTION = "unsupported_direction"

APPROVED_LABELS = {APPROVED_ALPHA_SOURCE, APPROVED_MOVEMENT_CHATTER}
PENDING_LABELS = {UNKNOWN_MOVEMENT_START, UNKNOWN_TWEET_TIMING}

DEFAULT_EARLY_AFTER_START_MINUTES = 30.0
DEFAULT_MAX_PRE_START_LEAD_MINUTES = 12 * 60.0

GENERIC_TERMS = {
    "ai",
    "ai narra",
    "alpha",
    "app launch",
    "article timing",
    "base tape",
    "big launch",
    "called the bottom",
    "cbventures",
    "cbventures ties",
    "cex rip",
    "community meme",
    "dead coin",
    "defai",
    "insane breakout",
    "kol call",
    "kol post",
    "kol positions",
    "moonshot",
    "narrative",
    "narrative rotation",
    "new app",
    "onchain outflows",
    "onchain terminal",
    "openai",
    "pump",
    "rockstar",
    "shutdown",
    "spacex",
    "team update",
    "vote activity",
    "vote count moving",
}


def apply_watcher_approvals(
    *,
    events_path: Path = MOVEMENT_EVENTS_FILE,
    watcher_path: Path = WATCHER_FILE,
    early_after_start_minutes: float = DEFAULT_EARLY_AFTER_START_MINUTES,
    max_pre_start_lead_minutes: float = DEFAULT_MAX_PRE_START_LEAD_MINUTES,
    apply: bool = True,
) -> dict:
    """Label candidate rules and update watcher account statuses."""
    event_data = load_events(events_path)
    labeled_events = label_events(
        event_data.get("events") or [],
        early_after_start_minutes=early_after_start_minutes,
        max_pre_start_lead_minutes=max_pre_start_lead_minutes,
    )

    watcher_state = load_watcher_state(watcher_path)
    watcher_state, watcher_stats = update_watcher_state_from_events(
        watcher_state,
        labeled_events,
    )

    stats = approval_stats(labeled_events)
    stats.update(watcher_stats)

    if apply:
        upsert_events(labeled_events, path=events_path)
        event_data = load_events(events_path)
        event_data["watcher_approval"] = {
            "method": "tweet_timing_vs_estimated_start",
            "early_after_start_minutes": early_after_start_minutes,
            "max_pre_start_lead_minutes": max_pre_start_lead_minutes,
            "stats": stats,
            "updated_at": now_iso(),
        }
        events_path.write_text(json.dumps(event_data, indent=2, sort_keys=True) + "\n")

        watcher_state["updated_at"] = now_iso()
        watcher_state["watcher_approval"] = {
            "method": "tweet_timing_vs_estimated_start",
            "early_after_start_minutes": early_after_start_minutes,
            "max_pre_start_lead_minutes": max_pre_start_lead_minutes,
            "stats": stats,
            "updated_at": watcher_state["updated_at"],
        }
        watcher_path.parent.mkdir(parents=True, exist_ok=True)
        watcher_path.write_text(json.dumps(watcher_state, indent=2, sort_keys=True) + "\n")

    return {
        "applied": apply,
        "events": len(labeled_events),
        **stats,
    }


def label_events(
    events: list[dict],
    *,
    early_after_start_minutes: float = DEFAULT_EARLY_AFTER_START_MINUTES,
    max_pre_start_lead_minutes: float = DEFAULT_MAX_PRE_START_LEAD_MINUTES,
) -> list[dict]:
    """Return events with candidate_watch_rules labeled for approval."""
    return [
        label_event(
            event,
            early_after_start_minutes=early_after_start_minutes,
            max_pre_start_lead_minutes=max_pre_start_lead_minutes,
        )
        for event in events
        if isinstance(event, dict)
    ]


def label_event(
    event: dict,
    *,
    early_after_start_minutes: float = DEFAULT_EARLY_AFTER_START_MINUTES,
    max_pre_start_lead_minutes: float = DEFAULT_MAX_PRE_START_LEAD_MINUTES,
) -> dict:
    out = dict(event)
    movement = out.get("movement") if isinstance(out.get("movement"), dict) else {}
    direction = str(movement.get("direction") or "").lower()
    estimated_start = parse_iso(movement.get("estimated_start_at"))
    evidence_by_author = _evidence_by_author(out.get("evidence") or [])
    token_terms = _token_terms(out)

    labeled = []
    for candidate in out.get("candidate_watch_rules") or []:
        if not isinstance(candidate, dict):
            continue
        account = normalize_handle(candidate.get("account"))
        evidence = evidence_by_author.get(account, [])
        decision = approval_decision(
            direction=direction,
            estimated_start=estimated_start,
            evidence=evidence,
            early_after_start_minutes=early_after_start_minutes,
            max_pre_start_lead_minutes=max_pre_start_lead_minutes,
        )
        approved_terms = approved_terms_for_candidate(candidate, token_terms, evidence=evidence, account=account)
        label = decision["label"]
        reason = decision["reason"]
        if label in APPROVED_LABELS and not approved_terms:
            label = REJECTED_NO_APPROVED_TERMS
            reason = "candidate had timely evidence, but no token-specific rule terms survived pruning"
        labeled.append({
            **candidate,
            "account": account or candidate.get("account") or "",
            "approval_label": label,
            "approval_status": status_for_label(label),
            "approval_reason": reason,
            "minutes_from_estimated_start": decision.get("minutes_from_estimated_start"),
            "approval_evidence_url": decision.get("evidence_url") or "",
            "approval_tweet_at": decision.get("tweet_at") or "",
            "approved_terms": approved_terms if label in APPROVED_LABELS else [],
        })

    out["candidate_watch_rules"] = labeled
    return out


def approval_decision(
    *,
    direction: str,
    estimated_start: datetime | None,
    evidence: list[dict],
    early_after_start_minutes: float = DEFAULT_EARLY_AFTER_START_MINUTES,
    max_pre_start_lead_minutes: float = DEFAULT_MAX_PRE_START_LEAD_MINUTES,
) -> dict:
    """Classify one account's evidence for one movement event."""
    if direction not in {"pump", "dump"}:
        return _decision(UNSUPPORTED_DIRECTION, "event direction is not pump or dump")
    if estimated_start is None:
        return _decision(UNKNOWN_MOVEMENT_START, "event has no estimated movement start")

    timed_evidence = []
    for ref in evidence:
        tweet_at = parse_iso(ref.get("tweet_at"))
        if tweet_at is not None:
            timed_evidence.append((tweet_at, ref))

    if not evidence:
        return _decision(REJECTED_NO_TWEET_EVIDENCE, "candidate account has no direct tweet evidence")
    if not timed_evidence:
        return _decision(UNKNOWN_TWEET_TIMING, "candidate account has no tweet timestamp")

    best_tweet_at, best_ref = min(
        timed_evidence,
        key=lambda item: abs((item[0] - estimated_start).total_seconds()),
    )
    minutes = round((best_tweet_at - estimated_start).total_seconds() / 60.0, 2)
    evidence_url = best_ref.get("url") or ""
    tweet_at_iso = best_ref.get("tweet_at") or ""

    if minutes < -max_pre_start_lead_minutes:
        return _decision(
            REJECTED_STALE_PRE_MOVE,
            f"tweet was {abs(minutes):.1f} minutes before the estimated move start, outside the freshness window",
            minutes=minutes,
            evidence_url=evidence_url,
            tweet_at=tweet_at_iso,
        )
    if minutes <= 0:
        return _decision(
            APPROVED_ALPHA_SOURCE,
            f"tweet was {abs(minutes):.1f} minutes before the estimated {direction} start",
            minutes=minutes,
            evidence_url=evidence_url,
            tweet_at=tweet_at_iso,
        )
    if minutes <= early_after_start_minutes:
        return _decision(
            APPROVED_MOVEMENT_CHATTER,
            f"tweet was {minutes:.1f} minutes after the estimated {direction} start",
            minutes=minutes,
            evidence_url=evidence_url,
            tweet_at=tweet_at_iso,
        )
    return _decision(
        REJECTED_LATE_REACTION,
        f"tweet was {minutes:.1f} minutes after the estimated {direction} start",
        minutes=minutes,
        evidence_url=evidence_url,
        tweet_at=tweet_at_iso,
    )


def update_watcher_state_from_events(state: dict, events: list[dict]) -> tuple[dict, dict]:
    """Apply best candidate labels back onto data/watcher.json state."""
    out = dict(state)
    watch_accounts = dict(out.get("watch_accounts") if isinstance(out.get("watch_accounts"), dict) else {})
    decisions = _best_decisions_by_account(events)
    stats = {
        "watch_accounts_approved": 0,
        "watch_accounts_pending": 0,
        "watch_accounts_rejected": 0,
        "watch_accounts_unchanged": 0,
    }

    for account, config in watch_accounts.items():
        if not isinstance(config, dict):
            continue
        decision = decisions.get(normalize_handle(account))
        if not decision:
            stats["watch_accounts_unchanged"] += 1
            continue

        current_status = str(config.get("status") or "pending").lower()
        if current_status == "disabled":
            stats["watch_accounts_unchanged"] += 1
            continue

        next_status = status_for_label(decision["label"])
        if current_status == "approved" and not config.get("approval_label"):
            stats["watch_accounts_unchanged"] += 1
            continue
        if current_status == "approved" and next_status != "approved":
            stats["watch_accounts_unchanged"] += 1
            continue

        updated = dict(config)
        updated.update({
            "status": next_status,
            "approval_label": decision["label"],
            "approval_reason": decision["reason"],
            "approval_source_signal_ids": decision["source_signal_ids"],
            "approval_evidence_urls": decision["evidence_urls"],
            "approval_updated_at": now_iso(),
        })
        if next_status == "approved":
            updated["terms"] = decision["approved_terms"]
        watch_accounts[account] = updated
        stats[f"watch_accounts_{next_status}"] += 1

    out["watch_accounts"] = watch_accounts
    return out, stats


def approval_stats(events: list[dict]) -> dict:
    labels: dict[str, int] = {}
    candidates = 0
    for event in events:
        for candidate in event.get("candidate_watch_rules") or []:
            if not isinstance(candidate, dict):
                continue
            candidates += 1
            label = candidate.get("approval_label") or "unlabeled"
            labels[label] = labels.get(label, 0) + 1
    return {
        "candidate_rules": candidates,
        "candidate_labels": labels,
    }


def status_for_label(label: str) -> str:
    if label in APPROVED_LABELS:
        return "approved"
    if label in PENDING_LABELS:
        return "pending"
    return "rejected"


def approved_terms_for_candidate(
    candidate: dict,
    token_terms: list[str],
    *,
    evidence: list[dict] | None = None,
    account: str = "",
) -> list[str]:
    terms = candidate.get("terms") if isinstance(candidate.get("terms"), list) else []
    evidence_text = _evidence_text(evidence or [])
    account_text = normalize_handle(account).lstrip("@")
    clean = []
    for term in terms:
        text = _clean_term(term)
        if not text:
            continue
        if text in GENERIC_TERMS and not text.startswith("$"):
            continue
        if not _term_is_grounded(text, evidence_text, account_text):
            continue
        if text in token_terms:
            clean.append(text)
            continue
        if any(token in text for token in token_terms if len(token) >= 4):
            clean.append(text)
    return dedupe(clean)[:12]


def load_watcher_state(path: Path = WATCHER_FILE) -> dict:
    if not path.exists():
        return {"schema_version": "watcher_state_v1", "signals": [], "watch_accounts": {}, "rules": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"schema_version": "watcher_state_v1", "signals": [], "watch_accounts": {}, "rules": []}
    return data if isinstance(data, dict) else {"schema_version": "watcher_state_v1", "signals": [], "watch_accounts": {}, "rules": []}


def _best_decisions_by_account(events: list[dict]) -> dict[str, dict]:
    ranked: dict[str, dict] = {}
    for event in events:
        source_id = event.get("source_signal_id") or event.get("event_id") or ""
        for candidate in event.get("candidate_watch_rules") or []:
            if not isinstance(candidate, dict):
                continue
            account = normalize_handle(candidate.get("account"))
            if not account:
                continue
            decision = {
                "label": candidate.get("approval_label") or "",
                "reason": candidate.get("approval_reason") or "",
                "source_signal_ids": [source_id] if source_id else [],
                "evidence_urls": [candidate.get("approval_evidence_url")] if candidate.get("approval_evidence_url") else [],
                "approved_terms": candidate.get("approved_terms") if isinstance(candidate.get("approved_terms"), list) else [],
            }
            existing = ranked.get(account)
            if existing is None:
                ranked[account] = decision
                continue
            if _label_rank(decision["label"]) < _label_rank(existing["label"]):
                ranked[account] = {
                    **decision,
                    "source_signal_ids": dedupe(existing["source_signal_ids"] + decision["source_signal_ids"]),
                    "evidence_urls": dedupe(existing["evidence_urls"] + decision["evidence_urls"]),
                    "approved_terms": dedupe(existing["approved_terms"] + decision["approved_terms"])[:24],
                }
            elif decision["label"] in APPROVED_LABELS and existing["label"] in APPROVED_LABELS:
                existing["source_signal_ids"] = dedupe(existing["source_signal_ids"] + decision["source_signal_ids"])
                existing["evidence_urls"] = dedupe(existing["evidence_urls"] + decision["evidence_urls"])
                existing["approved_terms"] = dedupe(existing["approved_terms"] + decision["approved_terms"])[:24]
            elif decision["label"] == existing["label"]:
                existing["source_signal_ids"] = dedupe(existing["source_signal_ids"] + decision["source_signal_ids"])
                existing["evidence_urls"] = dedupe(existing["evidence_urls"] + decision["evidence_urls"])
                existing["approved_terms"] = dedupe(existing["approved_terms"] + decision["approved_terms"])[:24]
    return ranked


def _label_rank(label: str) -> int:
    order = [
        APPROVED_ALPHA_SOURCE,
        APPROVED_MOVEMENT_CHATTER,
        UNKNOWN_MOVEMENT_START,
        UNKNOWN_TWEET_TIMING,
        REJECTED_STALE_PRE_MOVE,
        REJECTED_LATE_REACTION,
        REJECTED_NO_APPROVED_TERMS,
        REJECTED_NO_TWEET_EVIDENCE,
        UNSUPPORTED_DIRECTION,
    ]
    try:
        return order.index(label)
    except ValueError:
        return len(order)


def _evidence_by_author(evidence: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for ref in evidence:
        if not isinstance(ref, dict):
            continue
        handle = normalize_handle(ref.get("author_handle"))
        if handle:
            out.setdefault(handle, []).append(ref)
    return out


def _evidence_text(evidence: list[dict]) -> str:
    parts = []
    for ref in evidence:
        if isinstance(ref, dict):
            parts.append(str(ref.get("text") or ""))
            parts.append(str(ref.get("url") or ""))
    return _clean_term(" ".join(parts))


def _term_is_grounded(term: str, evidence_text: str, account_text: str) -> bool:
    if term.startswith("$"):
        return term in evidence_text
    if _contains_term(evidence_text, term):
        return True
    bare = term.lstrip("$").replace(" ", "")
    return bool(bare and len(bare) >= 4 and bare in account_text)


def _contains_term(text: str, term: str) -> bool:
    if not text or not term:
        return False
    if " " in term:
        return term in text
    return re.search(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", text) is not None


def _token_terms(event: dict) -> list[str]:
    token = event.get("token") if isinstance(event.get("token"), dict) else {}
    symbol = _clean_term(token.get("symbol"))
    terms = []
    if symbol:
        terms.extend([symbol, f"${symbol}"])
    clues = event.get("watcher_clues") if isinstance(event.get("watcher_clues"), dict) else {}
    for term in clues.get("keywords") or []:
        clean = _clean_term(term)
        if clean and clean not in GENERIC_TERMS:
            terms.append(clean)
    return dedupe(terms)


def _decision(
    label: str,
    reason: str,
    *,
    minutes: float | None = None,
    evidence_url: str = "",
    tweet_at: str = "",
) -> dict:
    return {
        "label": label,
        "reason": reason,
        "minutes_from_estimated_start": minutes,
        "evidence_url": evidence_url,
        "tweet_at": tweet_at,
    }


def _clean_term(value) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text
