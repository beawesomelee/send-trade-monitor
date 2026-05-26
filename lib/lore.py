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

The reader can already see the % move and chart. What they want is the LORE: the specific catalyst, news, product drop, narrative, team activity, partnership, or chatter that actually caused this move. Be concrete and substantive.

Voice:
- one sentence, casual lowercase, plain language. NOT corporate, NOT a press release.
- DO NOT lean on crypto-twitter filler like "degens aped", "ratio'd", "no cap", "shipping nonstop", "sending it", "running it back". These phrases tell the reader nothing about WHY. Avoid them in the output. Sparingly use one ("devs been shipping" or "based" or "lowkey") only if it adds flavor AND doesn't displace real info.
- Bloomberg / analyst phrases are also forbidden: no "renewed interest", "the token has appreciated", "amid growing", "narrative heats up", "doubling down on", "investors are showing".
- lead with the catalyst, not the move. Bad: "$tkn pumped 80% as devs shipped a new feature." Good: "team just released v2 of the agent framework with native base integration."
- if you genuinely cannot find anything specific from X or web search, say so plainly ("nothing announced from @handle or in the timeline, looks like a quiet move on thin liquidity") instead of inventing reasons.
- NEVER use em dashes (—). Use commas, periods, or "and" instead. Hard rule.
- NEVER include citation markers, footnotes, or source links (no [[1]](url), no inline URLs). Just clean prose.
- output the blurb only — no preamble, no quotes, no headers."""


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
        "Search X for recent posts from this handle and any crypto-twitter chatter about "
        f"${symbol} on Base in the last 24-48h. Then give me the lore — why is this moving? "
        "1-2 sentences, casual."
    )

    # Tools API: x_search restricted to project handle when known, plus web_search
    # as a fallback for context outside the handle. Keep tool count minimal so
    # search cost stays bounded ($25/1k tool calls).
    x_search_tool = {"type": "x_search"}
    if x_handle:
        x_search_tool["allowed_x_handles"] = [x_handle]

    payload = {
        "model": MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [x_search_tool, {"type": "web_search"}],
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
