"""
Live visual dashboard for the trading bot — Thinkorswim-inspired layout
with hero banner, animated bot mascot, and tabbed navigation.

Runs as a separate Railway service (web). Reads state from Alpaca and
the shared data volume — never touches bot state directly. Auto-refreshes
every 10 seconds while preserving the active tab via URL hash.
"""
import json
import logging
import os
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn

import config
from broker import Broker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("dashboard")

app = FastAPI(title="Trading Bot Dashboard")
broker = Broker()


def _load_trades() -> list:
    if not config.TRADES_FILE.exists():
        return []
    try:
        with open(config.TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _today_iso() -> str:
    return datetime.now(config.ET).date().isoformat()


def _fmt_money(x: float, plus: bool = False) -> str:
    sign = "+" if plus and x >= 0 else ""
    return f"{sign}${x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x:+.2f}%"


def _colour(x: float) -> str:
    if x > 0:
        return "pos"
    if x < 0:
        return "neg"
    return "neu"


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { background: #000; color: #cfcfcf; }
body {
  font-family: 'Roboto Condensed', 'Arial Narrow', 'Segoe UI', Tahoma, sans-serif;
  font-size: 12px;
  line-height: 1.35;
  min-height: 100vh;
  background:
    radial-gradient(ellipse at top, rgba(0, 163, 224, 0.08) 0%, transparent 50%),
    radial-gradient(ellipse at bottom, rgba(0, 255, 127, 0.04) 0%, transparent 50%),
    #000;
  background-attachment: fixed;
}

/* ============== HERO BANNER ============== */
.hero {
  position: relative;
  background:
    linear-gradient(180deg, rgba(0, 163, 224, 0.12) 0%, rgba(10,10,10,0.95) 100%),
    #050505;
  border-bottom: 2px solid #00a3e0;
  padding: 22px 24px;
  display: grid;
  grid-template-columns: 200px 1fr auto;
  align-items: center;
  gap: 24px;
  overflow: hidden;
}
.hero::before {
  /* subtle moving grid lines for that trading-floor feel */
  content: '';
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(0, 163, 224, 0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 163, 224, 0.06) 1px, transparent 1px);
  background-size: 40px 40px;
  animation: grid-drift 30s linear infinite;
  pointer-events: none;
  opacity: 0.5;
}
@keyframes grid-drift {
  from { transform: translate(0, 0); }
  to   { transform: translate(40px, 40px); }
}
.hero-left, .hero-center, .hero-right { position: relative; z-index: 1; }
.hero-title {
  color: #00a3e0;
  font-size: 22px;
  font-weight: 800;
  letter-spacing: 4px;
  font-family: 'Segoe UI', Tahoma, sans-serif;
  text-shadow: 0 0 12px rgba(0, 163, 224, 0.6);
}
.hero-subtitle {
  color: #888; font-size: 11px; letter-spacing: 2px; margin-top: 4px;
}

/* Animated bot mascot */
.bot-container {
  position: relative;
  width: 110px; height: 130px;
  display: flex; justify-content: center; align-items: center;
  animation: bob 3.5s ease-in-out infinite;
}
@keyframes bob {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-6px); }
}
.bot-pulse {
  position: absolute;
  width: 130px; height: 130px;
  border-radius: 50%;
  border: 2px solid #00ff7f;
  animation: pulse-ring 2.4s ease-out infinite;
  opacity: 0;
}
.bot-pulse.market-closed {
  border-color: #555;
  animation: none;
  opacity: 0.3;
}
@keyframes pulse-ring {
  0%   { transform: scale(0.6); opacity: 0; }
  30%  { opacity: 0.6; }
  100% { transform: scale(1.4); opacity: 0; }
}
.bot { width: 100px; height: 120px; }
.bot .eye {
  transform-origin: center;
  transform-box: fill-box;
  animation: blink 5s infinite;
}
.bot .eye.right { animation-delay: 0.05s; }
@keyframes blink {
  0%, 92%, 100% { transform: scaleY(1); }
  94%, 96%      { transform: scaleY(0.05); }
}
.bot .antenna-light {
  animation: antenna-pulse 1.4s ease-in-out infinite;
}
@keyframes antenna-pulse {
  0%, 100% { opacity: 1; r: 4; }
  50%      { opacity: 0.4; r: 5; }
}

/* Hero center: big P&L */
.hero-center {
  text-align: center;
  display: flex; flex-direction: column; align-items: center; gap: 4px;
}
.hero-pnl-label {
  color: #888; font-size: 11px; text-transform: uppercase; letter-spacing: 3px;
}
.hero-pnl-value {
  font-size: 56px; font-weight: 700;
  font-family: 'Consolas', 'Courier New', monospace;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 20px currentColor;
  line-height: 1;
  margin-top: 6px;
}
.hero-pnl-pct {
  font-size: 18px; font-weight: 600;
  font-family: 'Consolas', monospace;
  margin-top: 2px;
}

/* Hero right: status info */
.hero-right {
  display: flex; flex-direction: column; align-items: flex-end; gap: 8px;
}
.hero-mode {
  padding: 5px 14px; font-weight: 800; font-size: 11px;
  letter-spacing: 3px;
}
.hero-mode.paper { background: #00a3e0; color: #000; }
.hero-mode.live  { background: #ff3030; color: #fff; }
.hero-market {
  display: flex; align-items: center; gap: 8px;
  color: #cfcfcf; font-size: 12px; letter-spacing: 1.5px;
}
.hero-market .dot {
  width: 10px; height: 10px; border-radius: 50%;
}
.dot-open   { background: #00ff7f; box-shadow: 0 0 12px #00ff7f; animation: pulse-dot 1.6s ease-in-out infinite; }
.dot-closed { background: #555; }
@keyframes pulse-dot {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.4; }
}
.hero-time {
  color: #666; font-size: 10px; font-family: 'Consolas', monospace;
}
.hero-portfolio {
  color: #fff; font-size: 14px; font-family: 'Consolas', monospace; font-weight: 600;
  margin-top: 4px;
}

/* ============== TAB NAV ============== */
.tabs {
  display: flex;
  background: #0a0a0a;
  border-bottom: 1px solid #2a2a2a;
  padding: 0 12px;
  overflow-x: auto;
}
.tab-link {
  color: #888;
  text-decoration: none;
  padding: 12px 20px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px;
  text-transform: uppercase;
  border-bottom: 2px solid transparent;
  transition: all 0.2s ease;
  white-space: nowrap;
}
.tab-link:hover { color: #cfcfcf; background: #111; }
.tab-link.active {
  color: #00a3e0;
  border-bottom-color: #00a3e0;
  background: linear-gradient(180deg, transparent 0%, rgba(0, 163, 224, 0.08) 100%);
}

/* ============== TAB CONTENT (CSS-only via :target) ============== */
.tab-content { display: none; padding: 12px; }
.tab-content:target { display: block; animation: tab-fade 0.3s ease; }
/* Default tab when no :target is set */
body:not(:has(:target)) #overview { display: block; }
@keyframes tab-fade {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ============== PANELS ============== */
.row { display: grid; gap: 10px; margin-bottom: 10px; }
.row-2col { grid-template-columns: 1fr 1fr; }
@media (max-width: 900px) {
  .row-2col { grid-template-columns: 1fr; }
  .hero { grid-template-columns: 1fr; text-align: center; }
  .hero-right { align-items: center; }
  .hero-pnl-value { font-size: 42px; }
}

.panel {
  background: #0a0a0a;
  border: 1px solid #2a2a2a;
  transition: border-color 0.2s ease;
}
.panel:hover { border-color: #00a3e0; }
.panel-header {
  background: #141414;
  color: #00a3e0;
  padding: 6px 14px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px;
  border-bottom: 1px solid #2a2a2a;
  text-transform: uppercase;
  display: flex; justify-content: space-between; align-items: center;
}
.panel-header .meta {
  color: #666; font-weight: 400; letter-spacing: 1px; font-size: 10px;
}
.panel-body { padding: 0; }
.panel-body.padded { padding: 12px 14px; }

/* Metric tiles */
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0;
}
.metric {
  border-right: 1px solid #2a2a2a;
  padding: 12px 16px;
  transition: background 0.2s ease;
}
.metric:hover { background: #0d0d0d; }
.metric:last-child { border-right: none; }
.metric .label {
  color: #888; font-size: 9px; text-transform: uppercase;
  letter-spacing: 2px; margin-bottom: 4px; font-weight: 600;
}
.metric .value {
  font-size: 22px; font-weight: 600; color: #fff;
  font-family: 'Consolas', 'Courier New', monospace;
  font-variant-numeric: tabular-nums;
}
.metric .sub {
  font-size: 11px; margin-top: 2px;
  font-family: 'Consolas', monospace;
}

/* Tables */
table { width: 100%; border-collapse: collapse; font-family: 'Consolas','Courier New',monospace; }
thead { background: #141414; }
th {
  color: #00a3e0; text-align: left;
  padding: 6px 12px; font-weight: 700; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px;
  border-bottom: 1px solid #2a2a2a;
}
td {
  padding: 6px 12px; font-size: 12px;
  border-bottom: 1px solid #151515;
  transition: background 0.15s ease;
}
tbody tr { transition: background 0.15s ease; }
tbody tr:hover { background: #101010; }
tbody tr:hover td { color: #fff; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.sym {
  color: #ffd966; font-weight: 700; letter-spacing: 0.5px;
  cursor: default;
}
tbody tr:hover .sym { color: #ffe680; text-shadow: 0 0 6px rgba(255, 217, 102, 0.4); }

/* Colors */
.pos { color: #00ff7f; }
.neg { color: #ff3030; }
.neu { color: #cfcfcf; }
.warn { color: #ffaa00; }
.muted { color: #666; }

.empty {
  color: #666; padding: 24px; font-style: italic;
  text-align: center; font-size: 11px;
}

/* Parameters */
.params { padding: 14px; line-height: 2; }
.params .p {
  display: inline-block;
  margin: 3px 8px 3px 0;
  padding: 4px 10px;
  background: #101010;
  border-left: 2px solid #00a3e0;
  font-family: 'Consolas', monospace;
  font-size: 11px;
  transition: all 0.2s ease;
}
.params .p:hover {
  background: #161616;
  border-left-color: #00ff7f;
  transform: translateX(2px);
}
.params .p .k {
  color: #888; text-transform: uppercase;
  font-size: 9px; letter-spacing: 1px;
}
.params .p .v { color: #fff; margin-left: 6px; font-weight: 600; }

.pill {
  display: inline-block;
  padding: 1px 7px;
  background: #1a1a1a;
  border: 1px solid #333;
  font-size: 10px;
  letter-spacing: 0.5px;
  border-radius: 2px;
}

/* Footer */
.footer {
  text-align: center;
  padding: 16px;
  color: #444;
  font-size: 10px;
  letter-spacing: 1px;
  font-family: 'Consolas', monospace;
}
"""


def _bot_svg(market_open: bool, day_pnl: float) -> str:
    """SVG mascot — eyes blink, antenna pulses, body bobs, mood reflects P&L."""
    if day_pnl > 0:
        eye_color = "#00ff7f"
        mouth = '<path d="M 35 52 Q 50 60 65 52" stroke="#00ff7f" stroke-width="2.5" fill="none"/>'
    elif day_pnl < 0:
        eye_color = "#ff3030"
        mouth = '<path d="M 35 56 Q 50 48 65 56" stroke="#ff3030" stroke-width="2.5" fill="none"/>'
    else:
        eye_color = "#00a3e0"
        mouth = '<rect x="36" y="52" width="28" height="3" rx="1" fill="#00a3e0"/>'

    chest_color = "#00ff7f" if market_open else "#666"
    return f"""
    <svg class="bot" viewBox="0 0 100 120">
      <line x1="50" y1="2" x2="50" y2="14" stroke="#00a3e0" stroke-width="2"/>
      <circle class="antenna-light" cx="50" cy="5" r="4" fill="{chest_color}"/>
      <rect x="18" y="14" width="64" height="50" rx="9"
            fill="#0a0a0a" stroke="#00a3e0" stroke-width="2"/>
      <circle class="eye left"  cx="35" cy="35" r="5.5" fill="{eye_color}"/>
      <circle class="eye right" cx="65" cy="35" r="5.5" fill="{eye_color}"/>
      {mouth}
      <rect x="24" y="64" width="52" height="42" rx="5"
            fill="#0a0a0a" stroke="#00a3e0" stroke-width="2"/>
      <circle cx="50" cy="85" r="6" fill="{chest_color}" opacity="0.7">
        <animate attributeName="opacity" values="0.4;1;0.4" dur="2s" repeatCount="indefinite"/>
      </circle>
      <rect x="8"  y="68" width="10" height="28" rx="3"
            fill="#0a0a0a" stroke="#00a3e0" stroke-width="2"/>
      <rect x="82" y="68" width="10" height="28" rx="3"
            fill="#0a0a0a" stroke="#00a3e0" stroke-width="2"/>
    </svg>
    """


def _tab_links(active: str) -> str:
    """Active tab is determined by URL hash via a tiny inline script that
    adds .active to the matching link after page load."""
    tabs = [
        ("overview",   "Overview"),
        ("positions",  "Positions"),
        ("orders",     "Orders"),
        ("trades",     "Trades"),
        ("analytics",  "7-Day"),
        ("params",     "Parameters"),
    ]
    return "".join(
        f'<a href="#{tid}" class="tab-link" data-tab="{tid}">{label}</a>'
        for tid, label in tabs
    )


def render() -> str:
    try:
        account = broker.get_account()
        portfolio_value = float(account.portfolio_value)
        last_equity = float(account.last_equity or portfolio_value)
        cash = float(account.cash)
        buying_power = float(account.buying_power or 0)
        day_pnl = portfolio_value - last_equity
        day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0
    except Exception as e:
        return (
            "<body style='background:#000;color:#ff3030;font-family:monospace;padding:20px'>"
            f"Alpaca error: {e}</body>"
        )

    try:
        positions = list(broker.trading.get_all_positions())
    except Exception:
        positions = []

    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        open_orders = list(broker.trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=50)
        ))
    except Exception:
        open_orders = []

    market_open = False
    market_status = "CLOSED"
    try:
        clock = broker.get_clock()
        if clock.is_open:
            market_open = True
            market_status = "OPEN"
    except Exception:
        pass

    all_trades = _load_trades()
    today_iso = _today_iso()
    todays_trades = [t for t in all_trades if t.get("date") == today_iso]
    week_ago = (datetime.now(config.ET) - timedelta(days=7)).date().isoformat()
    week_trades = [t for t in all_trades if t.get("date", "") >= week_ago and t.get("pnl") is not None]

    params = config.load_params()
    now_str = datetime.now(config.ET).strftime("%Y-%m-%d  %H:%M:%S ET")
    mode = "PAPER" if config.PAPER_TRADING else "LIVE"
    mode_class = "paper" if config.PAPER_TRADING else "live"
    pnl_class = _colour(day_pnl)
    market_dot = "dot-open" if market_open else "dot-closed"
    pulse_class = "" if market_open else "market-closed"

    bot_html = _bot_svg(market_open, day_pnl)

    # --- Hero banner ---
    hero_html = f"""
    <div class="hero">
      <div class="hero-left">
        <div class="hero-title">◆ TRADING BOT</div>
        <div class="hero-subtitle">Autonomous ORB · Self-Optimising</div>
      </div>
      <div class="hero-center">
        <div class="bot-container">
          <div class="bot-pulse {pulse_class}"></div>
          {bot_html}
        </div>
        <div class="hero-pnl-label">DAY P&amp;L</div>
        <div class="hero-pnl-value {pnl_class}">{_fmt_money(day_pnl, plus=True)}</div>
        <div class="hero-pnl-pct {pnl_class}">{_fmt_pct(day_pnl_pct)}</div>
      </div>
      <div class="hero-right">
        <div class="hero-mode {mode_class}">{mode}</div>
        <div class="hero-market">
          <span class="dot {market_dot}"></span>MARKET {market_status}
        </div>
        <div class="hero-portfolio">{_fmt_money(portfolio_value)}</div>
        <div class="hero-time">{now_str}</div>
      </div>
    </div>
    """

    # --- Account metrics (Overview) ---
    metrics_html = f"""
    <div class="metrics">
      <div class="metric"><div class="label">Portfolio</div><div class="value">{_fmt_money(portfolio_value)}</div></div>
      <div class="metric"><div class="label">Day P&amp;L</div><div class="value {pnl_class}">{_fmt_money(day_pnl, plus=True)}</div><div class="sub {pnl_class}">{_fmt_pct(day_pnl_pct)}</div></div>
      <div class="metric"><div class="label">Cash</div><div class="value">{_fmt_money(cash)}</div></div>
      <div class="metric"><div class="label">Buying Power</div><div class="value">{_fmt_money(buying_power)}</div></div>
      <div class="metric"><div class="label">Budget Cap</div><div class="value">{_fmt_money(config.MAX_CAPITAL)}</div></div>
      <div class="metric"><div class="label">Positions</div><div class="value">{len(positions)} <span class="muted" style="font-size:14px">/ {params['max_positions']}</span></div></div>
      <div class="metric"><div class="label">Working Orders</div><div class="value">{len(open_orders)}</div></div>
      <div class="metric"><div class="label">Trades Today</div><div class="value">{len(todays_trades)}</div></div>
    </div>
    """

    # --- Positions table ---
    if positions:
        rows = []
        for p in positions:
            upl = float(p.unrealized_pl or 0)
            upl_pct = float(p.unrealized_plpc or 0) * 100
            cls = _colour(upl)
            entry = float(p.avg_entry_price)
            current = float(p.current_price or 0)
            mkt_val = float(p.market_value or 0)
            rows.append(f"""
              <tr>
                <td class="sym">{p.symbol}</td>
                <td class="num">{p.qty}</td>
                <td class="num">{entry:.2f}</td>
                <td class="num">{current:.2f}</td>
                <td class="num">{_fmt_money(mkt_val)}</td>
                <td class="num {cls}">{_fmt_money(upl, plus=True)}</td>
                <td class="num {cls}">{_fmt_pct(upl_pct)}</td>
              </tr>""")
        positions_html = f"""<table>
          <thead><tr>
            <th>Symbol</th><th class="num">Qty</th><th class="num">Entry</th>
            <th class="num">Last</th><th class="num">Mkt Val</th>
            <th class="num">P&amp;L $</th><th class="num">P&amp;L %</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        positions_html = '<div class="empty">No open positions</div>'

    # --- Working orders ---
    if open_orders:
        rows = []
        for o in open_orders:
            side = str(o.side).replace("OrderSide.", "")
            otype = str(getattr(o, "type", "")).replace("OrderType.", "").lower()
            detail = ""
            if "limit" in otype and getattr(o, "limit_price", None):
                detail = f"{float(o.limit_price):.2f}"
            elif "trail" in otype and getattr(o, "trail_percent", None):
                detail = f"{float(o.trail_percent)}%"
            side_cls = "pos" if side == "BUY" else "neg"
            rows.append(f"""
              <tr>
                <td class="sym">{o.symbol}</td>
                <td class="{side_cls}">{side}</td>
                <td class="num">{o.qty}</td>
                <td><span class="pill">{otype}</span></td>
                <td class="num">{detail}</td>
              </tr>""")
        orders_html = f"""<table>
          <thead><tr>
            <th>Symbol</th><th>Side</th><th class="num">Qty</th>
            <th>Type</th><th class="num">Price/Trail</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        orders_html = '<div class="empty">No working orders</div>'

    # --- Today's trades ---
    if todays_trades:
        rows = []
        for t in todays_trades:
            pnl = t.get("pnl")
            if pnl is None:
                pnl_cell = '<span class="warn">OPEN</span>'
                exit_cell = '<span class="muted">—</span>'
            else:
                pnl_cell = f'<span class="{_colour(pnl)}">{_fmt_money(pnl, plus=True)}</span>'
                exit_cell = f"{float(t.get('exit_price') or 0):.2f}"
            gap = (t.get("gap_pct") or 0) * 100
            sent = t.get("sentiment_score", 0)
            sent_cls = _colour(sent)
            rows.append(f"""
              <tr>
                <td class="sym">{t['symbol']}</td>
                <td class="num">{float(t.get('fill_price', 0)):.2f}</td>
                <td class="num">{exit_cell}</td>
                <td class="num">{t.get('qty')}</td>
                <td class="num">{pnl_cell}</td>
                <td><span class="pill">{t.get('exit_reason') or '—'}</span></td>
                <td class="num">{gap:+.2f}%</td>
                <td class="num {sent_cls}">{sent:+.2f}</td>
              </tr>""")
        trades_html = f"""<table>
          <thead><tr>
            <th>Symbol</th><th class="num">Entry</th><th class="num">Exit</th>
            <th class="num">Qty</th><th class="num">P&amp;L</th>
            <th>Exit</th><th class="num">Gap</th><th class="num">Sent</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        trades_html = '<div class="empty">No trades yet today</div>'

    # --- Week stats ---
    if week_trades:
        wins = [t for t in week_trades if t["pnl"] > 0]
        losses = [t for t in week_trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(week_trades) * 100
        total_pnl = sum(t["pnl"] for t in week_trades)
        loss_sum = abs(sum(t["pnl"] for t in losses)) if losses else 0
        pf_str = f"{(sum(t['pnl'] for t in wins) / loss_sum):.2f}" if loss_sum > 0 else "∞"
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        week_html = f"""
        <div class="metrics">
          <div class="metric"><div class="label">Trades</div><div class="value">{len(week_trades)}</div></div>
          <div class="metric"><div class="label">Win Rate</div><div class="value">{win_rate:.0f}%</div></div>
          <div class="metric"><div class="label">Profit Factor</div><div class="value">{pf_str}</div></div>
          <div class="metric"><div class="label">Total P&amp;L</div><div class="value {_colour(total_pnl)}">{_fmt_money(total_pnl, plus=True)}</div></div>
          <div class="metric"><div class="label">Avg Win</div><div class="value pos">{_fmt_money(avg_win, plus=True)}</div></div>
          <div class="metric"><div class="label">Avg Loss</div><div class="value neg">{_fmt_money(avg_loss, plus=True)}</div></div>
        </div>"""
    else:
        week_html = '<div class="empty">No completed trades in the last 7 days</div>'

    # --- Parameters ---
    param_keys = [
        "opening_range_minutes", "min_gap_pct", "min_volume_mult", "trail_percent",
        "risk_per_trade", "max_positions", "rs_spy_min", "min_sentiment_score",
        "take_profit_mult", "tp1_R_mult", "position_size_mult",
        "partial_tp_enabled", "sentiment_filter_enabled",
    ]
    param_items = []
    for k in param_keys:
        if k not in params:
            continue
        v = params[k]
        if isinstance(v, bool):
            v_str = "ON" if v else "OFF"
        elif isinstance(v, float):
            if k in ("min_gap_pct", "rs_spy_min", "risk_per_trade"):
                v_str = f"{v:.2%}"
            else:
                v_str = f"{v:.2f}"
        else:
            v_str = str(v)
        param_items.append(
            f'<span class="p"><span class="k">{k}</span><span class="v">{v_str}</span></span>'
        )
    params_html = "".join(param_items)

    # Tiny JS to set the active tab class based on URL hash
    activate_js = """
    <script>
      (function() {
        function setActive() {
          var hash = (location.hash || '#overview').slice(1);
          document.querySelectorAll('.tab-link').forEach(function(a) {
            a.classList.toggle('active', a.dataset.tab === hash);
          });
        }
        setActive();
        window.addEventListener('hashchange', setActive);
      })();
    </script>
    """

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Trading Bot — {mode}</title>
<style>{CSS}</style>
</head><body>

{hero_html}

<div class="tabs">{_tab_links("overview")}</div>

<section id="overview" class="tab-content">
  <div class="row">
    <div class="panel">
      <div class="panel-header">Account Summary</div>
      <div class="panel-body">{metrics_html}</div>
    </div>
  </div>
  <div class="row row-2col">
    <div class="panel">
      <div class="panel-header">Positions <span class="meta">{len(positions)} open</span></div>
      <div class="panel-body">{positions_html}</div>
    </div>
    <div class="panel">
      <div class="panel-header">Today's Trades <span class="meta">{len(todays_trades)} total</span></div>
      <div class="panel-body">{trades_html}</div>
    </div>
  </div>
</section>

<section id="positions" class="tab-content">
  <div class="panel">
    <div class="panel-header">Open Positions <span class="meta">{len(positions)} active</span></div>
    <div class="panel-body">{positions_html}</div>
  </div>
</section>

<section id="orders" class="tab-content">
  <div class="panel">
    <div class="panel-header">Working Exit Orders <span class="meta">{len(open_orders)} active · TP1 / TP2 / Trailing</span></div>
    <div class="panel-body">{orders_html}</div>
  </div>
</section>

<section id="trades" class="tab-content">
  <div class="panel">
    <div class="panel-header">Today's Trades <span class="meta">{len(todays_trades)} total</span></div>
    <div class="panel-body">{trades_html}</div>
  </div>
</section>

<section id="analytics" class="tab-content">
  <div class="panel">
    <div class="panel-header">Last 7 Days · Performance</div>
    <div class="panel-body">{week_html}</div>
  </div>
</section>

<section id="params" class="tab-content">
  <div class="panel">
    <div class="panel-header">Strategy Parameters <span class="meta">live values from params.json</span></div>
    <div class="panel-body params">{params_html}</div>
  </div>
</section>

<div class="footer">Auto-refresh every 10s · {now_str}</div>

{activate_js}

</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        return render()
    except Exception as e:
        log.error(f"Dashboard render failed: {e}", exc_info=True)
        return HTMLResponse(
            f"<body style='background:#000;color:#ff3030;font-family:monospace;padding:20px'>Dashboard error: {e}</body>",
            status_code=500,
        )


@app.get("/api/status")
def api_status():
    try:
        account = broker.get_account()
        positions = list(broker.trading.get_all_positions())
    except Exception as e:
        return {"error": str(e)}
    trades = _load_trades()
    today_iso = _today_iso()
    return {
        "mode": "PAPER" if config.PAPER_TRADING else "LIVE",
        "timestamp": datetime.now(config.ET).isoformat(),
        "portfolio_value": float(account.portfolio_value),
        "day_pnl": float(account.portfolio_value) - float(account.last_equity or account.portfolio_value),
        "cash": float(account.cash),
        "positions": [
            {
                "symbol": p.symbol,
                "qty": int(p.qty),
                "entry": float(p.avg_entry_price),
                "current": float(p.current_price or 0),
                "unrealized_pl": float(p.unrealized_pl or 0),
            }
            for p in positions
        ],
        "trades_today": [t for t in trades if t.get("date") == today_iso],
        "params": config.load_params(),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
