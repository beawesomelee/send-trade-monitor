# Send.Trade Verification Monitor — Build Skill

You are building a daily monitor that surfaces tokens on Base + Solana which meet Send.Trade's verification criteria but are not yet verified. Austin reviews the daily output, decides what to verify (manual action on the Send.Trade admin side), and uses the data fields directly as the verification payload.

This is a **fresh build**. Mirror the conventions in `../hydrex-base-monitor/` (cron-driven GitHub Action, candidates.csv versioned commit, Python 3.11) but the data flow and outputs are different.

---

## 1. Goal

Every morning at **8am ET (13:00 UTC)**, scan DexScreener for Base and Solana tokens that:

- Have **>$1M market cap** AND **>$1M 24-hour volume**
- Are **not already on Send.Trade's verified list**
- Are **not previously dismissed by Austin**

Surface the candidates via two channels:

1. **Telegram message** to Austin with a top-N summary and the Google Sheets link
2. **Google Sheet** updated with the full data (one row per candidate, status column tracks lifecycle)

Austin reviews the sheet, decides which tokens to verify (manual workflow on Send.Trade's side), and the system auto-detects verified tokens on the next run.

---

## 2. Status lifecycle

Each row in the sheet has a `status` column with one of three values:

| Status | Set by | Behavior |
|---|---|---|
| `pending` | System (default for new candidates) | Continues to surface day after day until acted on |
| `verified` | System (when token appears in Send.Trade API) | Token is dropped from active candidate list, row archived/grayed out in sheet |
| `dismissed` | Austin (manual edit in sheet) | Permanently excluded from re-surfacing, even if MC/vol thresholds are still met |

**Important**: Status transitions are:
- `(new) → pending`: candidate first detected
- `pending → verified`: token appears in Send.Trade's verified API
- `pending → dismissed`: Austin edits the cell to `dismissed`
- `dismissed → (sticky)`: never auto-revert; Austin can manually flip back to `pending` if needed

---

## 3. Data sources

### 3.1 DexScreener (candidate discovery)

- **Base tokens**: `https://api.dexscreener.com/latest/dex/search?q=base` — paginate / iterate as needed
- **Solana tokens**: same endpoint with `q=solana`
- Better: use the `/token-profiles/latest/v1` or `/dex/tokens/{address}` endpoints with proper iteration

Public API, no auth. **Rate limit: 300 requests / minute** per IP — respect this.

Reference: https://docs.dexscreener.com/api/reference

For each candidate token, DexScreener returns pairs with:
- `chainId` (e.g. `base`, `solana`)
- `baseToken.address`, `baseToken.symbol`, `baseToken.name`
- `priceUsd`, `volume.h24`, `fdv` (use fdv as MC fallback), `marketCap` (preferred when present)
- `liquidity.usd`
- `info.imageUrl` (logo)

**Filter logic**: `marketCap > 1_000_000 AND volume.h24 > 1_000_000`. Use `marketCap` if present, else `fdv`. If both missing, skip.

**Aggregation**: a token can appear on multiple DEXes (multiple pairs). Aggregate to one row per unique `(chainId, baseToken.address)`. Sum volume across pairs, take max liquidity.

### 3.2 Send.Trade verified-list (auto-verification detection)

- **Endpoint**: `https://api.send.trade/assets/verified`
- Public, no auth (confirm during build)
- Returns list of currently verified assets across all chains supported by Send.Trade

Confirmed response shape (verified live at `api.send.trade/assets/verified`, returns 200 OK, JSON array):
```json
{
  "address": "0xed664536023d8e4b1640c394777d34abaff1df8f",
  "name": "Dolphin",
  "symbol": "POD",
  "decimals": 18,
  "logoURI": "https://cdn.dexscreener.com/cms/images/...",
  "chainId": 8453,
  "isVerified": true
}
```

Chain IDs observed: `8453` for Base. Solana representation TBD — when Send.Trade adds their first Solana asset, confirm whether they use a synthetic numeric ID or a string like `"solana"`.

**Match logic**: a candidate is "verified" if `(chainId, address)` matches an entry in the API response (case-insensitive address comparison).

---

## 4. Output format

### 4.1 Google Sheet

One sheet titled "Send.Trade Verification Candidates" (or similar — Austin picks the name). Columns, in this order:

| Column | Source | Notes |
|---|---|---|
| `status` | System / Austin | `pending` / `verified` / `dismissed` |
| `first_seen_utc` | System | ISO date when first surfaced |
| `last_seen_utc` | System | ISO date of most recent appearance |
| `chain_id` | Map from DexScreener `chainId` | `8453` for Base, `1399811149` (or whatever Send.Trade uses) for Solana |
| `chain_name` | Derived | `base` / `solana` for human readability |
| `symbol` | DexScreener `baseToken.symbol` | |
| `name` | DexScreener `baseToken.name` | |
| `address` | DexScreener `baseToken.address` | lower-case on Base, base58 on Solana |
| `decimals` | Fetch from chain RPC or DexScreener if available | Required by Send.Trade verification |
| `logo_uri` | DexScreener `info.imageUrl` | Required by Send.Trade verification |
| `market_cap_usd` | DexScreener `marketCap` (fallback `fdv`) | rounded |
| `volume_24h_usd` | DexScreener `volume.h24` (summed across pairs) | rounded |
| `liquidity_usd` | DexScreener `liquidity.usd` (max across pairs) | rounded |
| `dexscreener_url` | Construct from chain+address | `https://dexscreener.com/{chain}/{address}` |
| `days_pending` | Computed | `today - first_seen_utc` in days, only relevant if status=`pending` |

**Update behavior per daily run**:
- Existing rows: update `last_seen_utc`, `market_cap_usd`, `volume_24h_usd`, `liquidity_usd`, `days_pending`. Auto-update `status` to `verified` if API says so. Never touch user-edited `status=dismissed`.
- New rows: append with `status=pending`, `first_seen_utc=today`.
- Rows where status flips to `verified`: optionally move to a separate "Archive" tab or just keep in main sheet with status filter.

### 4.2 Telegram message

After updating the sheet, send Austin a message summarizing the day's run:

```
gm — Send.Trade verification candidates for 2026-05-13

📊 5 new pending tokens today
🟡 12 tokens still pending review (older than 1 day)
✅ 3 tokens auto-verified (now on Send.Trade)
🚫 2 dismissed (skipped)

Top 3 new candidates by 24h volume:
1. POD (Base) — $2.2M MC, $220K vol
2. HANTA (Solana) — $5.1M MC, $1.2M vol
3. ...

Full list: [Google Sheets link]
```

Use Austin's voice: lowercase "gm", concise, no em dashes, real numbers. Keep emoji minimal.

---

## 5. Repository structure

```
send-trade-monitor/
├── SKILL.md                    # this file
├── README.md                   # operator-facing setup + usage notes
├── requirements.txt            # pinned deps
├── .env.example                # template for local dev
├── .gitignore                  # exclude .env, __pycache__, etc.
├── config.json                 # thresholds, API URLs, sheet ID
├── daily_scan.py               # main entrypoint — runs everything end-to-end
├── lib/
│   ├── dexscreener.py          # paginated candidate fetcher with filtering
│   ├── send_trade.py           # verified-list fetcher + matching logic
│   ├── decimals.py             # on-chain decimals fetch (Base via Alchemy/Etherscan, Solana via Helius/RPC)
│   ├── sheets.py               # gspread wrapper, upsert logic for status rows
│   └── telegram.py             # bot message sender
├── data/
│   └── snapshots/              # daily JSON snapshots for audit
├── reports/
│   └── YYYY-MM-DD.md           # optional human-readable daily report
└── .github/workflows/
    └── daily.yml               # cron at 13:00 UTC, run daily_scan.py, commit snapshot
```

---

## 6. Pipeline (`daily_scan.py`)

```
1. Load config (config.json)
2. Fetch Send.Trade verified list → set of (chain_id, address)
3. Fetch DexScreener candidates for Base — paginate until exhausted, filter MC>$1M AND vol>$1M
4. Fetch DexScreener candidates for Solana — same
5. Aggregate per (chain, address): sum volumes across pairs, max liquidity
6. For each Base address: fetch on-chain decimals (cache; only do this for new candidates)
7. Load current sheet state via gspread
8. For each unique candidate:
   - If in sheet AND status=dismissed → skip
   - If in sheet AND status=verified → leave alone unless API says otherwise (it should still be verified)
   - If in sheet AND status=pending → update last_seen, MC, vol, liquidity, days_pending; auto-mark verified if API matches
   - If NOT in sheet → append new row with status=pending
9. Audit existing pending rows that DID NOT appear in today's scan:
   - If they appear in the Send.Trade API → mark verified
   - Otherwise leave as pending (token may have temporarily dropped below thresholds; don't auto-dismiss)
10. Write a daily snapshot to data/snapshots/YYYY-MM-DD.json
11. Send Telegram summary message
12. Git commit + push snapshots (mirror hydrex-base-monitor's commit step)
```

---

## 7. Configuration (`config.json`)

```json
{
  "thresholds": {
    "min_market_cap_usd": 1000000,
    "min_volume_24h_usd": 1000000
  },
  "chains": [
    {"slug": "base", "chain_id": 8453, "dexscreener_query": "base"},
    {"slug": "solana", "chain_id": "1399811149_OR_SOLANA_NATIVE_ID", "dexscreener_query": "solana"}
  ],
  "endpoints": {
    "send_trade_verified": "https://api.send.trade/assets/verified",
    "dexscreener_search": "https://api.dexscreener.com/latest/dex/search",
    "dexscreener_pairs": "https://api.dexscreener.com/latest/dex/tokens"
  },
  "google_sheet": {
    "sheet_id": "TO_BE_SET_BY_AUSTIN",
    "tab_name": "Candidates"
  },
  "telegram": {
    "chat_id": "TO_BE_SET_BY_AUSTIN"
  }
}
```

Confirm the Solana chain_id format Send.Trade expects (Solana doesn't have an EVM chain_id; Send.Trade likely uses `1399811149` per their own scheme, or maybe `"solana"` as a string — verify against the API response).

---

## 8. Secrets (GitHub Actions)

Set these in repo Settings → Secrets:

| Secret | What |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather. Austin needs to create a bot if he doesn't have one yet. |
| `TELEGRAM_CHAT_ID` | Austin's user ID. He can get it from @userinfobot. |
| `GOOGLE_SHEETS_CREDENTIALS` | JSON of a Google Cloud service account with Sheets API enabled. The service account email needs to be granted edit access on the target sheet. |
| `ALCHEMY_API_KEY` (optional) | For Base decimals lookup. Or use Etherscan as fallback. |
| `HELIUS_API_KEY` (optional) | For Solana decimals lookup. |

---

## 9. Conventions to mirror from `hydrex-base-monitor`

- **Python 3.11**, use `requests` for HTTP
- **GitHub Action with `contents: write` permission** so the daily snapshot can be committed back
- **CSV (or JSON) committed back to the repo as audit trail** — `data/snapshots/YYYY-MM-DD.json` is the equivalent of `candidates.csv` in the base monitor
- Cron in `.github/workflows/daily.yml`:
  ```yaml
  on:
    schedule:
      - cron: "12 13 * * *"   # 13:12 UTC daily (off-minute to avoid cron stampede)
    workflow_dispatch: {}
  ```
- The commit step at the end of the workflow uses `git add -f` to avoid `.gitignore` clobbering:
  ```yaml
  - name: Commit snapshot
    run: |
      git config user.name  "github-actions[bot]"
      git config user.email "github-actions[bot]@users.noreply.github.com"
      git add -f data/snapshots/
      if git diff --staged --quiet; then echo "No changes"; else
        git commit -m "Daily snapshot $(date -u +%Y-%m-%d)"
        git push
      fi
  ```

---

## 10. Austin's Telegram message style

Refer to the existing patterns in the workspace memory (`feedback_tone_no_emdashes.md`, `user_voice_learnings.md`). Brief recap:
- Lowercase "gm" / "gmgm" for crypto-native greeting
- No em dashes (—). Use commas or periods.
- Capitalize first word after a period
- Proper contractions (let's, don't, it's, won't)
- Real numbers, not vague language
- Short and punchy, no walls of text

This monitor's daily message should feel like Austin wrote it to himself.

---

## 11. Things to validate during build

When you sit down to build this, hit these checks before considering done:

1. **Confirm Send.Trade API URL and response shape** — visit `https://api.send.trade/assets/verified` and verify
2. **Confirm Solana chain_id format** in the Send.Trade response — adjust config.json accordingly
3. **Test DexScreener pagination** — make sure you pull ALL qualifying Base + Solana tokens, not just the first page
4. **Test decimals fetch** — make sure you can resolve decimals for both Base (EVM) and Solana (SPL) tokens
5. **Test gspread upsert logic** — first run should populate, second run should update without duplicating
6. **Test status-dismissed sticky behavior** — manually set a row to `dismissed`, rerun, verify it doesn't get reset
7. **Test status auto-verify** — pick a token already on Send.Trade, manually add it as `pending`, rerun, verify it flips to `verified`
8. **Dry-run mode** — add a `--dry-run` flag that fetches everything but doesn't write to sheet or send Telegram, useful for iteration

---

## 12. Out of scope (don't build these yet)

- Auto-verification (writing back to Send.Trade's API) — Austin handles verification manually for now
- Telegram `/dismiss` command — `dismissed` is set via the sheet only
- Multi-user/team support — Austin is the only operator
- Historical re-runs — daily-only, no backfill of past dates needed
- Real-time monitoring — daily cron is enough

---

## 13. First-run expectations

The first scan will likely surface 30-100+ candidates because nothing has been triaged yet. That's expected. Austin will work through them, dismissing what he doesn't want and verifying what he does. Subsequent runs will be much shorter (only new graduates from below the threshold).

Build a clean first-run experience: don't blast Austin's Telegram with 100 individual messages. One summary message with top-N and a sheet link is enough.
