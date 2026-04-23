"""
Push notifications via ntfy.sh — free, no account required.

Set NTFY_TOPIC in Railway env to your unique topic name (e.g., the
string you picked when subscribing in the ntfy phone app).
Notifications arrive on any device subscribed to that topic.

Silently no-ops when NTFY_TOPIC isn't set, so the bot still runs
normally even without push config.
"""
import logging
import os

import requests

log = logging.getLogger(__name__)

_NTFY_URL = "https://ntfy.sh"


def _send(title: str, message: str, priority: int = 3, tags: list = None) -> bool:
    """
    Low-level notification. priority 1 (min) to 5 (max).
    Returns True on success, False otherwise (never raises).
    """
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False

    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = requests.post(
            f"{_NTFY_URL}/{topic}",
            data=message.encode("utf-8"),
            headers=headers,
            timeout=5,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning(f"ntfy send failed: {e}")
        return False


def notify_entry(symbol: str, qty: int, price: float, gap_pct: float,
                 sentiment: float, structure: str):
    title = f"ENTRY {symbol} x{qty} @ ${price:.2f}"
    msg = (
        f"Gap {gap_pct:+.1%} · Sent {sentiment:+.2f}\n"
        f"Exit structure: {structure}"
    )
    _send(title, msg, priority=3, tags=["chart_with_upwards_trend"])


def notify_exit(symbol: str, qty: int, exit_price: float, pnl: float, reason: str):
    sign = "+" if pnl >= 0 else ""
    tag = "moneybag" if pnl > 0 else "small_red_triangle_down"
    title = f"EXIT {symbol} {sign}${pnl:.2f}"
    msg = (
        f"{qty} shares @ ${exit_price:.2f}\n"
        f"Reason: {reason}"
    )
    _send(title, msg, priority=3, tags=[tag])


def notify_eod(positions_closed: int, day_pnl: float):
    sign = "+" if day_pnl >= 0 else ""
    tag = "white_check_mark" if day_pnl >= 0 else "warning"
    title = f"EOD Close · Day P&L {sign}${day_pnl:.2f}"
    msg = f"Closed {positions_closed} open position(s). See daily brief email for full recap."
    _send(title, msg, priority=2, tags=[tag])


def notify_error(msg: str):
    _send("Bot Error", msg, priority=5, tags=["rotating_light"])
