"""
Live visual dashboard for the trading bot — modern SaaS layout with
deep navy palette, glowing card panels, top horizontal navigation,
and the animated bot mascot embedded as a status card.

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
:root {
  --bg-deep:       #060d24;
  --bg-base:       #0a1432;
  --card-grad-top: #182849;
  --card-grad-bot: #0e1a3a;
  --card-border:   #2a3d6b;
  --card-border-hover: #4a6bb0;
  --accent:        #00b8ff;
  --accent-soft:   rgba(0, 184, 255, 0.18);
  --accent-glow:   rgba(0, 184, 255, 0.35);
  --gain:          #00e676;
  --loss:          #ff5252;
  --warn:          #ffb74d;
  --gold:          #ffd166;
  --text:          #e8eef9;
  --text-muted:    #7a8aa8;
  --text-dim:      #4f5d7a;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  background: var(--bg-deep);
  color: var(--text);
  min-height: 100vh;
}
body {
  font-family: 'Inter', 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 13px;
  line-height: 1.5;
  background:
    radial-gradient(ellipse 80% 60% at 15% 0%, rgba(0, 184, 255, 0.12) 0%, transparent 60%),
    radial-gradient(ellipse 60% 50% at 85% 100%, rgba(0, 184, 255, 0.08) 0%, transparent 60%),
    linear-gradient(180deg, var(--bg-base) 0%, var(--bg-deep) 100%);
  background-attachment: fixed;
  position: relative;
  overflow-x: hidden;
}

/* Decorative blue light streaks in the background */
body::before, body::after {
  content: '';
  position: fixed;
  pointer-events: none;
  z-index: 0;
  background: linear-gradient(135deg, transparent 45%, rgba(0, 184, 255, 0.18) 50%, transparent 55%);
  filter: blur(2px);
}
body::before {
  top: -10%; left: -10%; width: 50%; height: 50%;
  transform: rotate(15deg);
}
body::after {
  bottom: -10%; right: -10%; width: 50%; height: 50%;
  transform: rotate(15deg);
  opacity: 0.7;
}

/* ============== TOP NAVIGATION ============== */
.topnav {
  position: relative; z-index: 10;
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 28px;
  background: linear-gradient(180deg, rgba(15, 27, 60, 0.95) 0%, rgba(10, 20, 50, 0.85) 100%);
  border-bottom: 1px solid var(--card-border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}
.logo {
  display: flex; align-items: center; gap: 10px;
  color: var(--accent);
  font-weight: 700;
  letter-spacing: 2px;
  font-size: 16px;
}
.logo-icon {
  font-size: 22px;
  text-shadow: 0 0 12px var(--accent-glow);
  animation: logo-pulse 3s ease-in-out infinite;
}
@keyframes logo-pulse {
  0%, 100% { text-shadow: 0 0 12px var(--accent-glow); }
  50%      { text-shadow: 0 0 20px var(--accent), 0 0 30px var(--accent-glow); }
}
.nav-items {
  display: flex; gap: 4px;
  background: rgba(0, 0, 0, 0.25);
  border: 1px solid var(--card-border);
  border-radius: 10px;
  padding: 4px;
}
.nav-item {
  color: var(--text-muted);
  text-decoration: none;
  padding: 8px 18px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.5px;
  border-radius: 7px;
  transition: all 0.2s ease;
  white-space: nowrap;
}
.nav-item:hover { color: var(--text); background: rgba(255,255,255,0.04); }
.nav-item.active {
  color: #fff;
  background: linear-gradient(180deg, var(--accent) 0%, #0094d4 100%);
  box-shadow: 0 0 12px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2);
}
.user-info {
  display: flex; align-items: center; gap: 14px;
}
.market-pill {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 14px;
  background: rgba(0, 0, 0, 0.3);
  border: 1px solid var(--card-border);
  border-radius: 20px;
  font-size: 11px; letter-spacing: 1.5px;
  color: var(--text);
}
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-open   { background: var(--gain); box-shadow: 0 0 10px var(--gain); animation: pulse-dot 1.6s ease-in-out infinite; }
.dot-closed { background: var(--text-dim); }
@keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.4} }
.mode-badge {
  padding: 6px 14px; border-radius: 6px;
  font-size: 10px; font-weight: 800; letter-spacing: 2px;
}
.mode-badge.paper { background: var(--accent); color: #001528; box-shadow: 0 0 10px var(--accent-glow); }
.mode-badge.live  { background: var(--loss); color: #fff; box-shadow: 0 0 10px rgba(255,82,82,0.5); }
.timestamp { color: var(--text-dim); font-size: 11px; font-family: 'Consolas', monospace; }

/* ============== MAIN CONTENT ============== */
.main { position: relative; z-index: 1; padding: 20px 28px 28px; max-width: 1500px; margin: 0 auto; }

.page-title {
  font-size: 22px; font-weight: 700; color: #fff;
  margin-bottom: 4px; letter-spacing: 0.5px;
}
.page-sub {
  color: var(--text-muted); font-size: 12px; margin-bottom: 22px;
}

/* ============== TAB CONTENT (CSS-only via :target) ============== */
.tab-content { display: none; }
.tab-content:target { display: block; animation: tab-fade 0.35s ease; }
body:not(:has(:target)) #dashboard { display: block; }
@keyframes tab-fade {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ============== CARDS ============== */
.card {
  position: relative;
  background: linear-gradient(180deg, var(--card-grad-top) 0%, var(--card-grad-bot) 100%);
  border: 1px solid var(--card-border);
  border-radius: 12px;
  padding: 18px 20px;
  transition: all 0.25s ease;
  overflow: hidden;
}
.card::before {
  content: '';
  position: absolute;
  top: 0; left: 10%; right: 10%; height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-glow), transparent);
}
.card:hover {
  border-color: var(--card-border-hover);
  transform: translateY(-1px);
  box-shadow: 0 12px 32px rgba(0, 184, 255, 0.08);
}
.card-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 14px;
}
.card-title {
  font-size: 11px; font-weight: 700;
  color: var(--text-muted); text-transform: uppercase; letter-spacing: 2px;
}
.card-meta { color: var(--text-dim); font-size: 11px; }

/* Grid */
.grid { display: grid; gap: 16px; margin-bottom: 16px; }
.grid-4 { grid-template-columns: 280px 1fr 1fr 1fr; }
.grid-2 { grid-template-columns: 1fr 1fr; }
.grid-3 { grid-template-columns: 1fr 1fr 1fr; }
@media (max-width: 1100px) {
  .grid-4 { grid-template-columns: 1fr 1fr; }
  .grid-3 { grid-template-columns: 1fr; }
}
@media (max-width: 700px) {
  .grid-4, .grid-2 { grid-template-columns: 1fr; }
  .topnav { flex-wrap: wrap; gap: 12px; padding: 12px 16px; }
  .nav-items { width: 100%; overflow-x: auto; }
  .main { padding: 16px; }
}

/* ============== BOT CARD ============== */
.bot-card {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 12px;
  background:
    radial-gradient(circle at 50% 30%, rgba(0, 184, 255, 0.18) 0%, transparent 60%),
    linear-gradient(180deg, var(--card-grad-top) 0%, var(--card-grad-bot) 100%);
  min-height: 230px;
}
.bot-mascot-wrap {
  position: relative;
  width: 130px; height: 150px;
  display: flex; justify-content: center; align-items: center;
  animation: bob 3.5s ease-in-out infinite;
}
@keyframes bob {
  0%, 100% { transform: translateY(0); }
  50%      { transform: translateY(-6px); }
}
.bot-pulse {
  position: absolute;
  width: 140px; height: 140px;
  border-radius: 50%;
  border: 2px solid var(--gain);
  animation: pulse-ring 2.4s ease-out infinite;
  opacity: 0;
}
.bot-pulse.market-closed {
  border-color: var(--text-dim);
  animation: none;
  opacity: 0.25;
}
@keyframes pulse-ring {
  0%   { transform: scale(0.55); opacity: 0; }
  30%  { opacity: 0.55; }
  100% { transform: scale(1.4); opacity: 0; }
}
.bot { width: 110px; height: 130px; }
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
.bot .antenna-light { animation: antenna-pulse 1.4s ease-in-out infinite; }
@keyframes antenna-pulse {
  0%, 100% { opacity: 1; r: 4; }
  50%      { opacity: 0.4; r: 5; }
}
.bot-status-label {
  font-size: 9px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 2px; margin-top: 4px;
}
.bot-status-value {
  font-size: 14px; font-weight: 700; letter-spacing: 1.5px;
  color: var(--gain);
}
.bot-status-value.idle { color: var(--text-dim); }

/* ============== METRIC CARDS ============== */
.metric-card { padding: 18px 20px; }
.metric-label {
  font-size: 11px; color: var(--text-muted);
  text-transform: uppercase; letter-spacing: 2px; font-weight: 600;
  margin-bottom: 10px;
}
.metric-value {
  font-size: 28px; font-weight: 700; color: #fff;
  font-family: 'Consolas', 'Courier New', monospace;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.metric-sub {
  font-size: 12px; margin-top: 6px;
  color: var(--text-muted);
  font-family: 'Consolas', monospace;
}
.metric-icon {
  float: right; font-size: 18px; opacity: 0.5;
}

/* Colors */
.pos { color: var(--gain); }
.neg { color: var(--loss); }
.neu { color: var(--text); }
.warn { color: var(--warn); }
.muted { color: var(--text-muted); }
.gold  { color: var(--gold); }

/* ============== TABLES ============== */
.table-wrap { overflow-x: auto; margin: -4px -8px; padding: 4px 8px; }
table { width: 100%; border-collapse: collapse; font-family: 'Consolas','Courier New',monospace; }
thead th {
  color: var(--text-muted); text-align: left;
  padding: 10px 12px; font-weight: 600; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1.5px;
  border-bottom: 1px solid var(--card-border);
}
tbody td {
  padding: 10px 12px; font-size: 12px;
  border-bottom: 1px solid rgba(42, 61, 107, 0.3);
}
tbody tr { transition: background 0.15s ease; }
tbody tr:hover { background: rgba(0, 184, 255, 0.05); }
tbody tr:last-child td { border-bottom: none; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.sym {
  color: var(--gold); font-weight: 700; letter-spacing: 0.5px;
}
tbody tr:hover .sym { text-shadow: 0 0 8px rgba(255, 209, 102, 0.5); }

.empty {
  color: var(--text-dim); padding: 32px 16px;
  text-align: center; font-size: 12px; font-style: italic;
}

/* ============== PARAMETER PILLS ============== */
.params-grid {
  display: grid; gap: 8px;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
}
.param-pill {
  display: flex; justify-content: space-between; align-items: center;
  padding: 10px 14px;
  background: rgba(0, 0, 0, 0.25);
  border: 1px solid var(--card-border);
  border-left: 3px solid var(--accent);
  border-radius: 6px;
  transition: all 0.2s ease;
}
.param-pill:hover {
  background: rgba(0, 184, 255, 0.06);
  border-left-color: var(--gain);
  transform: translateX(2px);
}
.param-pill .k {
  color: var(--text-muted); font-size: 11px;
  text-transform: uppercase; letter-spacing: 1px;
}
.param-pill .v {
  color: #fff; font-weight: 600;
  font-family: 'Consolas', monospace;
}

/* ============== PILL/BADGES ============== */
.pill {
  display: inline-block;
  padding: 2px 8px;
  background: rgba(0, 184, 255, 0.1);
  border: 1px solid var(--card-border);
  font-size: 10px;
  letter-spacing: 0.5px;
  border-radius: 4px;
  color: var(--text);
}
.pill.warn { background: rgba(255,183,77,0.12); border-color: var(--warn); color: var(--warn); }
.pill.pos  { background: rgba(0,230,118,0.12); border-color: var(--gain); color: var(--gain); }
.pill.neg  { background: rgba(255,82,82,0.12); border-color: var(--loss); color: var(--loss); }

/* ============== FOOTER ============== */
.footer {
  text-align: center;
  padding: 20px 16px;
  color: var(--text-dim);
  font-size: 11px;
  letter-spacing: 1px;
  font-family: 'Consolas', monospace;
}
"""


def _bot_svg(market_open: bool, day_pnl: float) -> str:
    """SVG mascot — eyes blink, antenna pulses, body bobs, mood reflects P&L."""
    if day_pnl > 0:
        eye_color = "#00e676"
        mouth = '<path d="M 35 52 Q 50 60 65 52" stroke="#00e676" stroke-width="2.5" fill="none"/>'
    elif day_pnl < 0:
        eye_color = "#ff5252"
        mouth = '<path d="M 35 56 Q 50 48 65 56" stroke="#ff5252" stroke-width="2.5" fill="none"/>'
    else:
        eye_color = "#00b8ff"
        mouth = '<rect x="36" y="52" width="28" height="3" rx="1" fill="#00b8ff"/>'

    chest_color = "#00e676" if market_open else "#4f5d7a"
    return f"""
    <svg class="bot" viewBox="0 0 100 120">
      <line x1="50" y1="2" x2="50" y2="14" stroke="#00b8ff" stroke-width="2"/>
      <circle class="antenna-light" cx="50" cy="5" r="4" fill="{chest_color}"/>
      <rect x="18" y="14" width="64" height="50" rx="10"
            fill="#0a1432" stroke="#00b8ff" stroke-width="2"/>
      <circle class="eye left"  cx="35" cy="35" r="5.5" fill="{eye_color}"/>
      <circle class="eye right" cx="65" cy="35" r="5.5" fill="{eye_color}"/>
      {mouth}
      <rect x="24" y="64" width="52" height="42" rx="6"
            fill="#0a1432" stroke="#00b8ff" stroke-width="2"/>
      <circle cx="50" cy="85" r="6" fill="{chest_color}" opacity="0.7">
        <animate attributeName="opacity" values="0.4;1;0.4" dur="2s" repeatCount="indefinite"/>
      </circle>
      <rect x="8"  y="68" width="10" height="28" rx="3"
            fill="#0a1432" stroke="#00b8ff" stroke-width="2"/>
      <rect x="82" y="68" width="10" height="28" rx="3"
            fill="#0a1432" stroke="#00b8ff" stroke-width="2"/>
    </svg>
    """


def _nav_links() -> str:
    tabs = [
        ("dashboard",  "Dashboard"),
        ("positions",  "Positions"),
        ("orders",     "Orders"),
        ("trades",     "Trades"),
        ("analytics",  "Analytics"),
        ("settings",   "Settings"),
    ]
    return "".join(
        f'<a href="#{tid}" class="nav-item" data-tab="{tid}">{label}</a>'
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
            "<body style='background:#060d24;color:#ff5252;font-family:monospace;padding:20px'>"
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
    bot_status = "SCANNING" if market_open else "STANDBY"
    bot_status_class = "" if market_open else "idle"

    bot_html = _bot_svg(market_open, day_pnl)

    # --- Top nav ---
    topnav_html = f"""
    <nav class="topnav">
      <div class="logo">
        <span class="logo-icon">◆</span>
        <span class="logo-text">TRADING BOT</span>
      </div>
      <div class="nav-items">{_nav_links()}</div>
      <div class="user-info">
        <div class="market-pill">
          <span class="dot {market_dot}"></span>MARKET {market_status}
        </div>
        <div class="mode-badge {mode_class}">{mode}</div>
        <div class="timestamp">{now_str}</div>
      </div>
    </nav>
    """

    # --- Bot card ---
    bot_card_html = f"""
    <div class="card bot-card">
      <div class="bot-mascot-wrap">
        <div class="bot-pulse {pulse_class}"></div>
        {bot_html}
      </div>
      <div class="bot-status-label">Bot Status</div>
      <div class="bot-status-value {bot_status_class}">{bot_status}</div>
    </div>
    """

    # --- Metric cards ---
    metric_cards_html = f"""
    <div class="card metric-card">
      <div class="metric-label">Day P&amp;L <span class="metric-icon">📊</span></div>
      <div class="metric-value {pnl_class}">{_fmt_money(day_pnl, plus=True)}</div>
      <div class="metric-sub {pnl_class}">{_fmt_pct(day_pnl_pct)} today</div>
    </div>
    <div class="card metric-card">
      <div class="metric-label">Portfolio Value <span class="metric-icon">💼</span></div>
      <div class="metric-value">{_fmt_money(portfolio_value)}</div>
      <div class="metric-sub">Cash: {_fmt_money(cash)}</div>
    </div>
    <div class="card metric-card">
      <div class="metric-label">Positions <span class="metric-icon">⚡</span></div>
      <div class="metric-value">{len(positions)} <span class="muted" style="font-size:18px">/ {params['max_positions']}</span></div>
      <div class="metric-sub">{len(open_orders)} working orders</div>
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
                <td class="num">${entry:.2f}</td>
                <td class="num">${current:.2f}</td>
                <td class="num">{_fmt_money(mkt_val)}</td>
                <td class="num {cls}">{_fmt_money(upl, plus=True)}</td>
                <td class="num {cls}">{_fmt_pct(upl_pct)}</td>
              </tr>""")
        positions_html = f"""<div class="table-wrap"><table>
          <thead><tr>
            <th>Symbol</th><th class="num">Qty</th><th class="num">Entry</th>
            <th class="num">Last</th><th class="num">Mkt Val</th>
            <th class="num">P&amp;L $</th><th class="num">P&amp;L %</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>"""
    else:
        positions_html = '<div class="empty">No open positions yet</div>'

    # --- Working orders ---
    if open_orders:
        rows = []
        for o in open_orders:
            side = str(o.side).replace("OrderSide.", "")
            otype = str(getattr(o, "type", "")).replace("OrderType.", "").lower()
            detail = ""
            if "limit" in otype and getattr(o, "limit_price", None):
                detail = f"${float(o.limit_price):.2f}"
            elif "trail" in otype and getattr(o, "trail_percent", None):
                detail = f"{float(o.trail_percent)}%"
            side_cls = "pos" if side == "BUY" else "neg"
            rows.append(f"""
              <tr>
                <td class="sym">{o.symbol}</td>
                <td><span class="pill {side_cls}">{side}</span></td>
                <td class="num">{o.qty}</td>
                <td><span class="pill">{otype}</span></td>
                <td class="num">{detail}</td>
              </tr>""")
        orders_html = f"""<div class="table-wrap"><table>
          <thead><tr>
            <th>Symbol</th><th>Side</th><th class="num">Qty</th>
            <th>Type</th><th class="num">Price/Trail</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>"""
    else:
        orders_html = '<div class="empty">No working exit orders</div>'

    # --- Today's trades ---
    if todays_trades:
        rows = []
        for t in todays_trades:
            pnl = t.get("pnl")
            if pnl is None:
                pnl_cell = '<span class="pill warn">OPEN</span>'
                exit_cell = '<span class="muted">—</span>'
            else:
                pnl_cell = f'<span class="{_colour(pnl)}">{_fmt_money(pnl, plus=True)}</span>'
                exit_cell = f"${float(t.get('exit_price') or 0):.2f}"
            gap = (t.get("gap_pct") or 0) * 100
            sent = t.get("sentiment_score", 0)
            sent_cls = _colour(sent)
            rows.append(f"""
              <tr>
                <td class="sym">{t['symbol']}</td>
                <td class="num">${float(t.get('fill_price', 0)):.2f}</td>
                <td class="num">{exit_cell}</td>
                <td class="num">{t.get('qty')}</td>
                <td class="num">{pnl_cell}</td>
                <td><span class="pill">{t.get('exit_reason') or '—'}</span></td>
                <td class="num">{gap:+.2f}%</td>
                <td class="num {sent_cls}">{sent:+.2f}</td>
              </tr>""")
        trades_html = f"""<div class="table-wrap"><table>
          <thead><tr>
            <th>Symbol</th><th class="num">Entry</th><th class="num">Exit</th>
            <th class="num">Qty</th><th class="num">P&amp;L</th>
            <th>Exit</th><th class="num">Gap</th><th class="num">Sent</th>
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table></div>"""
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
        <div class="grid grid-3">
          <div class="card metric-card">
            <div class="metric-label">Total Trades</div>
            <div class="metric-value">{len(week_trades)}</div>
            <div class="metric-sub">past 7 days</div>
          </div>
          <div class="card metric-card">
            <div class="metric-label">Win Rate</div>
            <div class="metric-value">{win_rate:.0f}%</div>
            <div class="metric-sub">{len(wins)}W / {len(losses)}L</div>
          </div>
          <div class="card metric-card">
            <div class="metric-label">Profit Factor</div>
            <div class="metric-value gold">{pf_str}</div>
            <div class="metric-sub">gross win / gross loss</div>
          </div>
          <div class="card metric-card">
            <div class="metric-label">Total P&amp;L</div>
            <div class="metric-value {_colour(total_pnl)}">{_fmt_money(total_pnl, plus=True)}</div>
            <div class="metric-sub">net realized</div>
          </div>
          <div class="card metric-card">
            <div class="metric-label">Avg Win</div>
            <div class="metric-value pos">{_fmt_money(avg_win, plus=True)}</div>
            <div class="metric-sub">per winning trade</div>
          </div>
          <div class="card metric-card">
            <div class="metric-label">Avg Loss</div>
            <div class="metric-value neg">{_fmt_money(avg_loss, plus=True)}</div>
            <div class="metric-sub">per losing trade</div>
          </div>
        </div>"""
    else:
        week_html = '<div class="empty card">No completed trades in the last 7 days</div>'

    # --- Parameters ---
    param_keys = [
        "opening_range_minutes", "min_gap_pct", "min_volume_mult", "trail_percent",
        "risk_per_trade", "max_positions", "rs_spy_min", "min_sentiment_score",
        "take_profit_mult", "tp1_R_mult", "position_size_mult",
        "partial_tp_enabled", "sentiment_filter_enabled",
    ]
    param_pills = []
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
        param_pills.append(
            f'<div class="param-pill"><span class="k">{k}</span><span class="v">{v_str}</span></div>'
        )
    params_html = f'<div class="params-grid">{"".join(param_pills)}</div>'

    activate_js = """
    <script>
      (function() {
        function setActive() {
          var hash = (location.hash || '#dashboard').slice(1);
          document.querySelectorAll('.nav-item').forEach(function(a) {
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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body>

{topnav_html}

<main class="main">

  <section id="dashboard" class="tab-content">
    <div class="page-title">My Dashboard</div>
    <div class="page-sub">Live snapshot of bot activity and account performance</div>

    <div class="grid grid-4">
      {bot_card_html}
      {metric_cards_html}
    </div>

    <div class="grid grid-2">
      <div class="card">
        <div class="card-header">
          <div class="card-title">Open Positions</div>
          <div class="card-meta">{len(positions)} active</div>
        </div>
        {positions_html}
      </div>
      <div class="card">
        <div class="card-header">
          <div class="card-title">Today's Trades</div>
          <div class="card-meta">{len(todays_trades)} total</div>
        </div>
        {trades_html}
      </div>
    </div>
  </section>

  <section id="positions" class="tab-content">
    <div class="page-title">Open Positions</div>
    <div class="page-sub">Live unrealized P&amp;L on every position</div>
    <div class="card">
      <div class="card-header">
        <div class="card-title">Active Positions</div>
        <div class="card-meta">{len(positions)} open</div>
      </div>
      {positions_html}
    </div>
  </section>

  <section id="orders" class="tab-content">
    <div class="page-title">Working Orders</div>
    <div class="page-sub">TP1, TP2, and trailing stop orders waiting on the exchange</div>
    <div class="card">
      <div class="card-header">
        <div class="card-title">Exit Orders</div>
        <div class="card-meta">{len(open_orders)} active</div>
      </div>
      {orders_html}
    </div>
  </section>

  <section id="trades" class="tab-content">
    <div class="page-title">Today's Trades</div>
    <div class="page-sub">Every entry and exit fired today, with P&amp;L and reason</div>
    <div class="card">
      <div class="card-header">
        <div class="card-title">Trade Log</div>
        <div class="card-meta">{len(todays_trades)} total</div>
      </div>
      {trades_html}
    </div>
  </section>

  <section id="analytics" class="tab-content">
    <div class="page-title">Performance Analytics</div>
    <div class="page-sub">Last 7 days of completed trades</div>
    {week_html}
  </section>

  <section id="settings" class="tab-content">
    <div class="page-title">Strategy Parameters</div>
    <div class="page-sub">Live values from params.json — auto-tuned by Claude every evening</div>
    <div class="card">
      <div class="card-header">
        <div class="card-title">Active Configuration</div>
        <div class="card-meta">{len(param_pills)} parameters</div>
      </div>
      {params_html}
    </div>
  </section>

</main>

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
            f"<body style='background:#060d24;color:#ff5252;font-family:monospace;padding:20px'>Dashboard error: {e}</body>",
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
