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
SYSTEM_PROMPT = """You write a "lore log" line about a crypto token on Base that just moved sharply.

LORE LOG = how you'd describe it to a friend in a quick text. Conversational. Plain. Just the story of what happened, not a news headline.

The reader can click Dexscreener to see what the project DOES. Your job is the live narrative — today's catalyst, chatter, the post, the drop, the drama.

LENGTH = however long the story actually needs. If it's just "@jkrdoc dropped a bull post on pod today" — that's 8 words and that's perfect, don't pad it. If you actually need to explain the context for the catalyst to make sense (like a technical integration), use up to 35 words. Never go over 35.

Good examples (these are the target):
- "@jkrdoc dropped a bull post on pod today"
- "base just launched mcp letting ai agents control wallets and swaps on base, and Veilnet posted that their privacy-focused mcp layer is the missing piece for private agent execution"
- "team teased wallet context for Veil AI today, a flex against base's brand new agent standard"
- "Joe McCann shouted out the project on his timeline as the most asymmetric bet in decentralized inference"
- "no real chatter or team posts, looks like a quiet move on thin liquidity"

Bad examples (what NOT to write):
- "Dolphin is a decentralized AI inference network with uncensored open-weight models and protocol revenue routed to token buybacks" — Wikipedia entry, not lore
- "jkrdoc just posted the dolphin emoji with $POD and $VVV, amplifying chatter that it is the default model behind Venice and captures revenue as that platform grows" — too packed, sounds like a news brief. The simple version "jkrdoc just posted the dolphin emoji about pod" is better
- "the team has been doubling down on AI agent narratives amid renewed interest" — Bloomberg, forbidden
- "degens aped, devs shipped" — pure filler

CRITICAL rules:
- Match the length to the story complexity. Don't pad simple events.
- Use the project's plain NAME (Dolphin, Veilnet, Grantr) — never the project's @handle or $TICKER.
- For external accounts (KOLs, big founders, other protocols), the @handle is FINE and reads naturally ("@jkrdoc", "@elonmusk"). Use it.
- If you name a person directly (Joe McCann, jkrdoc, Brian Armstrong), no @ needed unless that's the form you've seen them go by online.
- Plain English. Max one acronym per blurb. If it sneaks in, gloss it briefly.
- Banned phrases: "degens aped", "ratio'd", "shipping nonstop", "sending it", "running it back", "renewed interest", "the token has appreciated", "amid growing", "narrative heats up", "doubling down on", "amplifying chatter".
- If X and web search return nothing actionable, say plainly: "no real chatter or team posts, looks like a quiet move on thin liquidity"
- NEVER use em dashes (—). Use commas, periods, or "and".
- NEVER include citation markers ([[1]](url), [1]) or inline URLs.
- Output the single line only — no preamble, no quotes, no headers."""


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
        return _extract_text(data, project_handle=x_handle)
    except Exception as e:
        print(f"    Grok lore err: {e}")
        return ""


def _extract_text(data: dict, project_handle: str = "") -> str:
    """Pull the model's text output out of the Responses API JSON.

    The /v1/responses payload can put the answer in a few shapes depending on
    tool use. Handles:
      - top-level `output_text` (short-circuit field)
      - `output[].content[].text` for message-type items

    project_handle: the X handle of the project itself. Stripped from output
    so blurbs read "Veilnet posted" instead of "@Veilnet_ posted". External
    @handles (KOLs, big founders) are left intact since they read naturally.
    """
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        raw = data["output_text"].strip()
    else:
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
    return _scrub(raw, project_handle=project_handle)


_CITATION_RE = re.compile(r"\[\[?\d+\]?\]\([^)]*\)")  # [[1]](url) or [1](url)
_BARE_URL_RE = re.compile(r"https?://\S+")


def _scrub(text: str, project_handle: str = "") -> str:
    """Final-pass cleanup: strip citation markers, bare URLs, em dashes, and
    the PROJECT'S own @handle (external @s preserved — they read naturally
    like "@jkrdoc dropped a post").

    The system prompt asks Grok to follow these rules but it doesn't always
    listen, so we enforce them here as a hard guarantee.
    """
    text = _CITATION_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)

    # Only strip the project's own @handle, leaving external handles intact.
    ph = (project_handle or "").lstrip("@").strip()
    if ph:
        # case-insensitive: "@Veilnet_" or "@veilnet_" both get stripped to "Veilnet_"
        text = re.sub(
            rf"(?<![a-zA-Z0-9_])@({re.escape(ph)})\b",
            r"\1",
            text,
            flags=re.IGNORECASE,
        )

    text = text.replace("—", ", ").replace("–", ", ")
    # collapse double spaces and trailing-space artifacts from substitutions
    text = re.sub(r"\s+", " ", text).strip()
    # tidy up ", ." or " ." artifacts
    text = re.sub(r"\s*([.,;:!?])", r"\1", text)
    return text
