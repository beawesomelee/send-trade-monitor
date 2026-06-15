"""Build structured movement event records from watcher signals."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lib.x_time import isoformat_z, tweet_id_from_url, tweet_time_from_url


ROOT = Path(__file__).resolve().parent.parent
MOVEMENT_EVENTS_FILE = ROOT / "data" / "movement_events.json"
SCHEMA_VERSION = "movement_events_v1"


def build_event_from_signal(signal: dict) -> dict:
    """Convert a watcher signal into a structured movement event."""
    detected_at = parse_iso(signal.get("detected_at"))
    movement = signal.get("movement") if isinstance(signal.get("movement"), dict) else {}
    window = str(movement.get("window") or "")
    window_start = movement_window_start(detected_at, window)
    token = signal.get("token") if isinstance(signal.get("token"), dict) else {}
    watcher_clues = (
        signal.get("watcher_clues") if isinstance(signal.get("watcher_clues"), dict) else {}
    )

    evidence = [
        enrich_reference(ref, window_start=window_start, detected_at=detected_at)
        for ref in signal.get("references", [])
        if isinstance(ref, dict)
    ]

    return {
        "event_id": signal.get("id") or "",
        "source_signal_id": signal.get("id") or "",
        "detected_at": isoformat_z(detected_at),
        "token": {
            "symbol": token.get("symbol") or "",
            "address": token.get("address") or "",
            "chain_slug": chain_slug_from_signal(signal),
        },
        "movement": {
            "direction": movement.get("direction") or "",
            "window": window,
            "change_pct": movement.get("change_pct") or 0,
            "detected_at": isoformat_z(detected_at),
            "window_start_at": isoformat_z(window_start),
            "window_end_at": isoformat_z(detected_at),
            "estimated_start_at": "",
            "estimated_start_method": "",
            "estimated_start_confidence": None,
        },
        "evidence": evidence,
        "watcher_clues": {
            "accounts": watcher_clues.get("accounts") if isinstance(watcher_clues.get("accounts"), list) else [],
            "keywords": watcher_clues.get("keywords") if isinstance(watcher_clues.get("keywords"), list) else [],
            "phrases": watcher_clues.get("phrases") if isinstance(watcher_clues.get("phrases"), list) else [],
            "catalysts": watcher_clues.get("catalysts") if isinstance(watcher_clues.get("catalysts"), list) else [],
        },
        "candidate_watch_rules": candidate_watch_rules(signal, evidence),
        "lore": signal.get("lore") or "",
        "links": signal.get("links") if isinstance(signal.get("links"), dict) else {},
    }


def enrich_signal_timing(signal: dict) -> dict:
    """Return a signal with movement window and reference timing fields added."""
    out = dict(signal)
    detected_at = parse_iso(out.get("detected_at"))
    movement = dict(out.get("movement") if isinstance(out.get("movement"), dict) else {})
    window_start = movement_window_start(detected_at, str(movement.get("window") or ""))
    movement["detected_at"] = isoformat_z(detected_at)
    movement["window_start_at"] = isoformat_z(window_start)
    movement["window_end_at"] = isoformat_z(detected_at)
    out["movement"] = movement
    out["references"] = [
        enrich_reference(ref, window_start=window_start, detected_at=detected_at)
        for ref in out.get("references", [])
        if isinstance(ref, dict)
    ]
    return out


def enrich_reference(
    reference: dict,
    *,
    window_start: datetime | None,
    detected_at: datetime | None,
) -> dict:
    """Add tweet ID/time and timing labels to an X reference."""
    out = dict(reference)
    tweet_id = tweet_id_from_url(out.get("url"))
    tweet_at = tweet_time_from_url(out.get("url")) if tweet_id else None
    out["tweet_id"] = tweet_id
    out["tweet_at"] = isoformat_z(tweet_at)
    out["timing_bucket"] = timing_bucket(tweet_at, window_start, detected_at)
    out["minutes_from_window_start"] = minutes_between(window_start, tweet_at)
    out["minutes_before_detection"] = minutes_between(tweet_at, detected_at)
    return out


def candidate_watch_rules(signal: dict, evidence: list[dict]) -> list[dict]:
    """Build initial account/term candidates from a signal."""
    token = signal.get("token") if isinstance(signal.get("token"), dict) else {}
    symbol = clean_term(token.get("symbol"))
    watcher_clues = (
        signal.get("watcher_clues") if isinstance(signal.get("watcher_clues"), dict) else {}
    )
    terms = []
    if symbol:
        terms.extend([symbol, f"${symbol}"])
    terms.extend(watcher_clues.get("keywords") if isinstance(watcher_clues.get("keywords"), list) else [])
    terms.extend(watcher_clues.get("phrases") if isinstance(watcher_clues.get("phrases"), list) else [])
    terms.extend(
        str(catalyst).replace("_", " ")
        for catalyst in (
            watcher_clues.get("catalysts") if isinstance(watcher_clues.get("catalysts"), list) else []
        )
    )
    clean_terms = dedupe([clean_term(term) for term in terms if clean_term(term)])

    accounts = []
    accounts.extend(
        watcher_clues.get("accounts") if isinstance(watcher_clues.get("accounts"), list) else []
    )
    accounts.extend(ref.get("author_handle") for ref in evidence if ref.get("author_handle"))

    evidence_by_author: dict[str, list[dict]] = {}
    for ref in evidence:
        handle = normalize_handle(ref.get("author_handle"))
        if handle:
            evidence_by_author.setdefault(handle, []).append(ref)

    out = []
    for account in dedupe([normalize_handle(account) for account in accounts if normalize_handle(account)]):
        refs = evidence_by_author.get(account, [])
        timing = strongest_timing_bucket([ref.get("timing_bucket") for ref in refs])
        out.append({
            "account": account,
            "terms": clean_terms,
            "source_evidence_urls": [ref.get("url") for ref in refs if ref.get("url")],
            "timing_bucket": timing or "unknown",
            "initial_value_label": initial_value_label(timing),
        })
    return out


def upsert_events_from_signals(
    signals: list[dict],
    *,
    path: Path = MOVEMENT_EVENTS_FILE,
) -> list[dict]:
    """Upsert movement events for the supplied watcher signals."""
    if not signals:
        return []
    data = load_events(path)
    by_id = {
        event.get("event_id"): event
        for event in data["events"]
        if isinstance(event, dict) and event.get("event_id")
    }
    updated = []
    for signal in signals:
        event = build_event_from_signal(signal)
        if not event["event_id"]:
            continue
        by_id[event["event_id"]] = event
        updated.append(event)

    data["events"] = sorted(by_id.values(), key=lambda e: e.get("detected_at") or "")
    data["updated_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return updated


def load_events(path: Path = MOVEMENT_EVENTS_FILE) -> dict:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "events": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "events": []}
    if not isinstance(data, dict):
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "events": []}
    events = data.get("events") if isinstance(data.get("events"), list) else []
    return {
        "schema_version": data.get("schema_version") or SCHEMA_VERSION,
        "updated_at": data.get("updated_at") or "",
        "events": events,
    }


def build_events_from_watcher_state(state: dict) -> list[dict]:
    signals = state.get("signals") if isinstance(state.get("signals"), list) else []
    return [build_event_from_signal(signal) for signal in signals if isinstance(signal, dict)]


def movement_window_start(detected_at: datetime | None, window: str) -> datetime | None:
    if detected_at is None:
        return None
    hours = window_hours(window)
    if hours <= 0:
        return None
    return detected_at - timedelta(hours=hours)


def window_hours(window: str) -> float:
    match = re.fullmatch(r"h(\d+(?:\.\d+)?)", str(window or "").lower())
    if match:
        return float(match.group(1))
    return 0.0


def timing_bucket(
    tweet_at: datetime | None,
    window_start: datetime | None,
    detected_at: datetime | None,
) -> str:
    if tweet_at is None or detected_at is None:
        return "unknown"
    if window_start is not None and tweet_at < window_start:
        return "pre_window"
    if tweet_at <= detected_at:
        return "during_window"
    return "post_detection"


def strongest_timing_bucket(values) -> str:
    order = ["pre_window", "during_window", "post_detection", "unknown"]
    cleaned = [value for value in values if value in order]
    if not cleaned:
        return "unknown"
    return min(cleaned, key=order.index)


def initial_value_label(bucket: str) -> str:
    if bucket == "pre_window":
        return "potentially_predictive"
    if bucket == "during_window":
        return "during_move_not_proven_predictive"
    if bucket == "post_detection":
        return "post_detection_explanatory"
    return "unknown_timing"


def minutes_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds() / 60, 2)


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def chain_slug_from_signal(signal: dict) -> str:
    links = signal.get("links") if isinstance(signal.get("links"), dict) else {}
    url = links.get("dexscreener") or ""
    parts = str(url).split("/")
    try:
        idx = parts.index("dexscreener.com")
        return parts[idx + 1] if len(parts) > idx + 1 else ""
    except ValueError:
        return ""


def clean_term(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_handle(value) -> str:
    if value is None:
        return ""
    handle = str(value).strip().lower()
    if "/" in handle:
        handle = handle.rstrip("/").rsplit("/", 1)[-1]
    handle = re.sub(r"[^a-z0-9_]", "", handle.lstrip("@"))
    return f"@{handle}" if handle else ""


def dedupe(values: list) -> list:
    out = []
    seen = set()
    for value in values:
        key = str(value or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
