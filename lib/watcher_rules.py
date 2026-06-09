"""Build X filtered-stream rules from watcher state."""

import json
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent.parent
WATCHER_FILE = SCRIPT_DIR / "data" / "watcher.json"
MAX_RULE_CHARS = 900


def load_watcher_state(path: Path = WATCHER_FILE) -> dict:
    if not path.exists():
        return {"watch_accounts": {}}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"watch_accounts": {}}
    return data if isinstance(data, dict) else {"watch_accounts": {}}


def build_desired_rules(state: dict, max_rule_chars: int = MAX_RULE_CHARS) -> list[dict]:
    """Return X stream rules from state["watch_accounts"]."""
    watch_accounts = state.get("watch_accounts")
    if not isinstance(watch_accounts, dict):
        return []

    rules = []
    for account, config in sorted(watch_accounts.items()):
        if not isinstance(config, dict) or config.get("status") != "active":
            continue

        handle = _handle_for_rule(account)
        if not handle:
            continue

        terms = _clean_terms(config.get("terms") or [])
        if not terms:
            continue

        chunks = _chunk_terms(handle, terms, max_rule_chars=max_rule_chars)
        for idx, chunk in enumerate(chunks, start=1):
            suffix = f":{idx}" if len(chunks) > 1 else ""
            rules.append({
                "value": _rule_value(handle, chunk),
                "tag": f"send_watcher:{handle}{suffix}",
            })

    return rules


def _chunk_terms(handle: str, terms: list[str], max_rule_chars: int) -> list[list[str]]:
    chunks = []
    current = []

    for term in terms:
        candidate = current + [term]
        if current and len(_rule_value(handle, candidate)) > max_rule_chars:
            chunks.append(current)
            current = [term]
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def _rule_value(handle: str, terms: list[str]) -> str:
    return f"from:{handle} ({' OR '.join(_format_term(t) for t in terms)}) -is:retweet"


def _format_term(term: str) -> str:
    term = term.strip()
    if " " in term or ":" in term:
        return '"' + term.replace('"', "") + '"'
    return term.replace('"', "")


def _handle_for_rule(value) -> str:
    if value is None:
        return ""
    handle = str(value).strip().lower().lstrip("@")
    return "".join(c for c in handle if c.isalnum() or c == "_")


def _clean_terms(values) -> list[str]:
    out = []
    seen = set()
    for value in values:
        if value is None:
            continue
        term = str(value).strip().lower()
        if len(term) < 3 or term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out
