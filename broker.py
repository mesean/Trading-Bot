import logging
from datetime import datetime, timedelta

import pandas as pd
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass, QueryOrderStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import config

log = logging.getLogger(__name__)


class Broker:
    def __init__(self):
        self.trading = TradingClient(
            config.ALPACA_API_KEY,
            config.ALPACA_API_SECRET,
            paper=config.PAPER_TRADING,
        )
        self.data = StockHistoricalDataClient(
            config.ALPACA_API_KEY,
            config.ALPACA_API_SECRET,
        )

    def get_account(self):
        return self.trading.get_account()

    def get_portfolio_value(self) -> float:
        return float(self.trading.get_account().portfolio_value)

    def get_buying_power(self) -> float:
        return float(self.trading.get_account().buying_power)

    def get_positions(self) -> dict:
        return {p.symbol: p for p in self.trading.get_all_positions()}

    def get_clock(self):
        return self.trading.get_clock()

    def submit_bracket_order(
        self,
        symbol: str,
        qty: int,
        stop_price: float,
        take_profit_price: float,
    ):
        try:
            order = self.trading.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
                    stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
                )
            )
            log.info(f"ORDER {symbol} x{qty} | stop={stop_price:.2f} tp={take_profit_price:.2f}")
            return order
        except Exception as e:
            log.error(f"Order failed {symbol}: {e}")
            return None

    def close_position(self, symbol: str):
        try:
            self.trading.close_position(symbol)
            log.info(f"Closed {symbol}")
        except Exception as e:
            log.error(f"Close failed {symbol}: {e}")

    def close_all_positions(self):
        try:
            self.trading.close_all_positions(cancel_orders=True)
            log.info("All positions closed")
        except Exception as e:
            log.error(f"Close-all failed: {e}")

    def get_bars(
        self,
        symbols: list,
        timeframe: TimeFrame,
        start: datetime,
        end: datetime = None,
    ) -> pd.DataFrame | None:
        try:
            req = StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=timeframe,
                start=start,
                end=end,
                feed="iex",
            )
            result = self.data.get_stock_bars(req)
            df = result.df
            return df if not df.empty else None
        except Exception as e:
            log.error(f"get_bars failed: {e}")
            return None

    def get_closed_orders_today(self) -> list:
        try:
            from datetime import date
            today_start = datetime.now(config.ET).replace(hour=0, minute=0, second=0, microsecond=0)
            orders = list(self.trading.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.CLOSED,
                    after=today_start,
                    limit=100,
                )
            ))
            return orders
        except Exception as e:
            log.error(f"get_closed_orders failed: {e}")
            return []

    def submit_market_buy(self, symbol: str, qty: int):
        """Plain market buy with no attached stops — use with submit_trailing_stop."""
        try:
            order = self.trading.submit_order(
                MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.BUY,
                    time_in_force=TimeInForce.DAY,
                )
            )
            log.info(f"BUY {symbol} x{qty}")
            return order
        except Exception as e:
            log.error(f"Market buy failed {symbol}: {e}")
            return None

    def submit_trailing_stop(self, symbol: str, qty: int, trail_percent: float):
        """Trailing stop-sell to manage an open long. Lets winners run, caps losers."""
        try:
            from alpaca.trading.requests import OrderRequest
            order = self.trading.submit_order(OrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                type="trailing_stop",
                time_in_force=TimeInForce.DAY,
                trail_percent=trail_percent,
            ))
            log.info(f"TRAIL STOP {symbol} x{qty} trail={trail_percent}%")
            return order
        except Exception as e:
            log.error(f"Trailing stop failed {symbol}: {e}")
            return None

    def extract_symbol_bars(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
        """Pull a single symbol out of a MultiIndex DataFrame."""
        try:
            if df is None or df.empty:
                return None
            if isinstance(df.index, pd.MultiIndex):
                syms = df.index.get_level_values("symbol")
                if symbol not in syms:
                    return None
                return df.xs(symbol, level="symbol")
            return df
        except Exception:
            return None
