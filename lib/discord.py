"""Send daily summary to a Discord channel via webhook."""

import os
import time

import requests

RECENT_THRESHOLD_DAYS = 7
DISCORD_CONTENT_LIMIT = 2000


def send_summary(stats: dict, candidates: list[dict], sheet_url: str,
                 dry_run: bool = False):
    """Post a Discord-formatted summary to the configured webhook.

    Skips entirely if no actionable news (new + verified + dismissed all zero).
    """
    if stats.get("new_pending", 0) == 0:
        print("no new tokens this run, skipping Discord")
        return

    new_candidates = [c for c in candidates if c.get("_is_new")]
    msg = _build_message(new_candidates, sheet_url, stats)

    if dry_run:
        print(f"[dry-run] would post to Discord:\n{msg}")
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("WARNING: DISCORD_WEBHOOK_URL not set, skipping Discord")
        return

    r = requests.post(webhook, json={"content": msg}, timeout=15)
    if r.status_code in (200, 204):
        print("Discord message sent")
    else:
        print(f"Discord post failed: {r.status_code} {r.text}")


def _build_message(new_candidates: list[dict], sheet_url: str, stats: dict) -> str:
    """Format the Discord message focused on newly-found tokens.

    Format:
        **New Tokens to Verify**

        * SYMBOL: [Dexscreener Link](url)
        * SYMBOL: [Dexscreener Link](url)

        full list: <sheet_url>
    """
    lines = []

    if new_candidates:
        lines.append("**New Tokens to Verify**")
        lines.append("")
        now_ms = int(time.time() * 1000)
        recent_cutoff_ms = now_ms - RECENT_THRESHOLD_DAYS * 86400 * 1000
        for c in new_candidates:
            sym = c.get("symbol", "?")
            url = c.get("dexscreener_url", "")
            created = c.get("pair_created_at_ms")
            age_tag = ""
            if created and created >= recent_cutoff_ms:
                age_days = max(1, (now_ms - created) // (86400 * 1000))
                age_tag = f" **({age_days}d old)**"
            lines.append(f"* {sym}{age_tag}: [Dexscreener Link](<{url}>)")
        lines.append("")

    # Auxiliary status lines (only when other things changed but no new tokens)
    auto_verified = stats.get("auto_verified", 0)
    dismissed = stats.get("dismissed", 0)
    if auto_verified or dismissed:
        if not new_candidates:
            lines.append("**Send.Trade update**")
            lines.append("")
        if auto_verified:
            lines.append(f"{auto_verified} now verified on Send.Trade (removed from list)")
        if dismissed:
            lines.append(f"{dismissed} dismissed")
        lines.append("")

    lines.append(f"full list: {sheet_url}")
    return "\n".join(lines)


def send_movement_alert(movers: list[dict], sheet_url: str = "", dry_run: bool = False):
    """Send a movement alert (pumps + dumps) summary to Discord."""
    if not movers:
        return

    pumps = [m for m in movers if m["direction"] == "pump"]
    dumps = [m for m in movers if m["direction"] == "dump"]

    # group by window so the header reads "Pumps (1h)" or "Pumps (6h)" correctly
    def _row(m):
        sym = m.get("symbol") or "?"
        change = m["price_change_pct"]
        lore = m.get("lore") or ""
        line = f"**{sym}** {change:+.0f}% - [Dexscreener](<{m['dexscreener_url']}>)"
        if lore:
            line += f"\n{lore}"
        # Surface the Send.Trade lore ID if auto-posted, so Austin can review
        # and delete from the admin panel if the auto-lore isn't relevant.
        stl = m.get("send_trade_lore") or {}
        if isinstance(stl, dict):
            lid = stl.get("id") or stl.get("_id")
            if lid:
                line += f"\n_posted to send.trade — lore id `{lid}` (delete via admin if not relevant)_"
        return line

    window_label = (pumps + dumps)[0]["price_change_window"][1:] + "h"
    lines = []
    if pumps:
        lines.append(f"**🚀 Pumps ({window_label})**")
        lines.append("")
        for m in pumps:
            lines.append(_row(m))
            lines.append("")  # blank line between entries

    if dumps:
        lines.append(f"**🔻 Dumps ({window_label})**")
        lines.append("")
        for m in dumps:
            lines.append(_row(m))
            lines.append("")

    msg = "\n".join(lines).rstrip()

    if dry_run:
        print(f"[dry-run] would post movement alert to Discord:\n{msg}")
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("WARNING: DISCORD_WEBHOOK_URL not set, skipping movement alert")
        return

    r = requests.post(webhook, json={"content": msg}, timeout=15)
    if r.status_code in (200, 204):
        print("Discord movement alert sent")
    else:
        print(f"Discord post failed: {r.status_code} {r.text}")


def send_x_watcher_hit(payload: dict, ingested_at: str, dry_run: bool = False):
    """Post a compact raw X watcher hit notification to Discord."""
    msg = _build_x_watcher_hit_message(payload, ingested_at)

    if dry_run:
        print(f"[dry-run] would post raw X watcher hit to Discord:\n{msg}")
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("WARNING: DISCORD_WEBHOOK_URL not set, skipping raw X watcher hit")
        return

    payload = {"content": msg, "allowed_mentions": {"parse": []}}
    try:
        r = requests.post(webhook, json=payload, timeout=15)
    except requests.exceptions.RequestException as exc:
        print(f"WARNING: Discord raw X watcher hit failed: {exc}")
        return
    if r.status_code in (200, 204):
        print("Discord raw X watcher hit sent")
    else:
        print(f"Discord post failed: {r.status_code} {r.text}")


def send_verified_x_watcher_hit(
    payload: dict,
    ingested_at: str,
    verification: dict,
    dry_run: bool = False,
):
    """Post a price-verified X watcher notification to Discord."""
    msg = _build_verified_x_watcher_hit_message(payload, ingested_at, verification)

    if dry_run:
        print(f"[dry-run] would post verified X watcher hit to Discord:\n{msg}")
        return

    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("WARNING: DISCORD_WEBHOOK_URL not set, skipping verified X watcher hit")
        return

    payload = {"content": msg, "allowed_mentions": {"parse": []}}
    try:
        r = requests.post(webhook, json=payload, timeout=15)
    except requests.exceptions.RequestException as exc:
        print(f"WARNING: Discord verified X watcher hit failed: {exc}")
        return
    if r.status_code in (200, 204):
        print("Discord verified X watcher hit sent")
    else:
        print(f"Discord post failed: {r.status_code} {r.text}")


def _build_x_watcher_hit_message(payload: dict, ingested_at: str) -> str:
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    tweet_id = str(data.get("id") or "")
    text = str(data.get("text") or "")
    user = _x_watcher_author(payload)
    username = str(user.get("username") or "") if user else ""
    display_name = str(user.get("name") or "") if user else ""
    author_label = _x_watcher_author_label(display_name, username, data.get("author_id"))
    raw_rules = payload.get("matching_rules")
    rule_tags = _x_watcher_rule_tags(raw_rules if isinstance(raw_rules, list) else [])
    tweet_url = _x_tweet_url(tweet_id, username)

    lines = [
        "**Raw X watcher hit**",
        "_not yet AI-classified lore; review before treating as signal._",
        f"Ingested: `{ingested_at}`",
    ]
    if author_label:
        lines.append(f"Author: {author_label}")
    if rule_tags:
        lines.append(f"Rules: {', '.join(rule_tags)}")
    if tweet_url:
        lines.append(f"Tweet: <{tweet_url}>")
    if text:
        lines.extend(["", _truncate_text(text, 1200)])

    return _truncate_text("\n".join(lines), DISCORD_CONTENT_LIMIT)


def _build_verified_x_watcher_hit_message(
    payload: dict,
    ingested_at: str,
    verification: dict,
) -> str:
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    tweet_id = str(data.get("id") or "")
    text = str(data.get("text") or "")
    user = _x_watcher_author(payload)
    username = str(user.get("username") or "") if user else ""
    display_name = str(user.get("name") or "") if user else ""
    author_label = _x_watcher_author_label(display_name, username, data.get("author_id"))
    tweet_url = _x_tweet_url(tweet_id, username)
    token = verification.get("token") or {}
    market = verification.get("market") or {}
    symbol = market.get("symbol") or token.get("symbol") or "?"
    direction = verification.get("direction") or "movement"
    h1 = market.get("price_change_h1_pct")
    h6 = market.get("price_change_h6_pct")
    ds_url = market.get("dexscreener_url") or token.get("dexscreener_url") or ""

    lines = [
        "**Verified X watcher hit**",
        f"Token: **{symbol}** `{direction}`",
        f"Move: h1 `{_format_pct(h1)}` h6 `{_format_pct(h6)}`",
        f"Ingested: `{ingested_at}`",
    ]
    if author_label:
        lines.append(f"Author: {author_label}")
    if tweet_url:
        lines.append(f"Tweet: <{tweet_url}>")
    if ds_url:
        lines.append(f"Dexscreener: <{ds_url}>")
    if text:
        lines.extend(["", _truncate_text(text, 1000)])

    return _truncate_text("\n".join(lines), DISCORD_CONTENT_LIMIT)


def _x_watcher_author(payload: dict) -> dict:
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    author_id = str(data.get("author_id") or "")
    raw_includes = payload.get("includes")
    includes = raw_includes if isinstance(raw_includes, dict) else {}
    raw_users = includes.get("users")
    users = raw_users if isinstance(raw_users, list) else []
    for user in users:
        if not isinstance(user, dict):
            continue
        if author_id and str(user.get("id") or "") == author_id:
            return user
    return users[0] if users and isinstance(users[0], dict) else {}


def _x_watcher_author_label(name: str, username: str, author_id: object) -> str:
    if name and username:
        return f"{_truncate_text(name, 80)} (@{_truncate_text(username, 40)})"
    if username:
        return f"@{_truncate_text(username, 40)}"
    if name:
        return _truncate_text(name, 80)
    if author_id:
        return f"author_id `{author_id}`"
    return ""


def _x_watcher_rule_tags(rules: list) -> list[str]:
    tags = []
    for rule in rules:
        if isinstance(rule, dict):
            tag = str(rule.get("tag") or rule.get("id") or "").strip()
        else:
            tag = str(rule).strip()
        if tag:
            tags.append(_truncate_text(tag, 80))
    return tags[:10]


def _x_tweet_url(tweet_id: str, username: str) -> str:
    if not tweet_id:
        return ""
    handle = username or "i"
    return f"https://x.com/{handle}/status/{tweet_id}"


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _format_pct(value) -> str:
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "?"
