"""
Token lore generation via xAI Grok with X search tool.

Given a mover dict (symbol, x_handle, website, etc.), calls Grok via the
Agent Tools / Responses API with the x_search + web_search tools enabled
(restricted to the project's X handle when known), and returns a 1-2
sentence Gen Z blurb explaining the move.

Returns "" if XAI_API_KEY is not set or the API call fails — callers should
treat lore as best-effort enrichment, never block on it.
"""

import os
import re

import requests

XAI_ENDPOINT = "https://api.x.ai/v1/responses"
MODEL = "grok-4-fast-reasoning"  # cheap tier; tools API is supported

# Voice rules baked into every lore request. Stays consistent with the
# user's feedback_lore_gen_z.md memory file.
SYSTEM_PROMPT = """You write 1-sentence "lore" blurbs explaining the actual reason a crypto token on Base just pumped or dumped.

The reader is scanning quickly. They can already see the % move and chart. What they need is the LORE: the specific catalyst that caused this move, in plain English.

CRITICAL — clarity over cleverness:
- A reader who is NOT deep in the project's world should understand the blurb on first read.
- If you mention a technical term or acronym (MCP, MEV, ZK, RWA, restaking, intent solver, etc.), give a 3-5 word plain-English gloss in the same sentence. Don't make the reader Google it.
  - Bad: "team tied veilnet MCP directly to base's MCP launch as the privacy layer"
  - Good: "Base just launched MCP (a new way for AI agents to read wallets and trade onchain), and Veil is positioning their version as the privacy layer for it"
- If a big crypto-twitter account (Uniswap, Coinbase, Base, a known KOL, a major founder) posted about the token, NAME THEM. That IS the lore. e.g. "Uniswap shared the project + @jkrdoc called it the most asymmetric bet in decentralized inference."
- Lead with the catalyst, not the move. Don't write "$tkn pumped because…" — write "team shipped X" or "Uniswap posted Y" directly.

Voice:
- one sentence, casual lowercase, plain language
- DO NOT lean on filler like "degens aped", "ratio'd", "shipping nonstop", "sending it", "running it back". They tell the reader nothing.
- Bloomberg / analyst phrases also forbidden: "renewed interest", "the token has appreciated", "amid growing", "narrative heats up", "doubling down on".
- If you genuinely cannot find anything in X or web search (including from broader accounts, not just the project handle), say so plainly: "nothing in the timeline from @handle or broader X chatter, looks like a quiet move on thin liquidity." Don't invent reasons.
- NEVER use em dashes (—). Use commas, periods, or "and" instead.
- NEVER include citation markers, footnotes, or source links (no [[1]](url), no inline URLs).
- Output the blurb only, no preamble, no quotes, no headers."""


def fetch_lore(mover: dict, timeout: int = 60) -> str:
    """Generate a Gen Z lore blurb for a mover via Grok + X/web search tools."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return ""

    symbol = mover.get("symbol") or "?"
    x_handle = (mover.get("x_handle") or "").lstrip("@")
    website = mover.get("website_url") or ""
    direction = mover.get("direction", "moved")
    change = mover.get("price_change_pct", 0)
    window = mover.get("price_change_window", "h1")
    win_label = window[1:] + "h"
    mc = mover.get("market_cap_usd", 0)
    ds_url = mover.get("dexscreener_url", "")

    user_prompt = (
        f"Token: ${symbol} on Base. "
        f"Just {direction}ed {change:+.0f}% in {win_label}. "
        f"MC ${mc/1e6:.2f}M. "
        f"Dexscreener: {ds_url}. "
    )
    if x_handle:
        user_prompt += f"Their X handle is @{x_handle}. "
    if website:
        user_prompt += f"Website: {website}. "
    user_prompt += (
        f"Search ALL of X broadly for ${symbol} chatter in the last 24-48h — "
        "not just the project's own handle. Specifically check if any large crypto-twitter "
        "accounts, founders, KOLs, or protocols (e.g. Uniswap, Coinbase, Base, well-known traders) "
        "posted about it. Also check the project's own handle"
    )
    if x_handle:
        user_prompt += f" (@{x_handle})"
    user_prompt += (
        ". Then give me the lore — the single most important reason this is moving. "
        "1 sentence, casual, plain English."
    )

    # Tools API: x_search is UNRESTRICTED — we want it to find chatter from
    # anyone (Uniswap, big crypto-twitter accounts, etc.), not just the
    # project's own handle. The handle is named in the prompt for guidance
    # but not locked via allowed_x_handles, which would hide everyone else.
    payload = {
        "model": MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [{"type": "x_search"}, {"type": "web_search"}],
        "temperature": 0.6,
        "max_output_tokens": 200,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(XAI_ENDPOINT, json=payload, headers=headers, timeout=timeout)
        if r.status_code != 200:
            print(f"    Grok lore HTTP {r.status_code}: {r.text[:300]}")
            return ""
        data = r.json()
        return _extract_text(data)
    except Exception as e:
        print(f"    Grok lore err: {e}")
        return ""


def _extract_text(data: dict) -> str:
    """Pull the model's text output out of the Responses API JSON.

    The /v1/responses payload can put the answer in a few shapes depending on
    tool use. Handles:
      - top-level `output_text` (short-circuit field)
      - `output[].content[].text` for message-type items
    """
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()

    output = data.get("output") or []
    chunks = []
    for item in output:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            text = c.get("text")
            if isinstance(text, str):
                chunks.append(text)
    raw = "\n".join(chunks).strip()
    return _scrub(raw)


_CITATION_RE = re.compile(r"\[\[?\d+\]?\]\([^)]*\)")  # [[1]](url) or [1](url)
_BARE_URL_RE = re.compile(r"https?://\S+")


def _scrub(text: str) -> str:
    """Final-pass cleanup: strip citation markers, bare URLs, em dashes.

    The system prompt asks Grok to skip these but it doesn't always listen,
    so we enforce the formatting here as a hard guarantee.
    """
    text = _CITATION_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)
    text = text.replace("—", ", ").replace("–", ", ")
    # collapse double spaces and trailing-space artifacts from substitutions
    text = re.sub(r"\s+", " ", text).strip()
    # tidy up ", ." or " ." artifacts from URL strip
    text = re.sub(r"\s*([.,;:!?])", r"\1", text)
    return text
