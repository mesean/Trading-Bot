"""
Pre-market news sentiment scoring via Claude Haiku.

Fetches 24h of news for every pre-market candidate, asks Claude to
score each stock on a -1 (bearish) to +1 (bullish) scale based on
short-term price impact, and returns {symbol: score}.

Called once per trading day from pre_market_scan, before the opening
range is built. The ORB strategy then uses these scores as a 7th filter:
stocks with clearly negative news are blocked from entry regardless of
how the breakout looks.

Requires ANTHROPIC_API_KEY. Gracefully returns neutral (0.0) for all
symbols if the key is missing or the API call fails — never blocks
the trading loop.
"""
import logging
import os

from news import fetch_news

log = logging.getLogger(__name__)

_TOOLS = [
    {
        "name": "score_sentiment",
        "description": (
            "Assign a sentiment score to each stock based on its recent news. "
            "Return one entry per symbol provided, even for symbols with no news."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scores": {
                    "type": "array",
                    "description": "One entry per symbol.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "score": {
                                "type": "number",
                                "minimum": -1.0,
                                "maximum": 1.0,
                                "description": "Sentiment score: -1 strongly bearish, 0 neutral, +1 strongly bullish",
                            },
                            "reason": {
                                "type": "string",
                                "description": "Brief (5-15 word) justification",
                            },
                        },
                        "required": ["symbol", "score", "reason"],
                    },
                }
            },
            "required": ["scores"],
        },
    }
]


def score_candidates(symbols: list) -> dict:
    """
    Return {symbol: score in [-1, 1]} for each candidate.
    Missing symbols or API failures default to 0.0 (neutral).
    """
    if not symbols:
        return {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.info("Sentiment scoring skipped — ANTHROPIC_API_KEY not set")
        return {s: 0.0 for s in symbols}

    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed — skipping sentiment")
        return {s: 0.0 for s in symbols}

    # Fetch last 24h of news for the candidate set
    news_items = fetch_news(symbols, hours_back=24, limit=50)

    # Index headlines by symbol (news items can tag multiple symbols)
    by_symbol: dict = {s: [] for s in symbols}
    for item in news_items:
        headline = (item.get("headline") or "").strip()
        if not headline:
            continue
        for sym in item.get("symbols", []):
            if sym in by_symbol:
                by_symbol[sym].append(headline)

    # Build the prompt
    lines = []
    for sym in symbols:
        headlines = by_symbol[sym][:5]  # cap per symbol for prompt size
        if headlines:
            lines.append(f"\n{sym}:")
            for h in headlines:
                lines.append(f"  - {h}")
        else:
            lines.append(f"\n{sym}: (no recent news)")

    headlines_block = "".join(lines)

    system = (
        "You are a financial news sentiment analyst scoring stocks for an "
        "intraday momentum strategy (Opening Range Breakout). Score each "
        "stock -1 to +1 based on likely short-term (same-day) price impact.\n\n"
        "POSITIVE (+0.3 to +1): earnings beats, analyst upgrades, strong guidance, "
        "new product launches, major contract wins, positive M&A, insider buying, "
        "upward revisions.\n"
        "NEGATIVE (-0.3 to -1): earnings misses, downgrades, guidance cuts, "
        "lawsuits, SEC actions, product recalls, major exec departures, insider "
        "selling, dilutive offerings.\n"
        "NEUTRAL (-0.2 to +0.2): no news, routine coverage, general market "
        "commentary, minor partnerships, reiterated ratings."
    )

    prompt = f"""Score the sentiment of each stock below based on its recent headlines.
Return a score for EVERY symbol listed — use 0.0 for symbols with no news.

HEADLINES (last 24h):
{headlines_block}

Call score_sentiment with one entry per symbol."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=_TOOLS,
            tool_choice={"type": "tool", "name": "score_sentiment"},
        )
    except Exception as e:
        log.error(f"Sentiment API call failed: {e}")
        return {s: 0.0 for s in symbols}

    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        log.warning("Sentiment: no tool call in response")
        return {s: 0.0 for s in symbols}

    result = {s: 0.0 for s in symbols}
    reasons: dict = {}
    for entry in tool_use.input.get("scores", []):
        sym = entry.get("symbol")
        if sym in result:
            result[sym] = float(entry.get("score", 0.0))
            reasons[sym] = entry.get("reason", "")

    # Log notable scores
    notable = [(s, v, reasons.get(s, "")) for s, v in result.items() if abs(v) >= 0.3]
    if notable:
        notable.sort(key=lambda x: x[1])
        for s, v, r in notable[:5]:
            log.info(f"  Sentiment {s:<6}: {v:+.2f}  {r[:60]}")
        for s, v, r in notable[-5:]:
            log.info(f"  Sentiment {s:<6}: {v:+.2f}  {r[:60]}")

    n_scored = sum(1 for v in result.values() if v != 0.0)
    log.info(f"Sentiment: scored {n_scored}/{len(symbols)} symbols with non-neutral score")

    return result
