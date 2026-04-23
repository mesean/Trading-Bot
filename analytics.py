"""
Post-trade analytics — MAE/MFE tracking plus bucketed performance stats.

Enriches every completed trade with:
  - mae_pct, mfe_pct         : max adverse/favorable excursion as % of entry
  - mae_dollars, mfe_dollars : same measured in P&L
  - sector                   : mapped from symbol
  - entry_hour               : 9..15 (ET)

Aggregates trade history into the shape Claude and the daily brief
consume when reasoning about what's working.
"""
import logging

import pandas as pd
from alpaca.data.timeframe import TimeFrame

import config

log = logging.getLogger(__name__)


SECTOR_MAP: dict = {
    # Mega-cap tech
    "AAPL": "tech", "MSFT": "tech", "NVDA": "tech", "AMZN": "tech",
    "META": "tech", "GOOGL": "tech", "TSLA": "tech",
    # Semis
    "AMD": "semis", "AVGO": "semis", "QCOM": "semis", "INTC": "semis",
    "TXN": "semis", "MU": "semis", "SMCI": "semis", "LRCX": "semis",
    "KLAC": "semis", "AMAT": "semis", "ARM": "semis", "MRVL": "semis",
    "TSM": "semis", "ASML": "semis", "ON": "semis", "NXPI": "semis",
    "ADI": "semis", "ANET": "semis",
    # Software / cloud / cyber / EDA / AI infra
    "CRM": "software", "ADBE": "software", "ORCL": "software", "NOW": "software",
    "IBM": "software", "INTU": "software", "PANW": "software", "FTNT": "software",
    "CRWD": "software", "ZS": "software", "NFLX": "software",
    "PLTR": "software", "SNOW": "software", "DDOG": "software", "NET": "software",
    "SNPS": "software", "CDNS": "software", "MDB": "software", "TEAM": "software",
    "DELL": "software", "CSCO": "software", "HPE": "software", "VRT": "software",
    # Consumer internet / commerce / gig / streaming
    "SHOP": "software", "UBER": "software", "DASH": "software",
    "RDDT": "software", "SPOT": "software", "PYPL": "fintech", "SQ": "fintech",
    # Gaming / betting
    "RBLX": "software", "TTWO": "software", "EA": "software",
    "DKNG": "consumer", "PENN": "consumer",
    # Bitcoin / crypto-sensitive
    "MSTR": "fintech", "MARA": "fintech", "RIOT": "fintech",
    # Finance
    "JPM": "finance", "BAC": "finance", "GS": "finance", "MS": "finance",
    "C": "finance", "WFC": "finance", "BLK": "finance", "SCHW": "finance",
    "AXP": "finance", "USB": "finance", "PNC": "finance",
    # Fintech / payments
    "V": "fintech", "MA": "fintech", "COIN": "fintech", "HOOD": "fintech",
    "SOFI": "fintech", "AFRM": "fintech",
    # Health
    "JNJ": "health", "PFE": "health", "ABBV": "health", "UNH": "health",
    "LLY": "health", "MRK": "health", "TMO": "health", "DHR": "health",
    "ABT": "health", "BMY": "health", "CVS": "health", "ISRG": "health",
    "REGN": "health", "GILD": "health", "AMGN": "health", "VRTX": "health",
    # Consumer discretionary / staples
    "COST": "consumer", "WMT": "consumer", "TGT": "consumer", "HD": "consumer",
    "LOW": "consumer", "NKE": "consumer", "SBUX": "consumer", "MCD": "consumer",
    "PEP": "consumer", "KO": "consumer", "DIS": "consumer", "BKNG": "consumer",
    "ABNB": "consumer", "CVNA": "consumer", "CHWY": "consumer",
    "KHC": "consumer", "MDLZ": "consumer", "PG": "consumer", "GME": "consumer",
    # Travel
    "AAL": "consumer", "DAL": "consumer", "UAL": "consumer",
    "LUV": "consumer", "CCL": "consumer",
    # Industrial / defense
    "BA": "industrial", "CAT": "industrial", "DE": "industrial", "LMT": "industrial",
    "RTX": "industrial", "GE": "industrial", "HON": "industrial",
    # Energy (traditional + clean)
    "XOM": "energy", "CVX": "energy", "COP": "energy", "OXY": "energy",
    "SLB": "energy", "ENPH": "energy", "FSLR": "energy", "PLUG": "energy",
    # Auto
    "F": "auto", "GM": "auto", "RIVN": "auto",
    # Communications
    "CMCSA": "comms", "T": "comms", "VZ": "comms", "ROKU": "comms",
    # Broad index ETFs
    "SPY": "etf_broad", "QQQ": "etf_broad", "IWM": "etf_broad", "DIA": "etf_broad",
    # Sector ETFs
    "XLK": "etf_tech", "XLF": "etf_finance", "XLE": "etf_energy",
    "XLV": "etf_health", "SMH": "etf_semis",
}


def _sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "other")


def _entry_hour(trade: dict) -> int | None:
    try:
        et = pd.to_datetime(trade.get("entry_time"))
        if et is pd.NaT:
            return None
        if et.tzinfo is None:
            return int(et.hour)
        return int(et.tz_convert(config.ET).hour)
    except Exception:
        return None


def compute_mae_mfe(broker, trade: dict) -> tuple[float, float, float, float]:
    """
    Return (mae_pct, mfe_pct, mae_dollars, mfe_dollars) for a closed trade.
    MAE is <= 0, MFE is >= 0. Returns zeros if bars can't be fetched.
    """
    entry_price = float(trade.get("fill_price") or 0)
    qty = int(trade.get("qty") or 0)
    if entry_price <= 0 or qty <= 0:
        return 0.0, 0.0, 0.0, 0.0

    try:
        start = pd.to_datetime(trade["entry_time"]).to_pydatetime()
        end = pd.to_datetime(trade.get("exit_time") or trade["entry_time"]).to_pydatetime()
    except Exception:
        return 0.0, 0.0, 0.0, 0.0

    try:
        bars = broker.get_bars([trade["symbol"]], TimeFrame.Minute, start=start, end=end)
        sym_bars = broker.extract_symbol_bars(bars, trade["symbol"]) if bars is not None else None
        if sym_bars is None or sym_bars.empty:
            return 0.0, 0.0, 0.0, 0.0
        min_price = float(sym_bars["low"].min())
        max_price = float(sym_bars["high"].max())
    except Exception as e:
        log.warning(f"MAE/MFE fetch failed for {trade.get('symbol')}: {e}")
        return 0.0, 0.0, 0.0, 0.0

    mae_pct = (min_price - entry_price) / entry_price
    mfe_pct = (max_price - entry_price) / entry_price
    mae_dollars = (min_price - entry_price) * qty
    mfe_dollars = (max_price - entry_price) * qty

    return (
        round(mae_pct, 4), round(mfe_pct, 4),
        round(mae_dollars, 2), round(mfe_dollars, 2),
    )


def annotate_trade(broker, trade: dict):
    """Enrich a closed trade in-place with MAE/MFE, sector, and entry hour."""
    if trade.get("exit_price") is None:
        return
    if "mae_pct" in trade:
        return  # already enriched
    mae_pct, mfe_pct, mae_dollars, mfe_dollars = compute_mae_mfe(broker, trade)
    trade["mae_pct"] = mae_pct
    trade["mfe_pct"] = mfe_pct
    trade["mae_dollars"] = mae_dollars
    trade["mfe_dollars"] = mfe_dollars
    trade["sector"] = _sector(trade["symbol"])
    trade["entry_hour"] = _entry_hour(trade)


def _bucket(x: float, edges: list, labels: list) -> str:
    for edge, label in zip(edges, labels):
        if x < edge:
            return label
    return labels[-1]


def _pf(win_sum: float, loss_sum: float) -> float:
    if loss_sum <= 0:
        return float("inf") if win_sum > 0 else 0.0
    return win_sum / loss_sum


def _bucket_stats(subset: pd.DataFrame) -> dict:
    w = subset[subset["is_win"]]
    l = subset[~subset["is_win"]]
    return {
        "n":        int(len(subset)),
        "win_rate": round(len(w) / len(subset), 3) if len(subset) else 0.0,
        "pf":       round(_pf(w["pnl"].sum(), abs(l["pnl"].sum())), 2),
        "pnl":      round(subset["pnl"].sum(), 2),
    }


def compute_stats(trades: list) -> dict:
    """Aggregate all closed trades into an overall + bucketed-stats dict."""
    closed = [t for t in trades if t.get("pnl") is not None]
    if not closed:
        return {"overall": {"n": 0}}

    df = pd.DataFrame(closed)
    df["is_win"] = df["pnl"] > 0
    wins = df[df["is_win"]]
    losses = df[~df["is_win"]]

    overall = {
        "n":             len(df),
        "win_rate":      round(len(wins) / len(df), 3),
        "profit_factor": round(_pf(wins["pnl"].sum(), abs(losses["pnl"].sum())), 2),
        "avg_win":       round(wins["pnl"].mean(), 2)   if len(wins)   else 0.0,
        "avg_loss":      round(losses["pnl"].mean(), 2) if len(losses) else 0.0,
        "total_pnl":     round(df["pnl"].sum(), 2),
    }

    # MAE/MFE
    mae_mfe = {}
    if "mfe_pct" in df.columns:
        tracked = df.dropna(subset=["mfe_pct"])
        if not tracked.empty:
            w = tracked[tracked["is_win"]]
            l = tracked[~tracked["is_win"]]
            mae_mfe = {
                "n_tracked":             len(tracked),
                "avg_mae_winners_pct":   round(w["mae_pct"].mean() * 100, 2) if len(w) else 0.0,
                "avg_mfe_winners_pct":   round(w["mfe_pct"].mean() * 100, 2) if len(w) else 0.0,
                "avg_mae_losers_pct":    round(l["mae_pct"].mean() * 100, 2) if len(l) else 0.0,
                "avg_mfe_losers_pct":    round(l["mfe_pct"].mean() * 100, 2) if len(l) else 0.0,
            }

    def _group(colname):
        result = {}
        if colname not in df.columns:
            return result
        for key, sub in df.dropna(subset=[colname]).groupby(colname):
            if len(sub) > 0:
                result[str(key) if not isinstance(key, int) else str(int(key))] = _bucket_stats(sub)
        return result

    by_hour = _group("entry_hour")
    by_sector = _group("sector")

    # Gap bucket
    if "gap_pct" in df.columns:
        df["gap_bucket"] = df["gap_pct"].fillna(0).apply(
            lambda g: _bucket(g, [0.005, 0.010, 0.020], ["<0.5%", "0.5-1%", "1-2%", ">2%"])
        )
        by_gap = _group("gap_bucket")
    else:
        by_gap = {}

    # Volume bucket
    if "volume_mult" in df.columns:
        df["vol_bucket"] = df["volume_mult"].fillna(0).apply(
            lambda v: _bucket(v, [1.5, 2.5, 4.0], ["<1.5x", "1.5-2.5x", "2.5-4x", ">4x"])
        )
        by_vol = _group("vol_bucket")
    else:
        by_vol = {}

    return {
        "overall":   overall,
        "mae_mfe":   mae_mfe,
        "by_hour":   by_hour,
        "by_sector": by_sector,
        "by_gap":    by_gap,
        "by_volume": by_vol,
    }


def format_summary(stats: dict) -> str:
    """Short human-readable block for the daily brief."""
    o = stats.get("overall", {})
    if o.get("n", 0) == 0:
        return "  No analytics yet — accumulating trade history."

    lines = [
        f"  Total trades    : {o['n']}",
        f"  Win rate        : {o['win_rate']*100:.0f}%",
        f"  Profit factor   : {o['profit_factor']:.2f}",
        f"  Avg win / loss  : ${o['avg_win']:+.2f}  /  ${o['avg_loss']:+.2f}",
        f"  Total P&L       : ${o['total_pnl']:+.2f}",
    ]

    m = stats.get("mae_mfe") or {}
    if m.get("n_tracked", 0) > 0:
        lines += [
            f"  Winner MAE avg  : {m['avg_mae_winners_pct']:+.2f}%  (dip before winning)",
            f"  Winner MFE avg  : {m['avg_mfe_winners_pct']:+.2f}%  (peak run captured)",
            f"  Loser MFE avg   : {m['avg_mfe_losers_pct']:+.2f}%  (missed TP potential)",
        ]

    by_hour = stats.get("by_hour", {})
    if by_hour:
        lines.append("  By hour (ET):")
        for h in sorted(by_hour.keys(), key=lambda x: int(float(x))):
            s = by_hour[h]
            lines.append(
                f"    {h}:00  n={s['n']:<3} win={s['win_rate']*100:>3.0f}%  "
                f"pf={s['pf']:.2f}  pnl=${s['pnl']:+.2f}"
            )

    by_sector = stats.get("by_sector", {})
    if by_sector:
        lines.append("  By sector:")
        for name in sorted(by_sector.keys()):
            s = by_sector[name]
            lines.append(
                f"    {name:<12} n={s['n']:<3} win={s['win_rate']*100:>3.0f}%  "
                f"pf={s['pf']:.2f}  pnl=${s['pnl']:+.2f}"
            )

    return "\n".join(lines)
