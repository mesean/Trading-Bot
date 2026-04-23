"""
Claude API research module — intelligent strategy analysis and adaptation.

Runs after the daily brief on trading days and on Saturday mornings.
Sends trade history, current params, and market context to Claude (Opus 4.7),
gets back reasoned parameter recommendations, applies them within safety bounds.

Requires ANTHROPIC_API_KEY env var. Silently skips if not set.
"""
import json
import logging
import os
from datetime import datetime, timedelta

import config
from news import fetch_news, summarise_for_prompt
from analytics import compute_stats

log = logging.getLogger(__name__)

# Maximum fractional move in any single session (on top of hard bounds below)
MAX_PARAM_STEP = 0.30

# Hard min/max limits — Claude can never push beyond these
PARAM_BOUNDS = {
    "opening_range_minutes": (5,      30),
    "min_gap_pct":           (0.002,  0.030),
    "min_volume_mult":       (1.0,    5.0),
    "trail_percent":         (1.0,    5.0),
    "risk_per_trade":        (0.005,  0.05),
    "rs_spy_min":            (0.000,  0.010),
    "min_premarket_vol":     (10_000, 500_000),
}

# The tool Claude must call to propose changes
_TOOLS = [
    {
        "name": "update_strategy_params",
        "description": (
            "Apply recommended parameter updates to the ORB strategy. "
            "Only include parameters you are confident should change based on the data. "
            "If you have no numeric changes to suggest, call this with only `reasoning`."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_range_minutes": {
                    "type": "integer",
                    "description": "Minutes to build the opening range (5–30)",
                    "minimum": 5,
                    "maximum": 30,
                },
                "min_gap_pct": {
                    "type": "number",
                    "description": "Minimum gap-up % required at open (0.002–0.030)",
                    "minimum": 0.002,
                    "maximum": 0.030,
                },
                "min_volume_mult": {
                    "type": "number",
                    "description": "Volume pace vs average daily volume required (1.0–5.0)",
                    "minimum": 1.0,
                    "maximum": 5.0,
                },
                "trail_percent": {
                    "type": "number",
                    "description": "Trailing stop distance as % of peak price (1.0–5.0)",
                    "minimum": 1.0,
                    "maximum": 5.0,
                },
                "risk_per_trade": {
                    "type": "number",
                    "description": "Portfolio fraction risked per trade (0.005–0.05)",
                    "minimum": 0.005,
                    "maximum": 0.05,
                },
                "rs_spy_min": {
                    "type": "number",
                    "description": "Minimum required outperformance vs SPY (0.000–0.010)",
                    "minimum": 0.0,
                    "maximum": 0.010,
                },
                "min_premarket_vol": {
                    "type": "integer",
                    "description": "Minimum pre-market share volume to qualify (10000–500000)",
                    "minimum": 10000,
                    "maximum": 500000,
                },
                "reasoning": {
                    "type": "string",
                    "description": "Concise explanation of what the data shows and why each change helps",
                },
            },
            "required": ["reasoning"],
        },
    }
]


def _load_recent_trades(n: int = 40) -> list:
    if not config.TRADES_FILE.exists():
        return []
    with open(config.TRADES_FILE) as f:
        return json.load(f)[-n:]


def _clamp(current: float, proposed: float, key: str) -> float:
    """Move current toward proposed but respect step limit and hard bounds."""
    max_delta = abs(current) * MAX_PARAM_STEP
    clamped = current + max(-max_delta, min(max_delta, proposed - current))
    if key in PARAM_BOUNDS:
        lo, hi = PARAM_BOUNDS[key]
        clamped = max(lo, min(hi, clamped))
    return clamped


def run_claude_research(broker=None) -> str | None:
    """
    Analyse trade history with Claude and apply any recommended param changes.
    Returns a short summary string, or None if skipped.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.info("Claude research skipped — ANTHROPIC_API_KEY not set")
        return None

    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not installed — pip install anthropic")
        return None

    trades = _load_recent_trades(40)
    params = config.load_params()

    # Summarise performance stats
    closed = [t for t in trades if t.get("pnl") is not None]
    if closed:
        wins   = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        win_rate = len(wins) / len(closed)
        gross_win  = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        stats = (
            f"{len(closed)} closed trades | win rate {win_rate:.1%} | "
            f"profit factor {pf:.2f} | "
            f"avg win ${gross_win/len(wins):.2f} | avg loss ${gross_loss/len(losses):.2f}"
            if wins and losses else
            f"{len(closed)} closed trades | win rate {win_rate:.1%} | profit factor {pf:.2f}"
        )
    else:
        stats = "No closed trades yet."

    # Exit reason breakdown
    exit_counts: dict = {}
    for t in closed:
        r = t.get("exit_reason", "unknown")
        exit_counts[r] = exit_counts.get(r, 0) + 1

    # Recent SPY context (best-effort)
    spy_context = ""
    if broker is not None:
        try:
            from alpaca.data.timeframe import TimeFrame
            now = datetime.now(config.ET)
            spy_bars = broker.get_bars(
                ["SPY"], TimeFrame.Day,
                start=now - timedelta(days=12), end=now,
            )
            sym = broker.extract_symbol_bars(spy_bars, "SPY") if spy_bars is not None else None
            if sym is not None and len(sym) >= 2:
                rets = sym["close"].pct_change().dropna().tail(5)
                spy_context = "\nSPY last 5 sessions: " + "  ".join(f"{r:+.2%}" for r in rets)
        except Exception:
            pass

    # Compact trade list for the prompt
    trade_rows = [
        {
            "sym":      t.get("symbol"),
            "date":     (t.get("date") or t.get("entry_time", ""))[:10],
            "pnl":      round(t.get("pnl", 0), 2),
            "gap_pct":  f"{t.get('gap_pct', 0):.2%}",
            "vol_mult": t.get("volume_mult"),
            "exit":     t.get("exit_reason"),
        }
        for t in closed[-25:]
    ]

    # Full-history bucketed stats (hour/sector/gap/volume + MAE/MFE)
    stats_block = json.dumps(compute_stats(trades), indent=2, default=str)

    # Pull 24h of news for today's traded symbols + broad-market proxies
    traded_syms = list({t.get("symbol") for t in closed[-20:] if t.get("symbol")})
    news_syms = traded_syms + ["SPY", "QQQ"]
    news_items = fetch_news(news_syms, hours_back=24, limit=30)
    news_block = summarise_for_prompt(news_items, max_items=15)

    now_str = datetime.now(config.ET).strftime("%Y-%m-%d %H:%M ET")

    system = (
        "You are a quantitative analyst specializing in Opening Range Breakout (ORB) momentum "
        "strategies on US equities. Analyze trade data and recommend specific parameter adjustments "
        "to improve profitability. Be precise and conservative — prefer small, evidence-based changes "
        "over large swings. The bot trades paper money right now, but will trade real money soon."
    )

    prompt = f"""Review my ORB strategy performance and suggest parameter updates.

DATE: {now_str}{spy_context}

CURRENT PARAMETERS:
{json.dumps(params, indent=2)}

PERFORMANCE SUMMARY ({len(closed)} recent closed trades):
{stats}

EXIT REASONS: {json.dumps(exit_counts)}

TRADE DETAIL (newest last):
{json.dumps(trade_rows, indent=2)}

BUCKETED PERFORMANCE ANALYTICS:
{stats_block}

Interpretation notes:
- `mae_mfe.avg_mae_winners_pct`: how far winning trades dipped before recovering (absorb limit)
- `mae_mfe.avg_mfe_winners_pct`: peak unrealised gain of winners (captured vs. left on the table)
- `mae_mfe.avg_mfe_losers_pct`: peak unrealised gain of losers (missed take-profit opportunities)
- `by_hour` / `by_sector` / `by_gap` / `by_volume`: win rate and profit factor bucketed by entry characteristic — use these to spot where edge is concentrated.

RECENT NEWS (last 24h, traded symbols + SPY/QQQ):
{news_block}

STRATEGY CONTEXT:
• Entry: breakout above the first {params['opening_range_minutes']}-min high after 9:30 ET open
• Filters: gap ≥ {params['min_gap_pct']:.1%}, volume pace ≥ {params['min_volume_mult']}x avg,
  price > VWAP, stock outperforms SPY by ≥ {params['rs_spy_min']:.2%}
• Exit: {params['trail_percent']}% trailing stop below peak; hard close at 3:45 pm ET
• Universe: 37 liquid US large-caps and ETFs

Call `update_strategy_params` with your recommendations. Include numeric fields only where the data clearly supports a change. Always include a `reasoning` field."""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model="claude-opus-4-7",
            max_tokens=2048,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": prompt}],
            tools=_TOOLS,
            tool_choice={"type": "any"},
        ) as stream:
            response = stream.get_final_message()
    except Exception as e:
        log.error(f"Claude research API call failed: {e}")
        return None

    # Extract tool call
    tool_use = next((b for b in response.content if b.type == "tool_use"), None)
    if tool_use is None:
        log.info("Claude research: no tool call in response")
        return "No changes recommended."

    recs = tool_use.input
    reasoning = recs.get("reasoning", "")
    log.info(f"Claude research: {reasoning}")

    # Apply numeric changes with safety clamping
    adjustable = set(PARAM_BOUNDS.keys())
    changes: dict = {}
    for key in recs:
        if key == "reasoning" or key not in adjustable:
            continue
        if key not in params:
            continue
        proposed = float(recs[key])
        current  = float(params[key])
        clamped  = _clamp(current, proposed, key)
        # Round integers
        if isinstance(params[key], int) or key in ("opening_range_minutes", "min_premarket_vol"):
            clamped = int(round(clamped))
        else:
            clamped = round(clamped, 5)
        if abs(clamped - current) > 1e-9:
            changes[key] = (current, clamped)
            params[key] = clamped

    if changes:
        config.save_params(params)
        change_str = " | ".join(f"{k}: {v[0]} → {v[1]}" for k, v in changes.items())
        log.info(f"Claude research applied changes: {change_str}")
        return f"{change_str}  //  {reasoning}"
    else:
        log.info("Claude research: parameters unchanged")
        return f"No changes applied  //  {reasoning}"
