# Send.Trade Verification Monitor

Daily scan that surfaces Base + Solana tokens meeting Send.Trade verification
criteria (>$1M MC, >$1M 24h volume) but not yet verified.

## Modes

| Mode | When to use | Source |
|---|---|---|
| `--backfill` | First run, or when sheet was wiped | GeckoTerminal pools, exhaustive (~3-5 min) |
| default (incremental) | Daily cron | DexScreener: refresh known + discover new launches (<7 days old) |

The daily cron runs incremental. Backfill is manual via workflow_dispatch.

## First-time setup

1. Create a Telegram bot via @BotFather, save the token.
2. Get your Telegram chat ID from @userinfobot.
3. Create a Google Cloud service account with Sheets API enabled; download JSON.
4. Create the target Google Sheet, share with the service account email (Editor).
5. Put the sheet ID in [config.json](config.json) under `google_sheet.sheet_id`.
6. Add secrets to the GitHub repo (Settings → Secrets and variables → Actions):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GOOGLE_SHEETS_CREDENTIALS` (the full service account JSON)
   - `ALCHEMY_API_KEY` (optional, for Base decimals)
   - `HELIUS_API_KEY` (optional, for Solana decimals)
7. Trigger the first backfill: GitHub Actions → Daily Send.Trade Scan → Run workflow → set `backfill: true`.

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env

python daily_scan.py --backfill --dry-run   # first run, prints to console
python daily_scan.py --dry-run              # incremental dry-run
python daily_scan.py                        # write to sheet + Telegram
```

## Status lifecycle (in sheet)

- `pending` — system default; resurfaces daily until acted on
- `verified` — system auto-sets when token appears in Send.Trade API
- `dismissed` — Austin manually sets in sheet; permanently excluded

## Files

- [daily_scan.py](daily_scan.py) — main entrypoint
- [lib/dexscreener.py](lib/dexscreener.py) — GeckoTerminal (backfill) + DexScreener (incremental) discovery
- [lib/send_trade.py](lib/send_trade.py) — verified-list fetcher
- [lib/decimals.py](lib/decimals.py) — on-chain decimals lookup with cache
- [lib/sheets.py](lib/sheets.py) — gspread upsert with status lifecycle
- [lib/telegram.py](lib/telegram.py) — summary message sender
- [config.json](config.json) — thresholds, endpoints, sheet ID
- [.github/workflows/daily.yml](.github/workflows/daily.yml) — cron at 13:12 UTC
