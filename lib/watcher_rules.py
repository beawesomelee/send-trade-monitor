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
    official_handles = []
    for account, config in sorted(watch_accounts.items()):
        if not isinstance(config, dict) or config.get("status") != "approved":
            continue

        handle = _handle_for_rule(account)
        if not handle:
            continue

        if _account_type(config) == "official_token_account":
            official_handles.append(handle)
            continue

        terms = _clean_terms(config.get("terms") or [])
        if not terms:
            continue

        chunks = _chunk_terms(handle, terms, max_rule_chars=max_rule_chars)
        for idx, chunk in enumerate(chunks, start=1):
            suffix = f":{idx}" if len(chunks) > 1 else ""
            rules.append({
                "value": _community_rule_value(handle, chunk),
                "tag": f"send_watcher:community:{handle}{suffix}",
            })

    for idx, chunk in enumerate(_chunk_official_handles(official_handles, max_rule_chars=max_rule_chars), start=1):
        rules.append({
            "value": _official_rule_value(chunk),
            "tag": f"send_watcher:official:{idx}",
        })

    return rules


def official_token_for_payload(payload: dict, state: dict | None = None) -> dict | None:
    """Return the mapped token when a payload matched an official account rule."""
    state = state if isinstance(state, dict) else load_watcher_state()
    watch_accounts = state.get("watch_accounts")
    if not isinstance(watch_accounts, dict):
        return None

    handles = _payload_author_handles(payload) + _matched_rule_handles(payload)
    for handle in handles:
        config = watch_accounts.get(f"@{handle}") or watch_accounts.get(handle)
        if not isinstance(config, dict):
            continue
        if _account_type(config) != "official_token_account":
            continue
        token = config.get("token") if isinstance(config.get("token"), dict) else {}
        if token.get("address") and token.get("chain_slug"):
            return {
                "symbol": token.get("symbol") or "",
                "address": token.get("address") or "",
                "chain_slug": token.get("chain_slug") or "",
                "dexscreener_url": token.get("dexscreener_url") or "",
                "account_type": "official_token_account",
                "source_account": f"@{handle}",
            }
    return None


def _chunk_terms(handle: str, terms: list[str], max_rule_chars: int) -> list[list[str]]:
    chunks = []
    current = []

    for term in terms:
        candidate = current + [term]
        if current and len(_community_rule_value(handle, candidate)) > max_rule_chars:
            chunks.append(current)
            current = [term]
        else:
            current = candidate

    if current:
        chunks.append(current)
    return chunks


def _chunk_official_handles(handles: list[str], max_rule_chars: int) -> list[list[str]]:
    chunks = []
    current = []
    for handle in sorted(_dedupe(handles)):
        candidate = current + [handle]
        if current and len(_official_rule_value(candidate)) > max_rule_chars:
            chunks.append(current)
            current = [handle]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _official_rule_value(handles: list[str]) -> str:
    return f"({' OR '.join(f'from:{handle}' for handle in handles)}) -is:retweet -is:reply"


def _community_rule_value(handle: str, terms: list[str]) -> str:
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


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        key = str(value or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _account_type(config: dict) -> str:
    raw = str(config.get("account_type") or "").strip().lower()
    if raw == "official_token_account":
        return "official_token_account"
    return "community_account"


def _matched_rule_handles(payload: dict) -> list[str]:
    rules = payload.get("matching_rules")
    if not isinstance(rules, list):
        return []

    handles = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        tag = str(rule.get("tag") or "")
        if not tag.startswith("send_watcher:"):
            continue
        parts = tag.split(":")
        if len(parts) >= 3 and parts[1] in {"official", "community"}:
            handle = parts[2]
        elif len(parts) >= 2:
            handle = parts[1]
        else:
            handle = ""
        handle = handle.split(":", 1)[0]
        clean = _handle_for_rule(handle)
        if clean and clean not in handles:
            handles.append(clean)
    return handles


def _payload_author_handles(payload: dict) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    author_id = str(data.get("author_id") or "")
    includes = payload.get("includes") if isinstance(payload.get("includes"), dict) else {}
    users = includes.get("users") if isinstance(includes.get("users"), list) else []
    handles = []
    for user in users:
        if not isinstance(user, dict):
            continue
        if author_id and str(user.get("id") or "") != author_id:
            continue
        handle = _handle_for_rule(user.get("username"))
        if handle and handle not in handles:
            handles.append(handle)
    return handles
