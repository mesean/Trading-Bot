"""
Research module — self-improving strategy loop.

After each trading day:
  - save_day_trades() appends the day's trades to trades.json

Every Saturday morning:
  - run_weekly_research() loads all accumulated trades, analyzes what
    parameter combinations produced the best outcomes, and writes an
    updated params.json.  Changes are bounded so we never over-fit to
    a thin data set.

Market regime detection:
  - detect_market_regime() looks at SPY's recent volatility and returns
    a multiplier that scales position sizes down in choppy/volatile markets.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from alpaca.data.timeframe import TimeFrame

import config

log = logging.getLogger(__name__)

MIN_TRADES_TO_OPTIMIZE = 10   # don't touch params until we have this many trades
MAX_PARAM_CHANGE = 0.20       # never change any parameter by more than 20% at once


# ------------------------------------------------------------------
# Persistence helpers
# ------------------------------------------------------------------

def _load_trades() -> list:
    if not config.TRADES_FILE.exists():
        return []
    with open(config.TRADES_FILE) as f:
        return json.load(f)


def _save_trades(trades: list):
    with open(config.TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


def save_day_trades(trades_today: list):
    """Append today's completed trades to the persistent history."""
    if not trades_today:
        return
    history = _load_trades()
    today = datetime.now(config.ET).date().isoformat()
    for t in trades_today:
        record = {**t, "date": today}
        # Only save if we have an actual exit (don't save still-open trades)
        if record.get("pnl") is not None:
            history.append(record)
    _save_trades(history)
    log.info(f"Saved {len(trades_today)} trades to history (total: {len(history)})")


# ------------------------------------------------------------------
# Market regime detection
# ------------------------------------------------------------------

def detect_market_regime(broker) -> dict:
    """
    Fetch the last 10 trading days of SPY and compute realized volatility.
    Returns a dict with position_size_mult (0.5 in high-vol, 1.0 normal).
    """
    now = datetime.now(config.ET)
    start = now - timedelta(days=20)
    try:
        bars = broker.get_bars(["SPY"], TimeFrame.Day, start=start, end=now)
        sym_bars = broker.extract_symbol_bars(bars, "SPY") if bars is not None else None
        if sym_bars is None or len(sym_bars) < 5:
            return {"regime": "unknown", "position_size_mult": 1.0, "vol_5d": None}

        returns = sym_bars["close"].pct_change().dropna()
        vol_5d = float(returns.tail(5).std() * (252 ** 0.5))  # annualised

        if vol_5d > 0.30:
            regime = "high_vol"
            mult = 0.50
        elif vol_5d > 0.20:
            regime = "elevated_vol"
            mult = 0.75
        else:
            regime = "normal"
            mult = 1.0

        log.info(f"Market regime: {regime} (5d ann vol {vol_5d:.1%}, size mult {mult})")
        return {"regime": regime, "position_size_mult": mult, "vol_5d": round(vol_5d, 4)}
    except Exception as e:
        log.error(f"Regime detection failed: {e}")
        return {"regime": "unknown", "position_size_mult": 1.0, "vol_5d": None}


# ------------------------------------------------------------------
# Weekly parameter optimisation
# ------------------------------------------------------------------

def run_weekly_research(regime: dict):
    """
    Analyse accumulated trade history to find better parameters.

    Methodology:
      1. Load all trades.
      2. Build a profit_factor score (gross wins / gross losses) for each
         trade, labelled by the setup characteristics.
      3. Find the gap_pct and volume_mult thresholds that maximise
         profit_factor on the historical data.
      4. Adjust take_profit_mult based on whether recent trades are more
         often stopped out or hitting target.
      5. Bound all changes to MAX_PARAM_CHANGE.
    """
    trades = _load_trades()
    closed = [t for t in trades if t.get("pnl") is not None]

    if len(closed) < MIN_TRADES_TO_OPTIMIZE:
        log.info(f"Weekly research: only {len(closed)} closed trades, need {MIN_TRADES_TO_OPTIMIZE}. Skipping.")
        return

    params = config.load_params()
    original = params.copy()
    df = pd.DataFrame(closed)

    log.info(f"Weekly research: analysing {len(df)} closed trades")

    # --- Overall stats ---
    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    win_rate = len(wins) / len(df)
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0
    profit_factor = (wins["pnl"].sum() / abs(losses["pnl"].sum())) if losses["pnl"].sum() != 0 else float("inf")
    log.info(f"  Overall — win_rate={win_rate:.1%} avg_win=${avg_win:.2f} avg_loss=${avg_loss:.2f} PF={profit_factor:.2f}")

    # --- min_gap_pct optimisation ---
    # Test thresholds: which minimum gap filter produces the best profit_factor?
    best_gap = params["min_gap_pct"]
    best_pf = -1.0
    for threshold in [0.003, 0.005, 0.008, 0.010, 0.015]:
        subset = df[df["gap_pct"] >= threshold]
        if len(subset) < 5:
            continue
        pf = _profit_factor(subset)
        if pf > best_pf:
            best_pf = pf
            best_gap = threshold

    new_gap = _bounded(params["min_gap_pct"], best_gap, MAX_PARAM_CHANGE)
    params["min_gap_pct"] = round(new_gap, 4)

    # --- min_volume_mult optimisation ---
    best_vmult = params["min_volume_mult"]
    best_pf = -1.0
    for threshold in [1.2, 1.5, 2.0, 2.5, 3.0]:
        subset = df[df["volume_mult"] >= threshold]
        if len(subset) < 5:
            continue
        pf = _profit_factor(subset)
        if pf > best_pf:
            best_pf = pf
            best_vmult = threshold

    new_vmult = _bounded(params["min_volume_mult"], best_vmult, MAX_PARAM_CHANGE)
    params["min_volume_mult"] = round(new_vmult, 2)

    # --- take_profit_mult adjustment ---
    # If win_rate > 60%: winners are reliable → let them run (increase TP mult)
    # If win_rate < 40%: too many losers → tighten TP to lock in smaller gains
    if win_rate > 0.60 and avg_win > avg_loss:
        new_tp = min(3.5, params["take_profit_mult"] * 1.10)
    elif win_rate < 0.40 or avg_loss > avg_win * 1.5:
        new_tp = max(1.5, params["take_profit_mult"] * 0.90)
    else:
        new_tp = params["take_profit_mult"]

    params["take_profit_mult"] = round(_bounded(params["take_profit_mult"], new_tp, MAX_PARAM_CHANGE), 2)

    # Apply regime multiplier
    params["position_size_mult"] = regime.get("position_size_mult", 1.0)

    config.save_params(params)

    changes = {k: (original[k], params[k]) for k in params if params.get(k) != original.get(k)}
    if changes:
        log.info(f"Params updated: {changes}")
    else:
        log.info("Params unchanged after research")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def get_earnings_exclusions(symbols: list) -> set:
    """
    Return symbols that have earnings today, yesterday, or tomorrow.
    Trading into an earnings print makes ORB signals unreliable — the gap
    is news-driven, not momentum-driven.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — skipping earnings filter")
        return set()

    exclusions = set()
    today = datetime.now(config.ET).date()
    window = {today - timedelta(days=1), today, today + timedelta(days=1)}

    for sym in symbols:
        try:
            cal = yf.Ticker(sym).calendar
            if cal is None:
                continue
            # calendar is a dict in newer yfinance versions
            dates = []
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date", [])
                dates = raw if hasattr(raw, "__iter__") and not hasattr(raw, "strftime") else [raw]
            elif hasattr(cal, "loc") and "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"]
                dates = list(val) if hasattr(val, "__iter__") else [val]
            for d in dates:
                if hasattr(d, "date"):
                    d = d.date()
                if d in window:
                    exclusions.add(sym)
                    log.info(f"Earnings exclusion: {sym} ({d})")
                    break
        except Exception:
            pass

    return exclusions


def _profit_factor(df: pd.DataFrame) -> float:
    wins = df[df["pnl"] > 0]["pnl"].sum()
    losses = abs(df[df["pnl"] <= 0]["pnl"].sum())
    return wins / losses if losses > 0 else float("inf")


def _bounded(current: float, target: float, max_change: float) -> float:
    """Move current toward target but not by more than max_change * |current|."""
    delta = target - current
    max_delta = abs(current) * max_change
    delta = max(-max_delta, min(max_delta, delta))
    return current + delta
