"""Send daily summary to a Discord channel via webhook."""

import os

import requests


def send_summary(stats: dict, candidates: list[dict], sheet_url: str,
                 dry_run: bool = False):
    """Post a Discord-formatted summary to the configured webhook.

    Like Telegram, skips entirely if no actionable news (new + verified +
    dismissed all zero).
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
        for c in new_candidates:
            sym = c.get("symbol", "?")
            url = c.get("dexscreener_url", "")
            lines.append(f"* {sym}: [Dexscreener Link](<{url}>)")
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
