# Next Planned Update: X Watcher Ingest

## Current State

The movement workflow can now build and sync watcher rules to X:

```text
movement_scan.py
-> data/watcher.json watch_accounts
-> scripts/sync_watcher_rules.py --apply
-> X filtered-stream rules
```

The next update is the runtime that listens to those rules and captures matching posts.

## Goal

Run a single X filtered-stream watcher that stores matching posts for later classification and Lore generation.

The watcher should not poll accounts. It should keep one stream connection open and receive posts pushed by X when they match the active rule list.

## Planned Scope

1. Add watcher ingest script
   - Connect to `GET /2/tweets/search/stream`.
   - Include tweet fields, matching rule tags, author expansion, and referenced tweet expansion.
   - Support bounded test runs with `--max-seconds` and `--max-posts`.

2. Store raw matching posts
   - Write raw stream records to `data/watcher_tweets.jsonl`.
   - Include `ingested_at`, `tweet_id`, `matching_rules`, and original payload.
   - Keep raw ingest files out of git by default.

3. Track ingest state
   - Store local checkpoint/dedupe data in `data/watcher_ingest_state.json`.
   - Deduplicate by tweet ID.
   - Track `last_seen_tweet_id`, `last_seen_at`, and last matching rules.

4. Handle runtime behavior
   - Reconnect on stream disconnects.
   - Use bounded read timeouts for tests.
   - Add basic backoff.
   - Avoid multiple simultaneous stream connections for the same X app.

## Not In Scope Yet

- Tweet classification.
- Token matching.
- Context enrichment for quote/reply/thread.
- Publishing watcher-derived Lore to Send.Trade.
- Deploying the watcher as a production daemon.
- GitHub Actions continuous runtime.

GitHub Actions is not the right long-running host for this stream because jobs are temporary and X expects a single stream connection per app.

## Deployment Decision Needed

Pick one host for the continuous watcher process:

- small VPS
- EC2
- Render worker
- Fly.io machine
- Railway worker
- local machine for short testing only

The MVP process command should look like:

```bash
python3 scripts/run_x_watcher.py --max-seconds 86400 --max-posts 1000
```

For a real daemon, we may later remove the caps and let the process manager handle restarts.

## Acceptance Criteria

- `python3 scripts/run_x_watcher.py --max-seconds 60 --max-posts 1` exits cleanly.
- Matching posts are appended to `data/watcher_tweets.jsonl`.
- Duplicate tweet IDs are not stored twice.
- Ingest state is updated only when posts are seen.
- Raw watcher ingest files are ignored by git.
- Existing movement scan and X rule sync still pass syntax checks.

## Follow-Up Update

After raw ingest works, the next update should be:

```text
raw tweet
-> enrich quote/reply/thread context
-> deterministic token candidate matching
-> AI review
-> accepted Lore item
```

That should be built as a replayable pipeline that can process stored `watcher_tweets.jsonl` rows before it runs live.
