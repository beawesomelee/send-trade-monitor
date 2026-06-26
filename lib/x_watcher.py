"""Ingest matching posts from the X filtered stream."""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import requests

from lib.discord import send_verified_x_watcher_hit, send_x_watcher_hit
from lib.watcher_outcomes import OUTCOMES_FILE, record_watcher_outcome
from lib.watcher_verify import verify_watcher_hit
from lib.x_rules import XRulesError


STREAM_ENDPOINT = "https://api.x.com/2/tweets/search/stream"
ROOT = Path(__file__).resolve().parent.parent
TWEETS_FILE = ROOT / "data" / "watcher_tweets.jsonl"
STATE_FILE = ROOT / "data" / "watcher_ingest_state.json"
LOCK_FILE = ROOT / "data" / "watcher_stream.lock"
MAX_SEEN_IDS = 5000


STREAM_PARAMS = {
    "tweet.fields": ",".join([
        "author_id",
        "conversation_id",
        "created_at",
        "entities",
        "lang",
        "possibly_sensitive",
        "referenced_tweets",
    ]),
    "expansions": ",".join([
        "author_id",
        "referenced_tweets.id",
        "referenced_tweets.id.author_id",
    ]),
    "user.fields": ",".join([
        "id",
        "name",
        "username",
        "verified",
        "verified_type",
    ]),
}


def stream_watcher_posts(
    bearer_token: str,
    *,
    max_posts: int | None = None,
    max_seconds: int | None = None,
    tweets_path: Path = TWEETS_FILE,
    state_path: Path = STATE_FILE,
    lock_path: Path = LOCK_FILE,
    connect_timeout: int = 10,
    read_timeout: int = 90,
    discord: bool = True,
    discord_dry_run: bool = False,
    raw_discord: bool = False,
    verify_hits: bool = True,
    outcomes_path: Path = OUTCOMES_FILE,
) -> dict:
    """Read filtered-stream posts and persist unseen payloads."""
    state = _load_state(state_path)
    recent_seen_ids = _seen_tweet_ids(state)
    seen_ids = set(recent_seen_ids)
    started = time.monotonic()
    deadline = started + max_seconds if max_seconds is not None else None
    stored = 0
    verified = 0
    unverified = 0
    skipped_duplicates = 0
    connection_errors = 0
    reconnects = 0

    with _exclusive_stream_lock(lock_path):
        while not _deadline_passed(deadline):
            remaining = _remaining_seconds(deadline)
            current_read_timeout = _bounded_read_timeout(read_timeout, remaining)
            try:
                for payload in iter_stream_payloads(
                    bearer_token,
                    connect_timeout=connect_timeout,
                    read_timeout=current_read_timeout,
                    stop_at_monotonic=deadline,
                ):
                    if _deadline_passed(deadline):
                        break

                    tweet_id = _tweet_id(payload)
                    if not tweet_id:
                        continue
                    if tweet_id in seen_ids:
                        skipped_duplicates += 1
                        continue

                    seen_ids.add(tweet_id)
                    recent_seen_ids.append(tweet_id)
                    ingested_at = _now_iso()
                    _append_tweet(tweets_path, payload, ingested_at=ingested_at)
                    stored += 1
                    state["last_seen_tweet_id"] = tweet_id
                    state["last_seen_at"] = ingested_at
                    state["last_matching_rules"] = payload.get("matching_rules") or []
                    _save_state(state_path, state, recent_seen_ids)
                    verification = {}
                    if verify_hits:
                        verification = verify_watcher_hit(payload)
                        state["last_verification"] = verification
                        _save_state(state_path, state, recent_seen_ids)
                    if verification.get("verified"):
                        verified += 1
                        record_watcher_outcome(payload, verification, ingested_at, path=outcomes_path)
                        if discord:
                            send_verified_x_watcher_hit(
                                payload,
                                ingested_at,
                                verification,
                                dry_run=discord_dry_run,
                            )
                    elif verification:
                        unverified += 1
                    if raw_discord:
                        send_x_watcher_hit(payload, ingested_at, dry_run=discord_dry_run)

                    if max_posts is not None and stored >= max_posts:
                        break
            except requests.exceptions.RequestException as exc:
                connection_errors += 1
                state["last_error"] = str(exc)[:500]
                state["last_error_at"] = _now_iso()

            if max_posts is not None and stored >= max_posts:
                break
            if _deadline_passed(deadline):
                break

            reconnects += 1
            time.sleep(_backoff_seconds(reconnects, deadline))

    return {
        "stored": stored,
        "verified": verified,
        "unverified": unverified,
        "skipped_duplicates": skipped_duplicates,
        "connection_errors": connection_errors,
        "reconnects": reconnects,
        "last_seen_tweet_id": state.get("last_seen_tweet_id") or "",
        "tweets_path": str(tweets_path),
        "state_path": str(state_path),
    }


def iter_stream_payloads(
    bearer_token: str,
    *,
    connect_timeout: int = 10,
    read_timeout: int = 90,
    stop_at_monotonic: float | None = None,
) -> Iterator[dict]:
    """Yield decoded JSON objects from the X filtered stream."""
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "User-Agent": "send-trade-monitor-x-watcher",
    }
    with requests.get(
        STREAM_ENDPOINT,
        params=STREAM_PARAMS,
        headers=headers,
        timeout=(connect_timeout, read_timeout),
        stream=True,
    ) as response:
        if response.status_code < 200 or response.status_code >= 300:
            raise XRulesError(
                f"X stream HTTP {response.status_code}: {response.text[:1000]}"
            )

        for line in response.iter_lines(decode_unicode=True):
            if _deadline_passed(stop_at_monotonic):
                return
            if line is None or not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict):
                yield payload


def _append_tweet(path: Path, payload: dict, ingested_at: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ingested_at": ingested_at or _now_iso(),
        "tweet_id": _tweet_id(payload),
        "matching_rules": payload.get("matching_rules") or [],
        "payload": payload,
    }
    with path.open("a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"seen_tweet_ids": []}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {"seen_tweet_ids": []}
    return data if isinstance(data, dict) else {"seen_tweet_ids": []}


def _save_state(path: Path, state: dict, seen_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["seen_tweet_ids"] = seen_ids[-MAX_SEEN_IDS:]
    state["updated_at"] = _now_iso()
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _seen_tweet_ids(state: dict) -> list[str]:
    ids = state.get("seen_tweet_ids") or []
    if not isinstance(ids, list):
        return []
    return [str(tweet_id) for tweet_id in ids if tweet_id]


def _tweet_id(payload: dict) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return str(data.get("id") or "")


def _deadline_passed(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _remaining_seconds(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.1, deadline - time.monotonic())


def _bounded_read_timeout(read_timeout: int, remaining: float | None) -> float:
    if remaining is None:
        return read_timeout
    return max(1.0, min(float(read_timeout), remaining))


def _backoff_seconds(reconnects: int, deadline: float | None) -> float:
    delay = min(30.0, float(2 ** min(reconnects, 5)))
    remaining = _remaining_seconds(deadline)
    if remaining is None:
        return delay
    return max(0.0, min(delay, remaining))


@contextmanager
def _exclusive_stream_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise XRulesError(
                f"Another X watcher stream appears to be running; lock is held at {path}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
