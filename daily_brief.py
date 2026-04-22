"""
Generates a markdown daily brief at end of day and writes it to logs/.
Also prints the full brief to stdout so it shows up in Railway logs.
"""
import logging
from datetime import datetime
from pathlib import Path

import config

log = logging.getLogger(__name__)


def generate_brief(broker, strategy) -> Path:
    now = datetime.now(config.ET)
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M ET")

    # Account state
    try:
        account = broker.get_account()
        portfolio_value = float(account.portfolio_value)
        last_equity = float(account.last_equity or portfolio_value)
        cash = float(account.cash)
        day_pnl = portfolio_value - last_equity
        day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0
    except Exception as e:
        log.error(f"Brief: failed to get account — {e}")
        portfolio_value = last_equity = cash = 0
        day_pnl = day_pnl_pct = 0

    trades = strategy.trades_today
    closed_trades = [t for t in trades if t.get("pnl") is not None]
    open_trades = [t for t in trades if t.get("pnl") is None]

    total_trade_pnl = sum(t["pnl"] for t in closed_trades)
    wins = [t for t in closed_trades if t["pnl"] > 0]
    losses = [t for t in closed_trades if t["pnl"] <= 0]
    win_rate = len(wins) / len(closed_trades) * 100 if closed_trades else 0

    params = strategy.params
    regime = params.get("position_size_mult", 1.0)

    # --- Build the markdown ---
    lines = [
        f"# Daily Trading Brief — {date_str}",
        f"*Generated: {timestamp}*",
        f"*Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'}*",
        "",
        "## Portfolio",
        f"| | |",
        f"|---|---|",
        f"| End-of-day value | **${portfolio_value:,.2f}** |",
        f"| Start-of-day value | ${last_equity:,.2f} |",
        f"| Trading budget cap | ${config.MAX_CAPITAL:,.2f} |",
        f"| Day P&L | {'🟢' if day_pnl >= 0 else '🔴'} **${day_pnl:+,.2f}** ({day_pnl_pct:+.2f}%) |",
        f"| Cash | ${cash:,.2f} |",
        "",
        "## Today's Trades",
    ]

    if not trades:
        lines.append("*No trades today — no qualifying setups found.*")
    else:
        lines += [
            f"**{len(closed_trades)} completed** | {len(wins)} wins / {len(losses)} losses | Win rate: {win_rate:.0f}%",
            f"Trade P&L: **${total_trade_pnl:+,.2f}**",
            "",
            "| Symbol | Entry | Exit | Qty | P&L | Exit Reason | Gap | Vol Mult |",
            "|--------|-------|------|-----|-----|-------------|-----|----------|",
        ]
        for t in closed_trades:
            ep = t.get("fill_price", 0)
            xp = t.get("exit_price", 0) or 0
            pnl = t.get("pnl", 0) or 0
            reason = t.get("exit_reason", "—")
            gap = t.get("gap_pct", 0) or 0
            vmult = t.get("volume_mult", 0) or 0
            lines.append(
                f"| {t['symbol']} | ${ep:.2f} | ${xp:.2f} | {t['qty']} | "
                f"**${pnl:+.2f}** | {reason} | {gap:.1%} | {vmult:.1f}x |"
            )
        for t in open_trades:
            ep = t.get("fill_price", 0)
            lines.append(
                f"| {t['symbol']} | ${ep:.2f} | — | {t['qty']} | *open* | — | "
                f"{(t.get('gap_pct') or 0):.1%} | {(t.get('volume_mult') or 0):.1f}x |"
            )

    lines += [
        "",
        "## Strategy Parameters",
        "| Parameter | Value |",
        "|-----------|-------|",
        f"| Opening range | {params['opening_range_minutes']} min |",
        f"| Min gap | {params['min_gap_pct']:.1%} |",
        f"| Min volume mult | {params['min_volume_mult']:.1f}x |",
        f"| Take-profit mult | {params['take_profit_mult']:.1f}x risk |",
        f"| Risk per trade | {params['risk_per_trade']:.1%} |",
        f"| Max positions | {params['max_positions']} |",
        f"| Position size mult | {regime:.2f} (regime adjustment) |",
        "",
        "## Notes",
    ]

    if regime < 1.0:
        lines.append(f"- ⚠️ High-volatility regime detected — position sizes reduced to {regime:.0%}.")
    if len(closed_trades) >= 3 and win_rate < 40:
        lines.append("- ⚠️ Win rate below 40% today — strategy will tighten filters at next weekly review.")
    if len(closed_trades) >= 3 and win_rate >= 65:
        lines.append("- ✅ Strong day — parameters may be loosened slightly at next weekly review.")
    if not lines[-1].startswith("-") and not lines[-1].startswith("*"):
        lines.append("- No special notes.")

    lines += ["", "---", f"*Next research update: Saturday 08:00 ET*"]

    content = "\n".join(lines) + "\n"

    # Write to file
    brief_path = config.LOG_DIR / f"daily_brief_{date_str}.md"
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Also print to stdout for Railway logs
    print("\n" + "=" * 60)
    print(content)
    print("=" * 60 + "\n")

    return brief_path
