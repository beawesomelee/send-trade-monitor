"""Send daily summary to Telegram."""

import os
import datetime as dt

import requests


def send_summary(stats: dict, candidates: list[dict], sheet_url: str,
                 top_n: int = 5, dry_run: bool = False):
    """Send the daily summary message to Austin.

    Skips sending when there's nothing actionable (no new tokens, no auto-verified,
    no dismissed) — avoids spamming when running every 2 hours.
    """
    today = dt.date.today().isoformat()

    if stats.get("new_pending", 0) == 0:
        print("no new tokens this run, skipping Telegram")
        return

    msg = _build_message(stats, candidates, sheet_url, today, top_n)

    if dry_run:
        print(f"[dry-run] would send Telegram message:\n{msg}")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("WARNING: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set, skipping message")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()
    print("Telegram message sent")


def _build_message(stats: dict, candidates: list[dict], sheet_url: str,
                   today: str, top_n: int) -> str:
    new_pending = stats.get("new_pending", 0)
    still_pending = stats.get("still_pending", 0)
    auto_verified = stats.get("auto_verified", 0)
    dismissed = stats.get("dismissed", 0)

    now_utc = dt.datetime.utcnow()
    greeting = "gm" if now_utc.hour < 16 else "Send.Trade update"
    lines = [f"{greeting}, {today}"]
    lines.append("")
    if new_pending:
        lines.append(f"{new_pending} new pending token{'s' if new_pending != 1 else ''}")
    if auto_verified:
        lines.append(f"{auto_verified} now verified on Send.Trade (removed from list)")
    if dismissed:
        lines.append(f"{dismissed} dismissed")

    new_candidates = [c for c in candidates if c.get("_is_new")]
    if new_candidates:
        lines.append("")
        lines.append(f"top {min(top_n, len(new_candidates))} new candidates by 24h volume:")
        for i, c in enumerate(new_candidates[:top_n], 1):
            mc = _fmt_usd(c["market_cap_usd"])
            vol = _fmt_usd(c["volume_24h_usd"])
            chain = c["chain_slug"].capitalize()
            lines.append(f"{i}. {c['symbol']} ({chain}), {mc} MC, {vol} vol")

    lines.append("")
    lines.append(f"full list: {sheet_url}")

    return "\n".join(lines)


def _fmt_usd(val: float) -> str:
    if val >= 1_000_000:
        return f"${val / 1_000_000:.1f}M"
    elif val >= 1_000:
        return f"${val / 1_000:.0f}K"
    return f"${val:.0f}"
