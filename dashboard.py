"""
Live visual dashboard for the trading bot.

Runs as a separate Railway service (web). Reads state from Alpaca and
the shared data volume — never touches bot state directly. Auto-refreshes
every 10 seconds.

Shows:
  - Portfolio value + day P&L
  - Open positions with live prices
  - Open exit orders (TP1/TP2/trailing)
  - Today's completed trades
  - Last 7 days performance
  - Current strategy parameters
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
* { box-sizing: border-box; }
body {
  margin: 0; padding: 20px;
  background: #0d1117;
  color: #e6edf3;
  font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  font-size: 14px;
  line-height: 1.5;
}
h1 { margin: 0 0 4px 0; font-size: 22px; }
h2 {
  margin: 24px 0 8px 0; font-size: 14px;
  color: #7d8590; text-transform: uppercase; letter-spacing: 1px;
  border-bottom: 1px solid #30363d; padding-bottom: 6px;
}
.sub { color: #7d8590; font-size: 12px; margin-bottom: 24px; }
.pos { color: #3fb950; }
.neg { color: #f85149; }
.neu { color: #e6edf3; }
.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 6px;
  padding: 16px;
  margin-bottom: 16px;
}
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
.metric { padding: 8px; }
.metric .label { color: #7d8590; font-size: 11px; text-transform: uppercase; }
.metric .value { font-size: 20px; font-weight: 600; margin-top: 2px; }
table { width: 100%; border-collapse: collapse; margin-top: 4px; }
th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid #21262d; }
th { color: #7d8590; font-weight: 500; font-size: 11px; text-transform: uppercase; }
tr:last-child td { border-bottom: none; }
.mode-paper { background: #1f6feb; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.mode-live { background: #da3633; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.empty { color: #7d8590; font-style: italic; padding: 10px 0; }
.param { display: inline-block; margin-right: 16px; margin-bottom: 4px; }
.param .k { color: #7d8590; }
"""


def render() -> str:
    try:
        account = broker.get_account()
        portfolio_value = float(account.portfolio_value)
        last_equity = float(account.last_equity or portfolio_value)
        cash = float(account.cash)
        day_pnl = portfolio_value - last_equity
        day_pnl_pct = (day_pnl / last_equity * 100) if last_equity else 0
    except Exception as e:
        return f"<body style='background:#0d1117;color:#f85149;font-family:monospace;padding:20px'>Alpaca error: {e}</body>"

    # Positions
    try:
        positions = list(broker.trading.get_all_positions())
    except Exception:
        positions = []

    # Open orders
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus
        open_orders = list(broker.trading.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=50)
        ))
    except Exception:
        open_orders = []

    # Trade history
    all_trades = _load_trades()
    today_iso = _today_iso()
    todays_trades = [t for t in all_trades if t.get("date") == today_iso]
    week_ago = (datetime.now(config.ET) - timedelta(days=7)).date().isoformat()
    week_trades = [t for t in all_trades if t.get("date", "") >= week_ago and t.get("pnl") is not None]

    params = config.load_params()
    now_str = datetime.now(config.ET).strftime("%Y-%m-%d %H:%M:%S ET")

    # --- Portfolio card ---
    mode = "PAPER" if config.PAPER_TRADING else "LIVE"
    mode_class = "mode-paper" if config.PAPER_TRADING else "mode-live"
    pnl_class = _colour(day_pnl)

    portfolio_html = f"""
    <div class="card">
      <div class="grid">
        <div class="metric"><div class="label">Portfolio</div><div class="value">{_fmt_money(portfolio_value)}</div></div>
        <div class="metric"><div class="label">Day P&amp;L</div><div class="value {pnl_class}">{_fmt_money(day_pnl, plus=True)} ({_fmt_pct(day_pnl_pct)})</div></div>
        <div class="metric"><div class="label">Cash</div><div class="value">{_fmt_money(cash)}</div></div>
        <div class="metric"><div class="label">Budget Cap</div><div class="value">{_fmt_money(config.MAX_CAPITAL)}</div></div>
        <div class="metric"><div class="label">Start of Day</div><div class="value">{_fmt_money(last_equity)}</div></div>
        <div class="metric"><div class="label">Positions</div><div class="value">{len(positions)} / {params['max_positions']}</div></div>
      </div>
    </div>
    """

    # --- Open positions table ---
    if positions:
        rows = []
        for p in positions:
            upl = float(p.unrealized_pl or 0)
            upl_pct = float(p.unrealized_plpc or 0) * 100
            cls = _colour(upl)
            rows.append(f"""
              <tr>
                <td><strong>{p.symbol}</strong></td>
                <td>{p.qty}</td>
                <td>{_fmt_money(float(p.avg_entry_price))}</td>
                <td>{_fmt_money(float(p.current_price or 0))}</td>
                <td>{_fmt_money(float(p.market_value or 0))}</td>
                <td class="{cls}">{_fmt_money(upl, plus=True)} ({_fmt_pct(upl_pct)})</td>
              </tr>""")
        positions_html = f"""
        <table>
          <thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th><th>Mkt Value</th><th>Unrealized P&amp;L</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        positions_html = '<div class="empty">No open positions.</div>'

    # --- Open orders table (exit structure) ---
    if open_orders:
        rows = []
        for o in open_orders:
            side = str(o.side).replace("OrderSide.", "")
            otype = str(getattr(o, "type", "")).replace("OrderType.", "").lower()
            price_str = ""
            if "limit" in otype and getattr(o, "limit_price", None):
                price_str = f"@ {_fmt_money(float(o.limit_price))}"
            elif "trail" in otype and getattr(o, "trail_percent", None):
                price_str = f"trail {float(o.trail_percent)}%"
            rows.append(f"<tr><td><strong>{o.symbol}</strong></td><td>{side}</td><td>{o.qty}</td><td>{otype}</td><td>{price_str}</td></tr>")
        orders_html = f"""
        <table>
          <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Type</th><th>Details</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        orders_html = '<div class="empty">No open orders.</div>'

    # --- Today's trades ---
    if todays_trades:
        rows = []
        for t in todays_trades:
            pnl = t.get("pnl")
            if pnl is None:
                pnl_str = '<span class="neu">open</span>'
                exit_str = "—"
            else:
                pnl_str = f'<span class="{_colour(pnl)}">{_fmt_money(pnl, plus=True)}</span>'
                exit_str = _fmt_money(t.get("exit_price") or 0)
            rows.append(f"""
              <tr>
                <td><strong>{t['symbol']}</strong></td>
                <td>{_fmt_money(t.get('fill_price', 0))}</td>
                <td>{exit_str}</td>
                <td>{t.get('qty')}</td>
                <td>{pnl_str}</td>
                <td>{t.get('exit_reason') or '—'}</td>
                <td>{(t.get('gap_pct') or 0)*100:+.2f}%</td>
                <td>{t.get('sentiment_score', 0):+.2f}</td>
              </tr>""")
        trades_html = f"""
        <table>
          <thead><tr><th>Symbol</th><th>Entry</th><th>Exit</th><th>Qty</th><th>P&amp;L</th><th>Exit Reason</th><th>Gap</th><th>Sent</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>"""
    else:
        trades_html = '<div class="empty">No trades yet today.</div>'

    # --- Last 7 days performance ---
    if week_trades:
        wins = [t for t in week_trades if t["pnl"] > 0]
        losses = [t for t in week_trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(week_trades) * 100
        total_pnl = sum(t["pnl"] for t in week_trades)
        pf = (sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses))) if losses else float("inf")
        week_html = f"""
        <div class="grid">
          <div class="metric"><div class="label">Trades (7d)</div><div class="value">{len(week_trades)}</div></div>
          <div class="metric"><div class="label">Win Rate</div><div class="value">{win_rate:.0f}%</div></div>
          <div class="metric"><div class="label">Profit Factor</div><div class="value">{pf:.2f}</div></div>
          <div class="metric"><div class="label">Total P&amp;L</div><div class="value {_colour(total_pnl)}">{_fmt_money(total_pnl, plus=True)}</div></div>
        </div>"""
    else:
        week_html = '<div class="empty">No completed trades in the last 7 days.</div>'

    # --- Params ---
    param_items = []
    for k in ["opening_range_minutes", "min_gap_pct", "min_volume_mult", "trail_percent",
              "risk_per_trade", "max_positions", "rs_spy_min", "min_sentiment_score",
              "take_profit_mult", "tp1_R_mult", "position_size_mult"]:
        if k in params:
            v = params[k]
            if isinstance(v, float):
                if k in ("min_gap_pct", "rs_spy_min"):
                    v_str = f"{v:.1%}"
                elif k in ("risk_per_trade",):
                    v_str = f"{v:.1%}"
                else:
                    v_str = f"{v:.2f}"
            else:
                v_str = str(v)
            param_items.append(f'<span class="param"><span class="k">{k}:</span> {v_str}</span>')
    params_html = " ".join(param_items)

    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10">
<title>Trading Bot — {mode}</title>
<style>{CSS}</style>
</head><body>
<h1>Trading Bot <span class="{mode_class}">{mode}</span></h1>
<div class="sub">Last updated: {now_str} · auto-refresh every 10s</div>

<h2>Portfolio</h2>
{portfolio_html}

<h2>Open Positions</h2>
<div class="card">{positions_html}</div>

<h2>Open Exit Orders</h2>
<div class="card">{orders_html}</div>

<h2>Today's Trades</h2>
<div class="card">{trades_html}</div>

<h2>Last 7 Days</h2>
<div class="card">{week_html}</div>

<h2>Strategy Parameters</h2>
<div class="card">{params_html}</div>

</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        return render()
    except Exception as e:
        log.error(f"Dashboard render failed: {e}", exc_info=True)
        return HTMLResponse(
            f"<body style='background:#0d1117;color:#f85149;font-family:monospace;padding:20px'>Dashboard error: {e}</body>",
            status_code=500,
        )


@app.get("/api/status")
def api_status():
    """JSON snapshot — useful for mobile/scripts."""
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
