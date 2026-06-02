"""Google Sheets upsert logic for Send.Trade verification candidates.

Supports two auth modes:
  - OAuth user credentials (default): set GOOGLE_OAUTH_TOKEN env or place
    token.json next to script. This is what's used in production.
  - Service account: set GOOGLE_SHEETS_CREDENTIALS to service-account JSON
    (path or contents). Not used; available as fallback.

Status lifecycle of each sheet row:
  - "pending"   — system default; resurfaces every run until acted on
  - "verified"  — auto-removed when Send.Trade verifies the token
  - "dismissed" — manual edit in sheet; permanently excluded (sticky via
                  data/dismissed.json)
  - any other manual edit ("Verified", "watching", etc.) — preserved, not
                  overwritten by the scanner

IMPORTANT: address comparisons go through lib.send_trade._norm_addr because
Solana base58 addresses are CASE-SENSITIVE. Lowercasing them silently breaks
matching against the Send.Trade verified list.
"""

import json
import os
import datetime as dt
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials as SACredentials
from google.oauth2.credentials import Credentials as UserCredentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "status",
    "first_seen_utc",
    "last_seen_utc",
    "chain_id",
    "chain_name",
    "symbol",
    "name",
    "address",
    "decimals",
    "logo_uri",
    "market_cap_usd",
    "volume_24h_usd",
    "liquidity_usd",
    "dexscreener_url",
    "days_pending",
]


def get_client() -> gspread.Client:
    """Authorize gspread. Tries OAuth user creds first, falls back to service account."""
    creds = _load_oauth_user_creds() or _load_service_account_creds()
    if creds is None:
        raise RuntimeError(
            "No Google credentials found. Either:\n"
            "  - set GOOGLE_OAUTH_TOKEN env var to OAuth token JSON, or place token.json next to daily_scan.py\n"
            "  - set GOOGLE_SHEETS_CREDENTIALS env var to service-account JSON (path or contents)"
        )
    return gspread.authorize(creds)


def _load_oauth_user_creds():
    raw = os.environ.get("GOOGLE_OAUTH_TOKEN", "")
    if raw:
        try:
            return UserCredentials.from_authorized_user_info(json.loads(raw), SCOPES)
        except Exception as e:
            print(f"WARNING: GOOGLE_OAUTH_TOKEN parse failed: {e}")

    token_path = Path(__file__).resolve().parent.parent / "token.json"
    if token_path.exists():
        try:
            return UserCredentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            print(f"WARNING: {token_path} parse failed: {e}")
    return None


def _load_service_account_creds():
    raw = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")
    if not raw:
        return None
    try:
        if os.path.isfile(raw):
            return SACredentials.from_service_account_file(raw, scopes=SCOPES)
        return SACredentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    except Exception as e:
        print(f"WARNING: service account creds parse failed: {e}")
        return None


def load_sheet(config: dict) -> tuple[gspread.Worksheet, list[dict]]:
    """Open the sheet and return (worksheet, existing_rows_as_dicts)."""
    client = get_client()
    sheet_id = config["google_sheet"]["sheet_id"]
    tab_name = config["google_sheet"]["tab_name"]

    spreadsheet = client.open_by_key(sheet_id)

    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=500, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="RAW")
        return ws, []

    all_values = ws.get_all_values()
    if not all_values:
        ws.append_row(HEADERS, value_input_option="RAW")
        return ws, []

    header_row = all_values[0]
    rows = []
    for row_values in all_values[1:]:
        row_dict = {}
        for i, h in enumerate(header_row):
            row_dict[h] = row_values[i] if i < len(row_values) else ""
        rows.append(row_dict)

    return ws, rows


def upsert(ws: gspread.Worksheet, existing: list[dict], candidates: list[dict],
           verified_set: set[tuple], today: str,
           dismissed_set: set[tuple] | None = None) -> dict:
    """Upsert candidates into the sheet. Returns stats dict.

    dismissed_set: set of (chain_slug, normalized_address) that Austin has dismissed.
    These are added to from any existing rows with status=dismissed (those rows
    get deleted and recorded), and used to skip future re-discovery.

    Address case rules:
    - Base (chain_id 8453): EVM hex, case-insensitive, lowercased for matching.
    - Solana (chain_id 501474): base58, CASE-SENSITIVE, preserved as-is.
    """
    from lib.send_trade import is_verified, _norm_addr

    if dismissed_set is None:
        dismissed_set = set()

    index = {}
    for i, row in enumerate(existing):
        cid = row.get("chain_id", "")
        addr = _norm_addr(cid, row.get("address", ""))
        key = (str(cid), addr)
        index[key] = (i, row)

    stats = {"new_pending": 0, "updated": 0, "auto_verified": 0, "dismissed": 0, "still_pending": 0}
    updates = []
    new_rows = []
    rows_to_delete = []

    for c in candidates:
        addr = _norm_addr(c["chain_id"], c["address"])
        key = (str(c["chain_id"]), addr)
        chain_addr = (c["chain_slug"], addr)

        # never re-add anything previously dismissed
        if chain_addr in dismissed_set:
            stats["dismissed"] += 1
            continue

        is_v = is_verified(c, verified_set)

        if key in index:
            idx, row = index[key]
            status = row.get("status", "pending").strip().lower()

            if status == "dismissed":
                # record for future skip + delete from sheet
                dismissed_set.add(chain_addr)
                rows_to_delete.append(idx + 2)
                stats["dismissed"] += 1
                continue

            if is_v:
                rows_to_delete.append(idx + 2)
                stats["auto_verified"] += 1
                continue

            # Preserve manual status edits — if user set anything other than
            # "pending" (e.g. typed "verified" before Send.Trade caught up),
            # don't overwrite. We only refresh rows that are still in pending state.
            if status != "pending":
                continue

            stats["still_pending"] += 1
            first_seen = row.get("first_seen_utc", today)
            days = _days_between(first_seen, today)
            updates.append((idx + 2, _build_row(c, "pending", first_seen, today, days)))
        else:
            if is_v:
                stats["auto_verified"] += 1
                continue
            stats["new_pending"] += 1
            new_rows.append(_build_row(c, "pending", today, today, 0))

    # audit existing rows not in today's scan.
    # Any row whose address is in Send.Trade's verified list gets deleted,
    # regardless of stored status (handles manual "Verified" edits + verified
    # tokens that dropped below thresholds and stopped being rediscovered).
    candidate_keys = {
        (str(c["chain_id"]), _norm_addr(c["chain_id"], c["address"]))
        for c in candidates
    }
    for key, (idx, row) in index.items():
        if key in candidate_keys:
            continue
        status = row.get("status", "pending").strip().lower()
        addr_norm = key[1]  # already normalized when index was built
        chain_name = (row.get("chain_name") or "").lower().strip()

        if status == "dismissed":
            # record + delete dismissed rows we didn't touch above
            if chain_name:
                dismissed_set.add((chain_name, addr_norm))
            rows_to_delete.append(idx + 2)
            stats["dismissed"] += 1
            continue

        # Verified-by-Send.Trade check applies to ANY non-dismissed row.
        chain_id_val = key[0]
        try:
            chain_id_parsed = int(chain_id_val)
        except ValueError:
            chain_id_parsed = chain_id_val
        if (chain_id_parsed, addr_norm) in verified_set:
            rows_to_delete.append(idx + 2)
            stats["auto_verified"] += 1
            continue

        # Leave rows with unrecognized status alone (don't overwrite manual edits)

    if updates:
        batch = [{
            "range": f"A{row_num}:{_col_letter(len(HEADERS))}{row_num}",
            "values": [vals],
        } for row_num, vals in updates]
        ws.batch_update(batch, value_input_option="RAW")

    if new_rows:
        ws.append_rows(new_rows, value_input_option="RAW")

    for row_num in sorted(set(rows_to_delete), reverse=True):
        ws.delete_rows(row_num)

    stats["updated"] = len(updates)
    return stats


def _build_row(candidate: dict, status: str, first_seen: str, last_seen: str, days: int) -> list:
    return [
        status,
        first_seen,
        last_seen,
        str(candidate.get("chain_id", "")),
        candidate.get("chain_slug", ""),
        candidate.get("symbol", ""),
        candidate.get("name", ""),
        candidate.get("address", ""),
        str(candidate.get("decimals", "")),
        candidate.get("logo_uri", ""),
        candidate.get("market_cap_usd", 0),
        candidate.get("volume_24h_usd", 0),
        candidate.get("liquidity_usd", 0),
        candidate.get("dexscreener_url", ""),
        days,
    ]


def _days_between(d1: str, d2: str) -> int:
    try:
        a = dt.date.fromisoformat(d1)
        b = dt.date.fromisoformat(d2)
        return (b - a).days
    except (ValueError, TypeError):
        return 0


def _col_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result
