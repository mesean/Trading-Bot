"""
Generates a markdown daily brief at end of day.
- Writes to logs/daily_brief_YYYY-MM-DD.md
- Prints to stdout (Railway logs)
- Emails to GMAIL_ADDRESS if credentials are set
"""
import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _send_email(subject: str, body: str):
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_address or not gmail_password:
        log.info("Email not configured — skipping")
        return
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = gmail_address
        msg["To"] = gmail_address
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_password)
            server.sendmail(gmail_address, gmail_address, msg.as_string())
        log.info(f"Daily brief emailed to {gmail_address}")
    except Exception as e:
        log.error(f"Email failed: {e}")


def generate_brief(broker, strategy) -> Path:
    now = datetime.now(config.ET)
    date_str = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y-%m-%d %H:%M ET")

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

    pnl_emoji = "🟢" if day_pnl >= 0 else "🔴"

    lines = [
        f"Daily Trading Brief — {date_str}",
        f"Generated: {timestamp} | Mode: {'PAPER' if config.PAPER_TRADING else 'LIVE'}",
        "",
        "PORTFOLIO",
        f"  End-of-day value : ${portfolio_value:,.2f}",
        f"  Start-of-day     : ${last_equity:,.2f}",
        f"  Budget cap       : ${config.MAX_CAPITAL:,.2f}",
        f"  Day P&L          : {pnl_emoji} ${day_pnl:+,.2f} ({day_pnl_pct:+.2f}%)",
        f"  Cash             : ${cash:,.2f}",
        "",
        "TODAY'S TRADES",
    ]

    if not trades:
        lines.append("  No trades — no qualifying setups found.")
    else:
        lines += [
            f"  {len(closed_trades)} completed | {len(wins)} wins / {len(losses)} losses | Win rate: {win_rate:.0f}%",
            f"  Trade P&L: ${total_trade_pnl:+,.2f}",
            "",
            f"  {'Symbol':<6}  {'Entry':>8}  {'Exit':>8}  {'Qty':>4}  {'P&L':>8}  {'Exit':>12}  {'Gap':>6}  {'Vol':>5}",
            f"  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*4}  {'-'*8}  {'-'*12}  {'-'*6}  {'-'*5}",
        ]
        for t in closed_trades:
            ep = t.get("fill_price", 0)
            xp = t.get("exit_price", 0) or 0
            pnl = t.get("pnl", 0) or 0
            reason = t.get("exit_reason", "—")
            gap = t.get("gap_pct", 0) or 0
            vmult = t.get("volume_mult", 0) or 0
            lines.append(
                f"  {t['symbol']:<6}  ${ep:>7.2f}  ${xp:>7.2f}  {t['qty']:>4}  "
                f"${pnl:>+7.2f}  {reason:<12}  {gap:>5.1%}  {vmult:>4.1f}x"
            )
        for t in open_trades:
            ep = t.get("fill_price", 0)
            lines.append(
                f"  {t['symbol']:<6}  ${ep:>7.2f}  {'—':>8}  {t['qty']:>4}  "
                f"{'open':>8}  {'—':<12}  {(t.get('gap_pct') or 0):>5.1%}  {(t.get('volume_mult') or 0):>4.1f}x"
            )

    lines += [
        "",
        "STRATEGY PARAMETERS",
        f"  Opening range    : {params['opening_range_minutes']} min",
        f"  Min gap          : {params['min_gap_pct']:.1%}",
        f"  Min volume mult  : {params['min_volume_mult']:.1f}x",
        f"  Trailing stop    : {params.get('trail_percent', 2.0):.1f}%",
        f"  Risk per trade   : {params['risk_per_trade']:.1%}",
        f"  Max positions    : {params['max_positions']}",
        f"  Size multiplier  : {regime:.2f}x (regime)",
        "",
        "NOTES",
    ]

    if regime < 1.0:
        lines.append(f"  ⚠  High-volatility regime — position sizes at {regime:.0%}.")
    if len(closed_trades) >= 3 and win_rate < 40:
        lines.append("  ⚠  Win rate below 40% — filters will tighten at Saturday review.")
    if len(closed_trades) >= 3 and win_rate >= 65:
        lines.append("  ✓  Strong day — parameters may loosen at Saturday review.")
    if lines[-1] == "NOTES":
        lines.append("  No special notes.")

    lines += ["", "---", "Next research update: Saturday 08:00 ET"]

    content = "\n".join(lines) + "\n"

    # 1. Write to log file
    brief_path = config.LOG_DIR / f"daily_brief_{date_str}.txt"
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(content)

    # 2. Print to stdout (Railway logs)
    print("\n" + "=" * 60)
    print(content)
    print("=" * 60 + "\n")

    # 3. Email
    subject = f"{pnl_emoji} Trading Brief {date_str} — ${day_pnl:+,.2f} ({day_pnl_pct:+.2f}%)"
    _send_email(subject, content)

    return brief_path
