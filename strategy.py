"""
Opening Range Breakout (ORB) Strategy — enhanced
--------------------------------------------------
Filters applied before entry:
  1. Gap up >= min_gap_pct vs previous close
  2. Earnings exclusion: skip stocks with earnings ±1 day
  3. Pre-market volume >= min_premarket_vol (signals institutional interest)
  4. Intraday volume pace >= min_volume_mult x avg daily volume
  5. VWAP filter: price must close above intraday VWAP on the breakout bar
  6. Relative strength: stock must outperform SPY by >= rs_spy_min on the day

Exit management:
  - Trailing stop (trail_percent) replaces fixed bracket TP — lets winners run
  - Hard EOD close at 3:45pm via main.py regardless
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

import pandas as pd
from alpaca.data.timeframe import TimeFrame

import config
from broker import Broker
from research import get_earnings_exclusions

log = logging.getLogger(__name__)


def _vwap(bars: pd.DataFrame) -> float:
    typical = (bars["high"] + bars["low"] + bars["close"]) / 3
    return float((typical * bars["volume"]).sum() / bars["volume"].sum())


class ORBStrategy:
    def __init__(self, broker: Broker):
        self.broker = broker
        self.params: dict = config.load_params()
        self.candidates: Dict[str, dict] = {}
        self.opening_ranges: Dict[str, dict] = {}
        self.trades_today: List[dict] = []
        self.scan_done: bool = False
        self.eod_close_done: bool = False
        self.brief_done: bool = False
        self._earnings_exclusions: set = set()

    def reset_day(self):
        self.params = config.load_params()
        self.candidates = {}
        self.opening_ranges = {}
        self.trades_today = []
        self.scan_done = False
        self.eod_close_done = False
        self.brief_done = False
        self._earnings_exclusions = set()
        log.info("=== New trading day — params reloaded ===")

    def update_regime(self, regime: dict):
        mult = regime.get("position_size_mult", 1.0)
        self.params["position_size_mult"] = mult
        if mult < 1.0:
            log.info(f"High-vol regime — position sizes scaled to {mult:.0%}")

    # ------------------------------------------------------------------
    # Phase 1: pre-market scan
    # ------------------------------------------------------------------
    def pre_market_scan(self):
        log.info("Pre-market scan ...")
        now = datetime.now(config.ET)

        # --- Earnings exclusions ---
        self._earnings_exclusions = get_earnings_exclusions(config.WATCHLIST)

        # --- Daily bars for prev close + avg volume ---
        bars = self.broker.get_bars(
            config.WATCHLIST, TimeFrame.Day,
            start=now - timedelta(days=35),
            end=now - timedelta(days=1),
        )

        for symbol in config.WATCHLIST:
            if symbol in self._earnings_exclusions:
                continue
            sym_bars = self.broker.extract_symbol_bars(bars, symbol) if bars is not None else None
            if sym_bars is None or len(sym_bars) < 5:
                continue
            avg_vol = sym_bars["volume"].rolling(20, min_periods=5).mean().iloc[-1]
            if avg_vol < 500_000:
                continue
            self.candidates[symbol] = {
                "prev_close": float(sym_bars["close"].iloc[-1]),
                "avg_volume": float(avg_vol),
                "premarket_vol": 0,
            }

        # --- Pre-market volume (4am–9:30am) ---
        if self.candidates:
            pm_start = now.replace(hour=4, minute=0, second=0, microsecond=0)
            pm_end = now.replace(hour=9, minute=29, second=0, microsecond=0)
            pm_bars = self.broker.get_bars(list(self.candidates.keys()), TimeFrame.Minute, start=pm_start, end=pm_end)
            if pm_bars is not None:
                for sym in list(self.candidates.keys()):
                    sym_pm = self.broker.extract_symbol_bars(pm_bars, sym)
                    if sym_pm is not None and not sym_pm.empty:
                        self.candidates[sym]["premarket_vol"] = int(sym_pm["volume"].sum())

            # Drop stocks with insufficient pre-market interest
            min_pm = self.params["min_premarket_vol"]
            before = len(self.candidates)
            self.candidates = {
                s: d for s, d in self.candidates.items()
                if d["premarket_vol"] >= min_pm
            }
            log.info(f"Pre-market volume filter: {before} → {len(self.candidates)} candidates")

        log.info(f"Scan complete — {len(self.candidates)} candidates "
                 f"({len(self._earnings_exclusions)} earnings exclusions)")
        self.scan_done = True

    # ------------------------------------------------------------------
    # Phase 2: opening range (called every minute 9:30–9:30+N)
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
    # Phase 3: entry scan (called every minute during trading hours)
    # ------------------------------------------------------------------
    def check_entries(self):
        if not self.opening_ranges:
            return

        positions = self.broker.get_positions()
        if len(positions) >= self.params["max_positions"]:
            return

        trades_placed = {t["symbol"] for t in self.trades_today}
        if len(self.trades_today) >= self.params["max_positions"]:
            return

        portfolio_value = min(self.broker.get_portfolio_value(), config.MAX_CAPITAL)

        # Kill-switch
        account = self.broker.get_account()
        last_equity = min(float(getattr(account, "last_equity", 0) or portfolio_value), config.MAX_CAPITAL)
        if last_equity > 0 and (last_equity - portfolio_value) / last_equity >= self.params["max_drawdown"]:
            log.warning("Kill-switch active — max drawdown reached")
            return

        now = datetime.now(config.ET)
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        minutes_since_open = max((now - market_open).total_seconds() / 60, 1)

        candidates = [
            s for s in self.opening_ranges
            if s not in positions and s not in trades_placed
        ]
        if not candidates:
            return

        # Fetch full intraday bars for all candidates in one call (needed for VWAP)
        bars = self.broker.get_bars(candidates, TimeFrame.Minute, start=market_open, end=now)
        if bars is None:
            return

        # SPY relative strength baseline
        spy_or = self.opening_ranges.get("SPY")
        spy_day_change = (
            (spy_or["open"] - spy_or["prev_close"]) / spy_or["prev_close"]
            if spy_or and spy_or["prev_close"] else 0.0
        )

        active_count = len(positions)

        for symbol in candidates:
            if active_count >= self.params["max_positions"]:
                break

            or_data = self.opening_ranges[symbol]

            # 1. Gap filter
            if or_data["gap_pct"] < self.params["min_gap_pct"]:
                continue

            # 2. Volume pace filter
            expected_vol = or_data["avg_volume"] / 390 * minutes_since_open
            if or_data["total_volume"] < expected_vol * self.params["min_volume_mult"]:
                continue

            # 3. Relative strength vs SPY
            if or_data["gap_pct"] < spy_day_change + self.params["rs_spy_min"]:
                continue

            sym_bars = self.broker.extract_symbol_bars(bars, symbol)
            if sym_bars is None or sym_bars.empty:
                continue

            latest = sym_bars.iloc[-1]
            current_price = float(latest["close"])
            bar_volume = float(latest["volume"])

            # 4. Breakout: close above ORB high
            if current_price <= or_data["high"]:
                continue

            # 5. Volume confirmation on the breakout bar
            avg_min_vol = or_data["avg_volume"] / 390
            if bar_volume < avg_min_vol * self.params["min_volume_mult"]:
                continue

            # 6. VWAP filter: price must be above intraday VWAP
            try:
                vwap = _vwap(sym_bars)
                if current_price < vwap:
                    log.debug(f"{symbol} below VWAP ({current_price:.2f} < {vwap:.2f}) — skip")
                    continue
            except Exception:
                pass  # if VWAP calc fails, don't block the trade

            # --- Position sizing ---
            stop_ref = or_data["low"] * (1.0 - self.params["stop_loss_buffer"])
            risk_per_share = current_price - stop_ref
            if risk_per_share < 0.01:
                continue

            size_mult = self.params.get("position_size_mult", 1.0)
            risk_dollars = portfolio_value * self.params["risk_per_trade"] * size_mult
            qty = int(risk_dollars / risk_per_share)
            max_qty = int(portfolio_value * self.params["max_position_pct"] / current_price)
            qty = min(qty, max_qty)
            if qty < 1:
                continue

            buying_power = self.broker.get_buying_power()
            if qty * current_price > buying_power * 0.95:
                qty = max(1, int(buying_power * 0.95 / current_price))

            # --- Enter: market buy + trailing stop (replaces bracket order) ---
            buy_order = self.broker.submit_market_buy(symbol, qty)
            if not buy_order:
                continue

            trail_order = self.broker.submit_trailing_stop(
                symbol, qty, self.params["trail_percent"]
            )

            active_count += 1
            ref_tp = current_price + risk_per_share * self.params["take_profit_mult"]
            self.trades_today.append({
                "symbol": symbol,
                "entry_time": now.isoformat(),
                "fill_price": current_price,
                "qty": qty,
                "stop_ref": round(stop_ref, 2),
                "trail_percent": self.params["trail_percent"],
                "take_profit_ref": round(ref_tp, 2),
                "gap_pct": round(or_data["gap_pct"], 4),
                "or_size": round(or_data["high"] - or_data["low"], 2),
                "volume_mult": round(
                    or_data["total_volume"] / or_data["avg_volume"]
                    if or_data["avg_volume"] else 0, 2
                ),
                "vwap_at_entry": round(vwap, 2) if "vwap" in dir() else None,
                "spy_rs": round(or_data["gap_pct"] - spy_day_change, 4),
                "exit_price": None,
                "exit_time": None,
                "exit_reason": None,
                "pnl": None,
                "params_snapshot": dict(self.params),
            })
            log.info(
                f"ENTRY {symbol} x{qty} @ ~{current_price:.2f} | "
                f"gap={or_data['gap_pct']:.1%} vwap={vwap:.2f} "
                f"rs_spy={or_data['gap_pct']-spy_day_change:+.1%} trail={self.params['trail_percent']}%"
            )

    # ------------------------------------------------------------------
    # Post-close: annotate trades with actual exit data
    # ------------------------------------------------------------------
    def reconcile_trades(self):
        closed_orders = self.broker.get_closed_orders_today()
        sell_fills = {}
        for o in closed_orders:
            if str(o.side) in ("OrderSide.SELL", "sell") and o.filled_avg_price:
                sym = o.symbol
                # Keep the latest fill per symbol
                if sym not in sell_fills or (o.filled_at and sell_fills[sym].filled_at and
                                              o.filled_at > sell_fills[sym].filled_at):
                    sell_fills[sym] = o

        for trade in self.trades_today:
            sym = trade["symbol"]
            if sym in sell_fills and trade["exit_price"] is None:
                o = sell_fills[sym]
                exit_price = float(o.filled_avg_price)
                pnl = (exit_price - trade["fill_price"]) * trade["qty"]
                trade["exit_price"] = round(exit_price, 2)
                trade["exit_time"] = str(o.filled_at)
                trade["pnl"] = round(pnl, 2)
                order_type = str(getattr(o, "type", "")).lower()
                if "trailing" in order_type:
                    trade["exit_reason"] = "trailing_stop"
                elif "stop" in order_type:
                    trade["exit_reason"] = "stop"
                else:
                    trade["exit_reason"] = "eod_close"
