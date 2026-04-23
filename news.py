"""
Free news feed via Alpaca's Benzinga-powered news API.
Already bundled with your Alpaca account — no extra credentials needed.

Used by the Claude research loop so end-of-day parameter decisions
can factor in the day's headlines and sentiment context.
"""
import logging
from datetime import datetime, timedelta, timezone

import requests

import config

log = logging.getLogger(__name__)

_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"


def fetch_news(symbols: list, hours_back: int = 24, limit: int = 50) -> list:
    """
    Fetch recent news for the given symbols.
    Returns a list of {symbols, headline, summary, author, source, created_at, url}.
    Silently returns [] on any error — never blocks the trading loop.
    """
    if not symbols:
        return []

    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    params = {
        # Alpaca caps URL length — trim if someone passes a giant universe
        "symbols": ",".join(symbols[:50]),
        "start": since.isoformat().replace("+00:00", "Z"),
        "limit": min(limit, 50),
        "sort": "desc",
    }
    headers = {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_API_SECRET,
    }

    try:
        resp = requests.get(_NEWS_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("news", [])
    except Exception as e:
        log.warning(f"News fetch failed: {e}")
        return []

    normalised = []
    for item in items:
        normalised.append({
            "symbols":    item.get("symbols", []),
            "headline":   item.get("headline", ""),
            "summary":    item.get("summary", ""),
            "source":     item.get("source", ""),
            "created_at": item.get("created_at", ""),
            "url":        item.get("url", ""),
        })
    return normalised


def summarise_for_prompt(items: list, max_items: int = 20) -> str:
    """Format news items as a concise block for Claude's prompt."""
    if not items:
        return "No recent news available."
    lines = []
    for item in items[:max_items]:
        syms = ",".join(item.get("symbols", [])[:4])
        headline = item.get("headline", "").strip()
        if headline:
            lines.append(f"- [{syms}] {headline}")
    return "\n".join(lines) if lines else "No recent news available."
