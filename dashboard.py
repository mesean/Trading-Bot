"""
Live visual dashboard for the trading bot — Thinkorswim-inspired layout.

Runs as a separate Railway service (web). Reads state from Alpaca and
the shared data volume — never touches bot state directly. Auto-refreshes
every 10 seconds.

Shows:
  - Account summary strip (portfolio, P&L, cash, budget)
  - Open positions with live prices + unrealized P&L
  - Working orders (TP1 / TP2 / trailing stops)
  - Today's completed trades
  - 7-day rolling performance
  - Current strategy parameters
  - Market status indicator
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
}

/* Top bar */
.topbar {
  background: linear-gradient(180deg, #1a1a1a 0%, #0e0e0e 100%);
  border-bottom: 2px solid #00a3e0;
  padding: 10px 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}
.topbar .title {
  color: #00a3e0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 3px;
  font-family: 'Segoe UI', Tahoma, sans-serif;
}
.topbar .mode-paper {
  background: #00a3e0; color: #000;
  padding: 3px 10px; font-weight: 700; font-size: 10px;
  letter-spacing: 2px;
}
.topbar .mode-live {
  background: #ff3030; color: #fff;
  padding: 3px 10px; font-weight: 700; font-size: 10px;
  letter-spacing: 2px;
}
.topbar .timestamp { color: #888; font-size: 11px; font-family: 'Consolas', monospace; }

/* Market status */
.status {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 11px; color: #ccc; letter-spacing: 1px;
}
.dot { width: 8px; height: 8px; border-radius: 50%; }
.dot-open { background: #00ff7f; box-shadow: 0 0 8px rgba(0,255,127,0.6); }
.dot-closed { background: #666; }

/* Panels */
.row { display: grid; gap: 6px; padding: 6px; }
.row-2col { grid-template-columns: 1fr 1fr; }
@media (max-width: 900px) { .row-2col { grid-template-columns: 1fr; } }

.panel {
  background: #0a0a0a;
  border: 1px solid #2a2a2a;
}
.panel-header {
  background: #141414;
  color: #00a3e0;
  padding: 5px 12px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 2px;
  border-bottom: 1px solid #2a2a2a;
  text-transform: uppercase;
  display: flex; justify-content: space-between; align-items: center;
}
.panel-header .meta { color: #666; font-weight: 400; letter-spacing: 1px; font-size: 10px; }
.panel-body { padding: 0; }
.panel-body.padded { padding: 10px; }

/* Account metrics strip */
.metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0;
}
.metric {
  border-right: 1px solid #2a2a2a;
  padding: 10px 14px;
}
.metric:last-child { border-right: none; }
.metric .label {
  color: #888; font-size: 9px; text-transform: uppercase;
  letter-spacing: 2px; margin-bottom: 3px; font-weight: 600;
}
.metric .value {
  font-size: 20px; font-weight: 600; color: #fff;
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
  padding: 5px 10px; font-weight: 700; font-size: 10px;
  text-transform: uppercase; letter-spacing: 1px;
  border-bottom: 1px solid #2a2a2a;
}
td {
  padding: 5px 10px; font-size: 12px;
  border-bottom: 1px solid #151515;
}
tbody tr:hover { background: #101010; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.sym { color: #ffd966; font-weight: 700; letter-spacing: 0.5px; }

/* Colors */
.pos { color: #00ff7f; }
.neg { color: #ff3030; }
.neu { color: #cfcfcf; }
.warn { color: #ffaa00; }
.muted { color: #666; }

.empty { color: #666; padding: 16px; font-style: italic; text-align: center; font-size: 11px; }

/* Parameters */
.params { padding: 10px 14px; line-height: 1.9; }
.params .p {
  display: inline-block;
  margin-right: 14px;
  padding: 2px 8px;
  background: #101010;
  border-left: 2px solid #00a3e0;
  font-family: 'Consolas', monospace;
  font-size: 11px;
}
.params .p .k { color: #888; text-transform: uppercase; font-size: 9px; letter-spacing: 1px; }
.params .p .v { color: #fff; margin-left: 4px; }

/* Pill badges for exit reasons */
.pill {
  display: inline-block;
  padding: 1px 6px;
  background: #1a1a1a;
  border: 1px solid #333;
  font-size: 10px;
  letter-spacing: 0.5px;
}
"""


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
        return f"<body style='background:#000;color:#ff3030;font-family:monospace;padding:20px'>Alpaca error: {e}</body>"

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

    # Market status
    market_status = "CLOSED"
    market_dot = "dot-closed"
    try:
        clock = broker.get_clock()
        if clock.is_open:
            market_status = "OPEN"
            market_dot = "dot-open"
    except Exception:
        pass

    # Trades
    all_trades = _load_trades()
    today_iso = _today_iso()
    todays_trades = [t for t in all_trades if t.get("date") == today_iso]
    week_ago = (datetime.now(config.ET) - timedelta(days=7)).date().isoformat()
    week_trades = [t for t in all_trades if t.get("date", "") >= week_ago and t.get("pnl") is not None]

    params = config.load_params()
    now_str = datetime.now(config.ET).strftime("%Y-%m-%d  %H:%M:%S ET")
    mode = "PAPER" if config.PAPER_TRADING else "LIVE"
    mode_class = "mode-paper" if config.PAPER_TRADING else "mode-live"
    pnl_class = _colour(day_pnl)

    # --- Account metrics ---
    metrics_html = f"""
    <div class="metrics">
      <div class="metric">
        <div class="label">Portfolio Value</div>
        <div class="value">{_fmt_money(portfolio_value)}</div>
      </div>
      <div class="metric">
        <div class="label">Day P&amp;L</div>
        <div class="value {pnl_class}">{_fmt_money(day_pnl, plus=True)}</div>
        <div class="sub {pnl_class}">{_fmt_pct(day_pnl_pct)}</div>
      </div>
      <div class="metric">
        <div class="label">Cash</div>
        <div class="value">{_fmt_money(cash)}</div>
      </div>
      <div class="metric">
        <div class="label">Buying Power</div>
        <div class="value">{_fmt_money(buying_power)}</div>
      </div>
      <div class="metric">
        <div class="label">Budget Cap</div>
        <div class="value">{_fmt_money(config.MAX_CAPITAL)}</div>
      </div>
      <div class="metric">
        <div class="label">Positions</div>
        <div class="value">{len(positions)} <span class="muted" style="font-size:14px">/ {params['max_positions']}</span></div>
      </div>
    </div>
    """

    # --- Positions ---
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
        week_html = f"""
        <div class="metrics">
          <div class="metric"><div class="label">Trades</div><div class="value">{len(week_trades)}</div></div>
          <div class="metric"><div class="label">Win Rate</div><div class="value">{win_rate:.0f}%</div></div>
          <div class="metric"><div class="label">Profit Factor</div><div class="value">{pf_str}</div></div>
          <div class="metric"><div class="label">Total P&amp;L</div><div class="value {_colour(total_pnl)}">{_fmt_money(total_pnl, plus=True)}</div></div>
        </div>"""
    else:
        week_html = '<div class="empty">No completed trades in the last 7 days</div>'

    # --- Parameters ---
    param_keys = [
        "opening_range_minutes", "min_gap_pct", "min_volume_mult", "trail_percent",
        "risk_per_trade", "max_positions", "rs_spy_min", "min_sentiment_score",
        "take_profit_mult", "tp1_R_mult", "position_size_mult",
    ]
    param_items = []
    for k in param_keys:
        if k not in params:
            continue
        v = params[k]
        if isinstance(v, float):
            if k in ("min_gap_pct", "rs_spy_min", "risk_per_trade"):
                v_str = f"{v:.2%}"
            else:
                v_str = f"{v:.2f}"
        else:
            v_str = str(v)
        param_items.append(f'<span class="p"><span class="k">{k}</span><span class="v">{v_str}</span></span>')
    params_html = "".join(param_items)

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Trading Bot — {mode}</title>
<style>{CSS}</style>
</head><body>

<div class="topbar">
  <div class="title">◆ TRADING BOT</div>
  <div class="status">
    <span class="dot {market_dot}"></span>MARKET {market_status}
  </div>
  <div><span class="{mode_class}">{mode}</span></div>
  <div class="timestamp">{now_str}</div>
</div>

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
    <div class="panel-header">Working Orders <span class="meta">{len(open_orders)} active</span></div>
    <div class="panel-body">{orders_html}</div>
  </div>
</div>

<div class="row">
  <div class="panel">
    <div class="panel-header">Today's Trades <span class="meta">{len(todays_trades)} total</span></div>
    <div class="panel-body">{trades_html}</div>
  </div>
</div>

<div class="row row-2col">
  <div class="panel">
    <div class="panel-header">Last 7 Days</div>
    <div class="panel-body">{week_html}</div>
  </div>
  <div class="panel">
    <div class="panel-header">Strategy Parameters</div>
    <div class="panel-body params">{params_html}</div>
  </div>
</div>

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
