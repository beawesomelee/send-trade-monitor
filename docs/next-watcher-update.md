# Next Planned Update: Verified X Watcher

## Current Model

The movement scan remains the hourly learning loop:

```text
movement_scan.py
-> detect pumps/dumps
-> explain movement with lore
-> estimate movement start time
-> compare source tweet time against movement start
-> approve/reject watcher accounts using timing evidence
-> approved watcher accounts become X filtered-stream rules
```

The X watcher is the cheaper real-time loop:

```text
X filtered stream hit
-> store raw tweet
-> match tweet to a known token
-> verify current price movement for that token
-> Discord only if movement is verified
```

This means watcher tweets are treated as cheap movement triggers, not trusted alpha by themselves.

## Runtime Boundary

GitHub Actions should keep running the scheduled movement scan and rule sync.

The X stream watcher should run on one long-lived host, such as a VPS, because X expects one active filtered-stream connection for the app.

## Safety Rules

- Store every unique stream hit locally for replay/debug.
- Do not post raw stream hits to Discord by default.
- Do not auto-approve watcher accounts from verifier output.
- Auto-approval happens only after movement scan enrichment, using tweet timing versus estimated movement start.
- Approval requires direct tweet evidence from the candidate account.
- Pre-start tweets and tweets within the early movement window can become rule-eligible.
- Late reactions, stale pre-move tweets, and accounts without direct tweet evidence are rejected.
- Discord alerts require all of:
  - matched known token
  - price movement language in tweet text
  - current token market data above liquidity/volume/MC floors
  - h1/h6 movement above configured watcher thresholds
- Ambiguous symbols or unmatched tokens are stored only.
- Approved rule terms from movement events are also used as token aliases during verification, so project-name hits like `opengradient` can still resolve to `OPG`.

## Approval Command

Dry-run approval labels against the stored dataset:

```bash
python3 scripts/apply_watcher_approvals.py
```

Apply labels to `data/movement_events.json` and `data/watcher.json`:

```bash
python3 scripts/apply_watcher_approvals.py --apply
```

Preview the X rules that would be synced:

```bash
python3 scripts/dry_run_watcher_rules.py
```

## MVP Command

For a bounded VPS smoke test:

```bash
python3 scripts/run_x_watcher.py --max-seconds 300 --max-posts 5 --discord-dry-run
```

For raw debugging only:

```bash
python3 scripts/run_x_watcher.py --max-seconds 300 --max-posts 5 --raw-discord --no-verify --discord-dry-run
```

Production should run one process under a supervisor such as systemd.
