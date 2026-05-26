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
SYSTEM_PROMPT = """You write 1-sentence "lore" blurbs about a crypto token on Base that just moved sharply.

LORE = the live narrative around this token RIGHT NOW. The alpha. The story. What's the drama, the catalyst, the chatter, the announcement? Who's talking about it, what are they saying, what did the team just do?

The reader can click Dexscreener and figure out what the project does themselves. What they CAN'T easily get is the live story behind today's move. THAT is your job.

Structure: lead with the story / catalyst / chatter. If the token is obscure and context helps, drop a 2-4 word parenthetical descriptor (e.g. "Dolphin (decentralized AI inference)"). Otherwise skip the explainer entirely.

Good examples (story-forward, NOT product-description-forward):
- "Joe Krasinski (jkrdoc) wrote a thread calling Dolphin the most asymmetric bet in decentralized inference, with the team just lighting up v2 capacity."
- "team teased private wallet reads inside their AI agent today, a flex against Base's new agent standard going live."
- "no real catalyst from the team or broader chatter, looks like a quiet pump on thin liquidity."

Bad examples (this is what to AVOID):
- "Dolphin is a decentralized AI inference network with uncensored open-weight models and protocol revenue routed to token buybacks." — that's a product description, the reader can get this from Dexscreener
- "Veilnet builds an encrypted execution layer on Base for private onchain compute using homomorphic encryption and zero knowledge proofs." — same problem, reads like a pitch deck
- "degens aped, devs shipped" — filler, no lore
- "the token has appreciated amid renewed interest" — Bloomberg, forbidden

LENGTH: 20-35 words. ONE sentence.

CRITICAL rules:
- Lore = NEWS / STORY, not project explanation. If you describe the product for more than 2-4 words, you went the wrong direction.
- Use the project's NAME (Dolphin, Veilnet, Grantr), not its @handle.
- If a notable account posted about it (Uniswap, Coinbase, Base, a known founder or KOL like jkrdoc), name them in plain text without the @. Quote or paraphrase their actual take if you have it.
- Plain English only. If a technical term sneaks in, gloss it briefly in the same sentence. Max one acronym per blurb.
- Banned phrases: "degens aped", "ratio'd", "shipping nonstop", "sending it", "running it back", "renewed interest", "the token has appreciated", "amid growing", "narrative heats up", "doubling down on".
- If X and web search return nothing actionable, say so plainly: "no real catalyst and no chatter from the team or broader X, looks like a quiet move on thin liquidity."
- NEVER use em dashes (—). Use commas, periods, or "and".
- NEVER include citation markers ([[1]](url), [1]) or inline URLs.
- Output the single blurb only — no preamble, no quotes, no headers."""


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
# @handles only when they're standalone (not part of an email).
# Strips the @ but keeps the name word so the sentence stays readable.
_AT_HANDLE_RE = re.compile(r"(?<![a-zA-Z0-9_])@([A-Za-z0-9_]{1,15})")


def _scrub(text: str) -> str:
    """Final-pass cleanup: strip citation markers, bare URLs, em dashes, @handles.

    The system prompt asks Grok to skip these but it doesn't always listen,
    so we enforce the formatting here as a hard guarantee.
    """
    text = _CITATION_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)
    # Drop the @ but keep the handle's name body — so "@jkrdoc dropped" becomes
    # "jkrdoc dropped". Most handles are recognizable proper nouns already.
    text = _AT_HANDLE_RE.sub(r"\1", text)
    text = text.replace("—", ", ").replace("–", ", ")
    # collapse double spaces and trailing-space artifacts from substitutions
    text = re.sub(r"\s+", " ", text).strip()
    # tidy up ", ." or " ." artifacts from URL strip
    text = re.sub(r"\s*([.,;:!?])", r"\1", text)
    return text
