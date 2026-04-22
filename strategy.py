"""
Opening Range Breakout (ORB) Strategy
--------------------------------------
1. Pre-market scan (9:15am ET):
   - Fetch last 30 days of daily bars for the watchlist
   - Calculate 20-day avg volume and previous close for each stock

2. Opening range (9:30 to 9:30+N min ET):
   - Track the first N-minute high/low for all candidates

3. Entry (after opening range, until 3:45pm):
   - Trigger: 1-min bar closes ABOVE the ORB high
   - Filters: gap up >= min_gap_pct, volume pace >= min_volume_mult * avg
   - Bracket order: stop below ORB low, target 2x the stop distance
   - Max max_positions concurrent positions

4. EOD: all positions are closed at 3:45pm by main.py
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd
from alpaca.data.timeframe import TimeFrame

import config
from broker import Broker

log = logging.getLogger(__name__)


class ORBStrategy:
    def __init__(self, broker: Broker):
        self.broker = broker
        self.params: dict = config.load_params()
        self.candidates: Dict[str, dict] = {}    # symbol → {prev_close, avg_volume}
        self.opening_ranges: Dict[str, dict] = {}  # symbol → {high, low, open, ...}
        self.trades_today: List[dict] = []
        self.scan_done: bool = False
        self.eod_close_done: bool = False
        self.brief_done: bool = False

    def reset_day(self):
        self.params = config.load_params()
        self.candidates = {}
        self.opening_ranges = {}
        self.trades_today = []
        self.scan_done = False
        self.eod_close_done = False
        self.brief_done = False
        log.info("=== New trading day — params reloaded ===")

    def update_regime(self, regime: dict):
        """Apply market-regime multiplier to position sizes."""
        mult = regime.get("position_size_mult", 1.0)
        self.params["position_size_mult"] = mult
        if mult < 1.0:
            log.info(f"High-vol regime detected — position sizes scaled to {mult:.0%}")

    # ------------------------------------------------------------------
    # Phase 1: pre-market scan
    # ------------------------------------------------------------------
    def pre_market_scan(self):
        log.info("Pre-market scan ...")
        now = datetime.now(config.ET)
        start = now - timedelta(days=35)
        end = now - timedelta(days=1)

        bars = self.broker.get_bars(config.WATCHLIST, TimeFrame.Day, start=start, end=end)
        if bars is None:
            log.warning("No daily bar data — using all watchlist stocks as candidates")
            for sym in config.WATCHLIST:
                self.candidates[sym] = {"prev_close": None, "avg_volume": 1_000_000}
            self.scan_done = True
            return

        for symbol in config.WATCHLIST:
            sym_bars = self.broker.extract_symbol_bars(bars, symbol)
            if sym_bars is None or len(sym_bars) < 5:
                continue
            avg_vol = sym_bars["volume"].rolling(20, min_periods=5).mean().iloc[-1]
            if avg_vol < 500_000:
                continue
            self.candidates[symbol] = {
                "prev_close": float(sym_bars["close"].iloc[-1]),
                "avg_volume": float(avg_vol),
            }

        log.info(f"Scan complete — {len(self.candidates)} liquid candidates")
        self.scan_done = True

    # ------------------------------------------------------------------
    # Phase 2: opening range (called every minute during OR window)
    # ------------------------------------------------------------------
    def update_opening_ranges(self):
        if not self.candidates:
            return
        now = datetime.now(config.ET)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        symbols = list(self.candidates.keys())

        bars = self.broker.get_bars(symbols, TimeFrame.Minute, start=market_open, end=now)
        if bars is None:
            return

        for symbol in symbols:
            sym_bars = self.broker.extract_symbol_bars(bars, symbol)
            if sym_bars is None or sym_bars.empty:
                continue
            cand = self.candidates[symbol]
            open_price = float(sym_bars["open"].iloc[0])
            prev_close = cand["prev_close"] or open_price
            gap_pct = (open_price - prev_close) / prev_close if prev_close else 0.0

            self.opening_ranges[symbol] = {
                "high": float(sym_bars["high"].max()),
                "low": float(sym_bars["low"].min()),
                "open": open_price,
                "total_volume": int(sym_bars["volume"].sum()),
                "avg_volume": cand["avg_volume"],
                "gap_pct": gap_pct,
                "prev_close": prev_close,
            }

    # ------------------------------------------------------------------
    # Phase 3: scan for entries (called every minute during trading hours)
    # ------------------------------------------------------------------
    def check_entries(self):
        if not self.opening_ranges:
            return

        positions = self.broker.get_positions()
        active_count = len(positions)
        if active_count >= self.params["max_positions"]:
            return

        trades_placed = {t["symbol"] for t in self.trades_today}
        if len(self.trades_today) >= self.params["max_positions"]:
            return

        # Cap effective portfolio to MAX_CAPITAL so sizing behaves as if we only have that much
        portfolio_value = min(self.broker.get_portfolio_value(), config.MAX_CAPITAL)

        # Kill-switch: stop new entries if drawdown vs start-of-day exceeds limit
        account = self.broker.get_account()
        last_equity = min(float(getattr(account, "last_equity", 0) or portfolio_value), config.MAX_CAPITAL)
        if last_equity > 0 and (last_equity - portfolio_value) / last_equity >= self.params["max_drawdown"]:
            log.warning("Kill-switch active — drawdown limit reached, no new entries")
            return

        now = datetime.now(config.ET)
        minutes_since_open = max((now.hour - 9) * 60 + now.minute - 30, 1)

        candidates = [
            s for s in self.opening_ranges
            if s not in positions and s not in trades_placed
        ]
        if not candidates:
            return

        # One batched API call for all candidate symbols
        bars = self.broker.get_bars(candidates, TimeFrame.Minute, start=now - timedelta(minutes=3), end=now)
        if bars is None:
            return

        for symbol in candidates:
            if active_count + len([t for t in self.trades_today if t.get("fill_price")]) >= self.params["max_positions"]:
                break

            or_data = self.opening_ranges[symbol]

            # Gap filter
            if or_data["gap_pct"] < self.params["min_gap_pct"]:
                continue

            # Volume pace filter — is today's volume on track relative to average?
            expected_vol = or_data["avg_volume"] / 390 * minutes_since_open
            if or_data["total_volume"] < expected_vol * self.params["min_volume_mult"]:
                continue

            sym_bars = self.broker.extract_symbol_bars(bars, symbol)
            if sym_bars is None or sym_bars.empty:
                continue

            latest = sym_bars.iloc[-1]
            current_price = float(latest["close"])
            bar_volume = float(latest["volume"])

            # Breakout: price must close above ORB high
            if current_price <= or_data["high"]:
                continue

            # Volume confirmation on the breakout bar itself
            avg_min_vol = or_data["avg_volume"] / 390
            if bar_volume < avg_min_vol * self.params["min_volume_mult"]:
                continue

            # --- Position sizing ---
            stop_price = or_data["low"] * (1.0 - self.params["stop_loss_buffer"])
            risk_per_share = current_price - stop_price
            if risk_per_share < 0.01:
                continue

            size_mult = self.params.get("position_size_mult", 1.0)
            risk_dollars = portfolio_value * self.params["risk_per_trade"] * size_mult
            qty = int(risk_dollars / risk_per_share)

            # Cap at max_position_pct of portfolio
            max_qty = int(portfolio_value * self.params["max_position_pct"] / current_price)
            qty = min(qty, max_qty)

            if qty < 1:
                continue

            # Don't exceed available buying power
            buying_power = self.broker.get_buying_power()
            if qty * current_price > buying_power * 0.95:
                qty = max(1, int(buying_power * 0.95 / current_price))

            take_profit = current_price + risk_per_share * self.params["take_profit_mult"]

            order = self.broker.submit_bracket_order(
                symbol=symbol,
                qty=qty,
                stop_price=stop_price,
                take_profit_price=take_profit,
            )

            if order:
                active_count += 1
                self.trades_today.append({
                    "symbol": symbol,
                    "entry_time": now.isoformat(),
                    "fill_price": current_price,
                    "qty": qty,
                    "stop": round(stop_price, 2),
                    "take_profit": round(take_profit, 2),
                    "gap_pct": round(or_data["gap_pct"], 4),
                    "or_size": round(or_data["high"] - or_data["low"], 2),
                    "volume_mult": round(
                        or_data["total_volume"] / or_data["avg_volume"]
                        if or_data["avg_volume"] else 0, 2
                    ),
                    "exit_price": None,
                    "exit_time": None,
                    "exit_reason": None,
                    "pnl": None,
                    "params_snapshot": {
                        k: v for k, v in self.params.items()
                    },
                })
                log.info(
                    f"ENTRY {symbol} x{qty} @ ~{current_price:.2f} | "
                    f"gap={or_data['gap_pct']:.1%} stop={stop_price:.2f} tp={take_profit:.2f}"
                )

    # ------------------------------------------------------------------
    # Called after EOD close — annotate trades with actual exit data
    # ------------------------------------------------------------------
    def reconcile_trades(self):
        closed_orders = self.broker.get_closed_orders_today()
        sell_fills = {
            o.symbol: o for o in closed_orders
            if str(o.side) in ("OrderSide.SELL", "sell")
            and o.filled_avg_price is not None
        }
        for trade in self.trades_today:
            sym = trade["symbol"]
            if sym in sell_fills and trade["exit_price"] is None:
                o = sell_fills[sym]
                exit_price = float(o.filled_avg_price)
                pnl = (exit_price - trade["fill_price"]) * trade["qty"]
                trade["exit_price"] = round(exit_price, 2)
                trade["exit_time"] = str(o.filled_at)
                trade["pnl"] = round(pnl, 2)
                if exit_price >= trade["take_profit"] * 0.99:
                    trade["exit_reason"] = "take_profit"
                elif exit_price <= trade["stop"] * 1.01:
                    trade["exit_reason"] = "stop_loss"
                else:
                    trade["exit_reason"] = "eod_close"
