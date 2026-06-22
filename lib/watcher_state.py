"""Small JSON state file for watcher inputs."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from lib.movement_events import enrich_signal_timing


SCRIPT_DIR = Path(__file__).resolve().parent.parent
WATCHER_FILE = SCRIPT_DIR / "data" / "watcher.json"
SCHEMA_VERSION = "watcher_state_v1"


def record_signals(movers: list[dict], detected_at: str | None = None) -> list[dict]:
    """Append movement/lore packets as watcher-ready signals."""
    if not movers:
        return []

    state = _load_state()
    detected_at = detected_at or _now_iso()
    seen = {s.get("id") for s in state["signals"]}

    created = []
    for mover in movers:
        signal = _build_signal(mover, detected_at)
        if signal["id"] in seen:
            continue
        state["signals"].append(signal)
        _merge_watch_accounts(state, signal)
        seen.add(signal["id"])
        created.append(signal)

    if created:
        state["updated_at"] = _now_iso()
        WATCHER_FILE.parent.mkdir(parents=True, exist_ok=True)
        WATCHER_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")

    return created


def _load_state() -> dict:
    empty = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": "",
        "signals": [],
        "watch_accounts": {},
        "rules": [],
    }
    if not WATCHER_FILE.exists():
        return empty
    try:
        data = json.loads(WATCHER_FILE.read_text())
    except Exception:
        return empty
    if not isinstance(data, dict):
        return empty
    out = dict(data)
    out["schema_version"] = out.get("schema_version") or SCHEMA_VERSION
    out["updated_at"] = out.get("updated_at") or ""
    out["signals"] = out.get("signals") if isinstance(out.get("signals"), list) else []
    out["watch_accounts"] = (
        out.get("watch_accounts") if isinstance(out.get("watch_accounts"), dict) else {}
    )
    out["rules"] = out.get("rules") if isinstance(out.get("rules"), list) else []
    return out


def _build_signal(mover: dict, detected_at: str) -> dict:
    lore_packet = mover.get("lore_packet") if isinstance(mover.get("lore_packet"), dict) else {}
    watcher_clues = lore_packet.get("watcher_clues") if isinstance(lore_packet.get("watcher_clues"), dict) else {}

    signal = {
        "id": _signal_id(mover, detected_at),
        "detected_at": detected_at,
        "token": {
            "symbol": mover.get("symbol") or "",
            "address": mover.get("address") or "",
        },
        "movement": {
            "direction": mover.get("direction") or "",
            "window": mover.get("price_change_window") or "",
            "change_pct": _round_pct(mover.get("price_change_pct")),
        },
        "lore": mover.get("lore") or "",
        "references": lore_packet.get("references") if isinstance(lore_packet.get("references"), list) else [],
        "watcher_clues": {
            "accounts": watcher_clues.get("accounts") if isinstance(watcher_clues.get("accounts"), list) else [],
            "phrases": watcher_clues.get("phrases") if isinstance(watcher_clues.get("phrases"), list) else [],
            "keywords": watcher_clues.get("keywords") if isinstance(watcher_clues.get("keywords"), list) else [],
            "catalysts": watcher_clues.get("catalysts") if isinstance(watcher_clues.get("catalysts"), list) else [],
        },
        "links": {
            "dexscreener": mover.get("dexscreener_url") or "",
        },
    }
    return enrich_signal_timing(signal)


def _signal_id(mover: dict, detected_at: str) -> str:
    body = "|".join([
        mover.get("address") or "",
        mover.get("symbol") or "",
        mover.get("direction") or "",
        mover.get("price_change_window") or "",
        str(_round_pct(mover.get("price_change_pct"))),
        detected_at[:13],
    ])
    return "signal_" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def _merge_watch_accounts(state: dict, signal: dict):
    accounts = _signal_accounts(signal)
    terms = _signal_terms(signal)
    if not accounts or not terms:
        return

    watch_accounts = state.setdefault("watch_accounts", {})
    for account in accounts:
        existing = watch_accounts.get(account)
        if not isinstance(existing, dict):
            existing = {}

        existing_terms = existing.get("terms") if isinstance(existing.get("terms"), list) else []
        source_ids = existing.get("source_signal_ids")
        if not isinstance(source_ids, list):
            source_ids = []

        watch_accounts[account] = {
            "status": existing.get("status") or "pending",
            "terms": _dedupe(existing_terms + terms)[:120],
            "source_signal_ids": _dedupe(source_ids + [signal["id"]]),
            "reason_added": existing.get("reason_added") or _reason_added(signal),
            "updated_at": _now_iso(),
        }


def _signal_accounts(signal: dict) -> list[str]:
    clues = signal.get("watcher_clues") if isinstance(signal.get("watcher_clues"), dict) else {}
    refs = signal.get("references") if isinstance(signal.get("references"), list) else []

    accounts = []
    accounts.extend(clues.get("accounts") if isinstance(clues.get("accounts"), list) else [])
    for ref in refs:
        if isinstance(ref, dict):
            accounts.append(ref.get("author_handle"))

    return [_normalize_handle(a) for a in _dedupe(accounts) if _normalize_handle(a)]


def _signal_terms(signal: dict) -> list[str]:
    clues = signal.get("watcher_clues") if isinstance(signal.get("watcher_clues"), dict) else {}
    token = signal.get("token") if isinstance(signal.get("token"), dict) else {}
    symbol = _clean_term(token.get("symbol"))

    terms = []
    if symbol:
        terms.extend([symbol, f"${symbol}"])
    terms.extend(clues.get("keywords") if isinstance(clues.get("keywords"), list) else [])
    terms.extend(clues.get("phrases") if isinstance(clues.get("phrases"), list) else [])

    catalysts = clues.get("catalysts") if isinstance(clues.get("catalysts"), list) else []
    terms.extend(str(c).replace("_", " ") for c in catalysts)

    cleaned = []
    for term in terms:
        t = _clean_term(term)
        if t and len(t) >= 3:
            cleaned.append(t)
    return _dedupe(cleaned)[:80]


def _reason_added(signal: dict) -> str:
    raw_token = signal.get("token")
    token = raw_token if isinstance(raw_token, dict) else {}
    raw_movement = signal.get("movement")
    movement = raw_movement if isinstance(raw_movement, dict) else {}
    symbol = token.get("symbol") or "unknown token"
    change_pct = movement.get("change_pct")
    window = movement.get("window") or "unknown window"
    return (
        f"Suggested by movement explanation for {symbol}; "
        f"movement {change_pct}% over {window}; requires manual approval before rule sync."
    )


def _normalize_handle(value) -> str:
    if value is None:
        return ""
    handle = str(value).strip().lower()
    if not handle:
        return ""
    handle = handle.rsplit("/", 1)[-1] if "/" in handle else handle
    handle = handle.lstrip("@")
    handle = re.sub(r"[^a-z0-9_]", "", handle)
    return f"@{handle}" if handle else ""


def _clean_term(value) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return re.sub(r"\s+", " ", text)


def _dedupe(values: list) -> list:
    out = []
    seen = set()
    for value in values:
        if value is None:
            continue
        key = str(value).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round_pct(value) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0
