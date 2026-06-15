"""Timestamp helpers for X/Twitter snowflake IDs."""

from __future__ import annotations

import re
from datetime import datetime, timezone


TWITTER_EPOCH_MS = 1288834974657
STATUS_ID_RE = re.compile(r"/status(?:es)?/(\d+)")


def tweet_id_from_url(url: str | None) -> str:
    """Extract a tweet ID from a common X/Twitter status URL."""
    if not url:
        return ""
    match = STATUS_ID_RE.search(str(url))
    return match.group(1) if match else ""


def tweet_time_from_id(tweet_id: str | int | None) -> datetime | None:
    """Decode a tweet snowflake ID into its UTC creation time."""
    if tweet_id is None:
        return None
    try:
        value = int(str(tweet_id))
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None

    timestamp_ms = (value >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def tweet_time_from_url(url: str | None) -> datetime | None:
    """Decode the tweet creation time from a status URL."""
    return tweet_time_from_id(tweet_id_from_url(url))


def isoformat_z(value: datetime | None) -> str:
    """Return an ISO-8601 UTC string with Z suffix."""
    if value is None:
        return ""
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
