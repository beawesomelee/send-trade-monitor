"""
Token lore generation via xAI Grok with X search tool.

Given a mover dict (symbol, x_handle, website, etc.), calls Grok via the
Agent Tools / Responses API with the x_search + web_search tools enabled,
and returns a short send.trade-style trader-voice blurb explaining the move.

Returns "" if XAI_API_KEY is not set or the API call fails — callers should
treat lore as best-effort enrichment, never block on it.
"""

import os
import json
import re

import requests

XAI_ENDPOINT = "https://api.x.ai/v1/responses"
MODEL = "grok-4-fast-reasoning"  # cheap tier; tools API is supported
LORE_PACKET_SCHEMA_VERSION = "lore_packet_v1"


LORE_PACKET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "lore": {
            "type": "string",
            "description": "The final short send.trade-style lore blurb.",
        },
        "references": {
            "type": "array",
            "description": "Source posts or links that support the lore. Empty if no reliable source was found.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["x_post", "web_page", "unknown"],
                    },
                    "url": {
                        "type": "string",
                        "description": "Canonical URL for the source when available.",
                    },
                    "author_handle": {
                        "type": "string",
                        "description": "X handle for posts, including @ when known. Empty when unknown.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Short relevant quote or paraphrase from the source.",
                    },
                    "relevance": {
                        "type": "string",
                        "description": "Why this source matters for the token move.",
                    },
                },
                "required": ["type", "url", "author_handle", "text", "relevance"],
            },
        },
        "watcher_clues": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "accounts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "X accounts worth considering as watcher candidates.",
                },
                "phrases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multi-word phrases worth considering as watcher candidates.",
                },
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Single-token keywords, tickers, memes, or project names.",
                },
                "catalysts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Short catalyst tags like kol_post, listing, team_update, meme_reactivation.",
                },
            },
            "required": ["accounts", "phrases", "keywords", "catalysts"],
        },
    },
    "required": ["lore", "references", "watcher_clues"],
}

# Voice rules baked into every lore request. Mirrors the send.trade post-tone
# skill — crypto-native gen z trader voice, all lowercase, real numbers and
# observations, no hype filler.
SYSTEM_PROMPT = """ROLE: you're posting in a send.trade-style trader chat. Crypto-native gen z trader voice. The audience already knows crypto, so skip definitions and skip explaining what the project is. They want the read on why this is moving.

## formatting rules
- all lowercase, ALWAYS — including the start of sentences and proper nouns (so "veilnet" not "Veilnet", "coinbase" not "Coinbase")
- no em dashes (or --). use commas, spaces, or just start a new sentence
- no exclamation points unless something is genuinely big, and even then one max
- keep it short. 1-3 sentences, target 8-30 words

## voice rules
- no hype language: skip "exciting", "thrilled", "game-changing", "dive into", "it's worth noting"
- no Bloomberg / analyst phrases: skip "renewed interest", "the token has appreciated", "amid growing", "narrative heats up", "doubling down on", "amplifying chatter"
- no empty crypto filler: skip "degens aped", "shipping nonstop", "sending it", "running it back"
- no corporate hedging, say the thing directly
- don't over-explain, audience knows crypto, skip the definitions
- ground every take in a number or a real observation (a vol figure, a price level, who specifically posted, the actual thing they said)
- end posts open when possible: a question, a one-word thought, or just let it hang

## vocabulary (use when natural, don't force)
vol (not volume), mc (not market cap), narra (not narrative), floor, bottom bid, pvp, bleeds/bleeding, imo, exposure, price action, cooked, copium, bid, ask, runner

## sentence structure
mix short punchy lines with one longer observational one. parentheticals fine for asides: (good to have both tho). let the reader connect dots, don't spell it out.

## naming
- project itself: use the plain lowercase name (dolphin, veilnet, grantr). never the project's @handle. never the $TICKER.
- external accounts (KOLs, founders, big protocols): @handles are fine and read natural (@jkrdoc, @elonmusk, @Uniswap)
- if you name someone directly by name (jkrdoc, ty), no @ needed

## what "lore" means here
the live story behind today's move. who posted what, what the team shipped, what the chatter says. NOT a project description (chart link is one click away). NOT a news headline. just the read.

## CRITICAL: synthesize, don't describe literally
if a KOL posted a 🐬 emoji + tagged the token, the takeaway is "bull post on pod". the emoji is data, "bull post" is the answer. same for cryptic posts, quote-tweets, etc. translate to the actual takeaway.

## good examples (the target)
- "@jkrdoc dropped a bull post on pod today"
- "only ai / animal token that pulled 6m~ vol on a bleeding tape today. bottom bidded. tradition?"
- "base just shipped mcp for ai agents, veilnet teased their privacy layer as the missing piece. early but a clean narra"
- "keeta personal launch pushed to tuesday with no update. people getting antsy"
- "no real chatter, team's quiet. quiet pump on thin liq imo"

## bad examples (what NOT to write)
- "Dolphin is a decentralized AI inference network with uncensored open-weight models" — wikipedia entry, not lore
- "@jkrdoc dropped the dolphin emoji with Dolphin today" — describes literal action not the takeaway
- "the team is doubling down on AI agent narratives amid renewed interest" — Bloomberg, forbidden
- "degens aped, devs shipped" — filler

## hard rules
- NEVER use em dashes
- NEVER include citation markers ([[1]](url), [1]) or inline URLs
- NEVER use uppercase for the project name in your output
- if X and web search return nothing real, say it plainly: "no real chatter, team's quiet. looks like a quiet move on thin liq"
- output the line only, no preamble, no quotes, no headers"""


def fetch_lore(mover: dict, timeout: int = 60) -> str:
    """Generate a send.trade-style trader-voice blurb for a mover."""
    return fetch_lore_packet(mover, timeout=timeout).get("lore", "")


def fetch_lore_packet(mover: dict, timeout: int = 60) -> dict:
    """Generate lore plus source references and watcher clues for a mover."""
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        return _empty_lore_packet()

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
        f"A friend in the trader chat just asked: 'why is ${symbol} {direction}ing today?' "
        f"Search ALL of X broadly for ${symbol} chatter in the last 24-48h (NOT just the "
        "project's own handle — also check big crypto-twitter accounts, founders, KOLs, "
        "and protocols like Uniswap, Coinbase, Base, Aero, well-known traders). Also check "
        "the project's own handle"
    )
    if x_handle:
        user_prompt += f" (@{x_handle})"
    user_prompt += (
        ". Now post the read in send.trade voice — all lowercase, ground it in a real "
        "observation, end open when possible. Synthesize don't describe (if @someone "
        "posted a bull emoji or cryptic hype, it's a bull post, not 'they posted X emoji'). "
        "Also return watcher clues from the sources you used: X accounts, exact phrases, "
        "keywords, catalyst tags, and source references. For X references, include the "
        "post URL, author handle, and a short relevant text snippet when available. "
        "Do not invent source URLs, handles, or quotes. If you cannot verify a source, "
        "leave references empty and say so plainly in the lore."
    )

    payload = {
        "model": MODEL,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "tools": [{"type": "x_search"}, {"type": "web_search"}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "send_trade_lore_packet",
                "schema": LORE_PACKET_SCHEMA,
                "strict": True,
            }
        },
        "temperature": 0.6,
        "max_output_tokens": 700,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(XAI_ENDPOINT, json=payload, headers=headers, timeout=timeout)
        if r.status_code != 200:
            print(f"    Grok lore HTTP {r.status_code}: {r.text[:300]}")
            return _empty_lore_packet()
        data = r.json()
        return _parse_lore_packet(data, project_handle=x_handle)
    except Exception as e:
        print(f"    Grok lore err: {e}")
        return _empty_lore_packet()


def _empty_lore_packet() -> dict:
    return {
        "schema_version": LORE_PACKET_SCHEMA_VERSION,
        "model": MODEL,
        "lore": "",
        "references": [],
        "watcher_clues": {
            "accounts": [],
            "phrases": [],
            "keywords": [],
            "catalysts": [],
        },
    }


def _parse_lore_packet(data: dict, project_handle: str = "") -> dict:
    raw = _extract_raw_text(data)
    if not raw:
        return _empty_lore_packet()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        packet = _empty_lore_packet()
        packet["lore"] = _scrub(raw, project_handle=project_handle)
        return packet

    packet = _empty_lore_packet()
    packet["lore"] = _scrub(str(parsed.get("lore") or ""), project_handle=project_handle)
    packet["references"] = _clean_references(parsed.get("references") or [])
    packet["watcher_clues"] = _clean_watcher_clues(parsed.get("watcher_clues") or {})
    return packet


def _extract_text(data: dict, project_handle: str = "") -> str:
    """Pull the model's text output out of the Responses API JSON.

    project_handle: the X handle of the project itself. Stripped from output
    so blurbs read "veilnet posted" instead of "@Veilnet_ posted". External
    @handles (KOLs, big founders) are left intact since they read naturally.
    """
    raw = _extract_raw_text(data)
    return _scrub(raw, project_handle=project_handle)


def _extract_raw_text(data: dict) -> str:
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
    return "\n".join(chunks).strip()


def _clean_references(refs) -> list[dict]:
    if not isinstance(refs, list):
        return []

    out = []
    for ref in refs[:5]:
        if not isinstance(ref, dict):
            continue
        out.append({
            "type": _string(ref.get("type")) or "unknown",
            "url": _string(ref.get("url")),
            "author_handle": _string(ref.get("author_handle")),
            "text": _string(ref.get("text")),
            "relevance": _string(ref.get("relevance")),
        })
    return out


def _clean_watcher_clues(clues) -> dict:
    if not isinstance(clues, dict):
        clues = {}
    return {
        "accounts": _string_list(clues.get("accounts")),
        "phrases": _string_list(clues.get("phrases")),
        "keywords": _string_list(clues.get("keywords")),
        "catalysts": _string_list(clues.get("catalysts")),
    }


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string(v) for v in value if _string(v)][:12]


def _string(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


_CITATION_RE = re.compile(r"\[\[?\d+\]?\]\([^)]*\)")  # [[1]](url) or [1](url)
_BARE_URL_RE = re.compile(r"https?://\S+")


def _scrub(text: str, project_handle: str = "") -> str:
    """Final-pass cleanup: strip citation markers, bare URLs, em dashes, and
    the PROJECT'S own @handle (external @s preserved). Also lowercases the
    output as a hard guarantee since the model occasionally caps proper nouns.

    The system prompt asks Grok to follow these rules but it doesn't always
    listen, so we enforce them here.
    """
    text = _CITATION_RE.sub("", text)
    text = _BARE_URL_RE.sub("", text)

    # Only strip the project's own @handle, leaving external handles intact.
    # Also strip leading/trailing underscores from the handle body so we don't
    # leave artifacts like "veilnet_" or "_dphnAI" in the output.
    ph = (project_handle or "").lstrip("@").strip()
    if ph:
        ph_clean = ph.strip("_") or ph  # never collapse to empty
        text = re.sub(
            rf"(?<![a-zA-Z0-9_])@?{re.escape(ph)}\b",
            ph_clean,
            text,
            flags=re.IGNORECASE,
        )

    text = text.replace("—", ", ").replace("–", ", ")
    # collapse double spaces and trailing-space artifacts from substitutions
    text = re.sub(r"\s+", " ", text).strip()
    # tidy up ", ." or " ." artifacts
    text = re.sub(r"\s*([.,;:!?])", r"\1", text)
    # final pass: force lowercase to enforce the style rule.
    # we lowercase the WHOLE output. @handles stay as written since the casing
    # of an @handle doesn't matter on X anyway.
    text = text.lower()
    return text
