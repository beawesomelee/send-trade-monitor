"""Small JSON state file for watcher inputs."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


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
    return {
        "schema_version": data.get("schema_version") or SCHEMA_VERSION,
        "updated_at": data.get("updated_at") or "",
        "signals": data.get("signals") if isinstance(data.get("signals"), list) else [],
        "rules": data.get("rules") if isinstance(data.get("rules"), list) else [],
    }


def _build_signal(mover: dict, detected_at: str) -> dict:
    lore_packet = mover.get("lore_packet") if isinstance(mover.get("lore_packet"), dict) else {}
    watcher_clues = lore_packet.get("watcher_clues") if isinstance(lore_packet.get("watcher_clues"), dict) else {}

    return {
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round_pct(value) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0
