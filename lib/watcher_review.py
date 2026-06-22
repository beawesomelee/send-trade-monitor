"""Build and classify pre-move watcher candidates."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from lib.lore import MODEL, XAI_ENDPOINT, _extract_raw_text
from lib.movement_events import MOVEMENT_EVENTS_FILE, load_events, parse_iso
from lib.watcher_rules import WATCHER_FILE, load_watcher_state


ROOT = Path(__file__).resolve().parent.parent
WATCHER_REVIEW_FILE = ROOT / "data" / "watcher_review.json"
SCHEMA_VERSION = "watcher_review_v1"

CLASSIFICATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "label": {
            "type": "string",
            "enum": ["predictive_alpha", "maybe_alpha", "late_reaction", "noise", "unknown"],
        },
        "implied_direction": {
            "type": "string",
            "enum": ["pump", "dump", "unclear"],
        },
        "reason": {"type": "string"},
        "suggested_terms": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["label", "implied_direction", "reason", "suggested_terms"],
}

SYSTEM_PROMPT = """you classify whether an x post was useful pre-move alpha.

rules:
- judge only the provided post and event context.
- do not search the web.
- predictive_alpha means the post happened before the move and contained a concrete catalyst, thesis, launch, article, official/team signal, or accumulation/bottom call that plausibly pointed in the same direction as the later price move.
- maybe_alpha means timing was good but the post is weaker, vague, or partly reactive.
- late_reaction means it mainly reacts to price already moving, even if timestamp is before detection.
- noise means generic hype, vote spam, copy-paste promo, unrelated chatter, or useless broad terms.
- unknown means there is not enough information.
- suggested_terms must be narrow terms worth watching with this account. prefer token/project name, ticker, specific product/phrase, or named catalyst. avoid broad words like moonshot, pump, ath, team update, narrative, community, killing it, vote.
- output json only."""


def build_pre_move_candidates(
    *,
    events_path: Path = MOVEMENT_EVENTS_FILE,
    watcher_path: Path = WATCHER_FILE,
    max_lookback_hours: float = 24.0,
) -> list[dict]:
    """Return cited evidence posts that happened before estimated start/window."""
    events = load_events(events_path).get("events", [])
    watcher = load_watcher_state(watcher_path)
    watch_accounts = (
        watcher.get("watch_accounts") if isinstance(watcher.get("watch_accounts"), dict) else {}
    )
    return build_pre_move_candidates_from_events(
        events,
        watch_accounts=watch_accounts,
        max_lookback_hours=max_lookback_hours,
    )


def build_pre_move_candidates_from_events(
    events: list[dict],
    *,
    watch_accounts: dict | None = None,
    max_lookback_hours: float = 24.0,
) -> list[dict]:
    """Return pre-move candidates from an in-memory event list."""
    watch_accounts = watch_accounts if isinstance(watch_accounts, dict) else {}
    candidates = []
    for event in events:
        if not isinstance(event, dict):
            continue
        movement = event.get("movement") if isinstance(event.get("movement"), dict) else {}
        token = event.get("token") if isinstance(event.get("token"), dict) else {}
        estimated_start = parse_iso(movement.get("estimated_start_at"))
        window_start = parse_iso(movement.get("window_start_at"))
        cutoff = estimated_start or window_start
        cutoff_source = "estimated_start_at" if estimated_start else "window_start_at"
        if cutoff is None:
            continue

        for evidence in event.get("evidence", []):
            if not isinstance(evidence, dict):
                continue
            tweet_at = parse_iso(evidence.get("tweet_at"))
            if tweet_at is None or tweet_at >= cutoff:
                continue
            minutes_before_cutoff = minutes_between(tweet_at, cutoff)
            if (
                max_lookback_hours > 0
                and minutes_before_cutoff is not None
                and minutes_before_cutoff > max_lookback_hours * 60
            ):
                continue
            account = normalize_handle(evidence.get("author_handle"))
            if not account:
                continue
            terms = terms_for_account(account, event, watch_accounts)
            candidates.append({
                "candidate_id": candidate_id(event, evidence, terms),
                "schema_version": SCHEMA_VERSION,
                "account": account,
                "token_symbol": token.get("symbol") or "",
                "token_address": token.get("address") or "",
                "chain_slug": token.get("chain_slug") or "",
                "event_id": event.get("event_id") or "",
                "source_signal_id": event.get("source_signal_id") or event.get("event_id") or "",
                "movement_direction": movement.get("direction") or "",
                "movement_change_pct": movement.get("change_pct") or 0,
                "detected_at": event.get("detected_at") or "",
                "window_start_at": movement.get("window_start_at") or "",
                "estimated_start_at": movement.get("estimated_start_at") or "",
                "pre_move_cutoff_at": isoformat_z(cutoff),
                "pre_move_cutoff_source": cutoff_source,
                "tweet_at": evidence.get("tweet_at") or "",
                "minutes_before_cutoff": minutes_before_cutoff,
                "minutes_before_detection": evidence.get("minutes_before_detection"),
                "timing_bucket": "before_move",
                "source_evidence_url": evidence.get("url") or "",
                "tweet_text": evidence.get("text") or "",
                "relevance": evidence.get("relevance") or "",
                "terms": terms,
                "status": watch_accounts.get(account, {}).get("status", "unknown"),
            })

    return sorted(
        dedupe_candidates(candidates),
        key=lambda item: (
            item.get("event_id") or "",
            -(item.get("minutes_before_cutoff") or 0),
            item.get("account") or "",
        ),
    )


def review_recent_watcher_candidates(
    events: list[dict],
    *,
    watcher_path: Path = WATCHER_FILE,
    review_path: Path = WATCHER_REVIEW_FILE,
    classify: bool = True,
    timeout: int = 20,
    max_lookback_hours: float = 24.0,
    max_candidates: int = 25,
    max_records: int = 500,
) -> dict:
    """Review newly created movement events without raising into the alert flow."""
    try:
        watcher = load_watcher_state(watcher_path)
        watch_accounts = (
            watcher.get("watch_accounts") if isinstance(watcher.get("watch_accounts"), dict) else {}
        )
        candidates = build_pre_move_candidates_from_events(
            events,
            watch_accounts=watch_accounts,
            max_lookback_hours=max_lookback_hours,
        )
        if max_candidates > 0:
            candidates = candidates[:max_candidates]
        if classify:
            candidates = classify_candidates_with_grok(candidates, timeout=timeout)
        written = upsert_review_candidates(
            candidates,
            path=review_path,
            max_records=max_records,
        )
        return {
            "candidate_count": len(candidates),
            "classified_count": sum(1 for candidate in candidates if candidate.get("grok")),
            "written": written,
            "error": "",
        }
    except Exception as exc:
        return {
            "candidate_count": 0,
            "classified_count": 0,
            "written": 0,
            "error": str(exc),
        }


def classify_candidate_with_grok(candidate: dict, timeout: int = 60) -> dict:
    """Ask Grok whether one pre-move candidate looks predictive."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return {
            "label": "unknown",
            "implied_direction": "unclear",
            "reason": "XAI_API_KEY is not set",
            "suggested_terms": [],
        }

    payload = {
        "model": MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": candidate_prompt(candidate)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "watcher_candidate_classification",
                "schema": CLASSIFICATION_SCHEMA,
                "strict": True,
            }
        },
        "temperature": 0.1,
        "max_output_tokens": 350,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(XAI_ENDPOINT, json=payload, headers=headers, timeout=timeout)
        if response.status_code != 200:
            return {
                "label": "unknown",
                "implied_direction": "unclear",
                "reason": f"Grok HTTP {response.status_code}: {response.text[:240]}",
                "suggested_terms": [],
            }
        raw = _extract_raw_text(response.json())
        parsed = json.loads(raw)
        return clean_classification(parsed)
    except Exception as exc:
        return {
            "label": "unknown",
            "implied_direction": "unclear",
            "reason": f"Grok classification failed: {exc}",
            "suggested_terms": [],
        }


def classify_candidates_with_grok(candidates: list[dict], timeout: int = 60) -> list[dict]:
    out = []
    for candidate in candidates:
        classified = dict(candidate)
        classification = classify_candidate_with_grok(candidate, timeout=timeout)
        classified["grok"] = classification
        classified["recommendation"] = recommendation_for_classification(classification)
        out.append(classified)
    return out


def review_payload(candidates: list[dict]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_iso(),
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def write_review(candidates: list[dict], path: Path = WATCHER_REVIEW_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review_payload(candidates), indent=2, sort_keys=True) + "\n")


def upsert_review_candidates(
    candidates: list[dict],
    *,
    path: Path = WATCHER_REVIEW_FILE,
    max_records: int = 500,
) -> int:
    """Upsert compact candidate review records."""
    existing = load_review(path)
    by_id = {
        item.get("candidate_id"): item
        for item in existing.get("candidates", [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    updated_at = now_iso()
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        if not candidate_id:
            continue
        previous = by_id.get(candidate_id, {})
        row = compact_review_candidate(candidate)
        row["created_at"] = previous.get("created_at") or updated_at
        row["updated_at"] = updated_at
        by_id[candidate_id] = row

    rows = sorted(
        by_id.values(),
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )[:max_records]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": updated_at,
        "candidate_count": len(rows),
        "candidates": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return len(candidates)


def load_review(path: Path = WATCHER_REVIEW_FILE) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "candidates": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "candidates": []}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "candidates": []}
    return {
        "schema_version": data.get("schema_version") or SCHEMA_VERSION,
        "updated_at": data.get("updated_at") or "",
        "candidates": data.get("candidates") if isinstance(data.get("candidates"), list) else [],
    }


def compact_review_candidate(candidate: dict) -> dict:
    classification = candidate.get("grok") if isinstance(candidate.get("grok"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate.get("candidate_id") or "",
        "account": candidate.get("account") or "",
        "token_symbol": candidate.get("token_symbol") or "",
        "token_address": candidate.get("token_address") or "",
        "chain_slug": candidate.get("chain_slug") or "",
        "event_id": candidate.get("event_id") or "",
        "source_signal_id": candidate.get("source_signal_id") or "",
        "movement_direction": candidate.get("movement_direction") or "",
        "movement_change_pct": candidate.get("movement_change_pct") or 0,
        "detected_at": candidate.get("detected_at") or "",
        "estimated_start_at": candidate.get("estimated_start_at") or "",
        "pre_move_cutoff_at": candidate.get("pre_move_cutoff_at") or "",
        "pre_move_cutoff_source": candidate.get("pre_move_cutoff_source") or "",
        "tweet_at": candidate.get("tweet_at") or "",
        "minutes_before_cutoff": candidate.get("minutes_before_cutoff"),
        "source_evidence_url": candidate.get("source_evidence_url") or "",
        "tweet_text": truncate(candidate.get("tweet_text") or "", 280),
        "relevance": truncate(candidate.get("relevance") or "", 280),
        "terms": candidate.get("terms") or [],
        "watch_account_status": candidate.get("status") or "",
        "grok_label": classification.get("label") or "",
        "grok_implied_direction": classification.get("implied_direction") or "",
        "grok_reason": truncate(classification.get("reason") or "", 600),
        "grok_suggested_terms": classification.get("suggested_terms") or [],
        "recommendation": candidate.get("recommendation") or "",
        "human_label": candidate.get("human_label") or "unset",
        "final_action": candidate.get("final_action") or "unset",
        "model": MODEL if classification else "",
    }


def candidate_prompt(candidate: dict) -> str:
    return "\n".join([
        f"token: {candidate.get('token_symbol')} ({candidate.get('chain_slug')})",
        f"movement_direction: {candidate.get('movement_direction')}",
        f"movement_change_pct: {candidate.get('movement_change_pct')}",
        f"tweet_at: {candidate.get('tweet_at')}",
        f"pre_move_cutoff_at: {candidate.get('pre_move_cutoff_at')}",
        f"cutoff_source: {candidate.get('pre_move_cutoff_source')}",
        f"minutes_before_cutoff: {candidate.get('minutes_before_cutoff')}",
        f"account: {candidate.get('account')}",
        f"post_text: {candidate.get('tweet_text')}",
        f"existing_relevance: {candidate.get('relevance')}",
        f"candidate_terms: {', '.join(candidate.get('terms') or [])}",
        "",
        "question: did this post contain predictive alpha for the later movement?",
    ])


def recommendation_for_classification(classification: dict) -> str:
    label = classification.get("label")
    direction = classification.get("implied_direction")
    if label == "predictive_alpha" and direction in {"pump", "dump"}:
        return "manual_approve_review"
    if label == "maybe_alpha":
        return "manual_review"
    if label in {"late_reaction", "noise"}:
        return "reject"
    return "unknown"


def clean_classification(value: dict) -> dict:
    if not isinstance(value, dict):
        value = {}
    label = value.get("label") if value.get("label") in {
        "predictive_alpha", "maybe_alpha", "late_reaction", "noise", "unknown"
    } else "unknown"
    direction = value.get("implied_direction") if value.get("implied_direction") in {
        "pump", "dump", "unclear"
    } else "unclear"
    return {
        "label": label,
        "implied_direction": direction,
        "reason": str(value.get("reason") or "").strip()[:600],
        "suggested_terms": clean_terms(value.get("suggested_terms") or []),
    }


def terms_for_account(account: str, event: dict, watch_accounts: dict) -> list[str]:
    configured = watch_accounts.get(account, {})
    if isinstance(configured, dict) and isinstance(configured.get("terms"), list):
        return clean_terms(configured["terms"])

    terms = []
    token = event.get("token") if isinstance(event.get("token"), dict) else {}
    symbol = str(token.get("symbol") or "").strip()
    if symbol:
        terms.extend([symbol, f"${symbol}"])
    clues = event.get("watcher_clues") if isinstance(event.get("watcher_clues"), dict) else {}
    for key in ("keywords", "phrases"):
        values = clues.get(key)
        if isinstance(values, list):
            terms.extend(values)
    return clean_terms(terms)


BROAD_TERMS = {
    "moonshot", "pump", "pumping", "ath", "gains", "community", "team update",
    "narrative", "narra", "kol post", "vote", "vote activity", "killing it fr",
    "base tape", "ai narra",
}


def clean_terms(values) -> list[str]:
    out = []
    seen = set()
    for value in values:
        term = re.sub(r"\s+", " ", str(value or "").strip().lower())
        if len(term) < 3 or term in BROAD_TERMS or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out[:12]


def normalize_handle(value) -> str:
    if value is None:
        return ""
    handle = str(value).strip().lower().lstrip("@")
    handle = re.sub(r"[^a-z0-9_]", "", handle)
    return f"@{handle}" if handle else ""


def candidate_id(event: dict, evidence: dict, terms: list[str]) -> str:
    raw = "|".join([
        str(event.get("event_id") or ""),
        str(evidence.get("url") or ""),
        ",".join(terms),
    ])
    return "watch_candidate_" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def dedupe_candidates(candidates: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for candidate in candidates:
        key = candidate.get("candidate_id")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def minutes_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 2)


def isoformat_z(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
