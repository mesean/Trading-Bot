import os
import json
import pytz
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Windows can set SSLKEYLOGFILE to a virtual path that urllib3 can't write to,
# breaking all HTTPS connections. Clear it early.
os.environ.pop("SSLKEYLOGFILE", None)

ET = pytz.timezone("America/New_York")

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")
PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() == "true"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # optional — enables Claude research loop

if not ALPACA_API_KEY or not ALPACA_API_SECRET:
    raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET must be set in environment or .env")

# Cap the capital the bot will deploy. Position sizes and drawdown checks are
# calculated against min(actual_portfolio_value, MAX_CAPITAL).
MAX_CAPITAL = float(os.environ.get("MAX_CAPITAL", "10000"))

# Persist data across restarts: use Railway volume at /data if it exists, else local
DATA_DIR = Path("/data") if Path("/data").exists() else Path("data")
LOG_DIR = Path("logs")
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

PARAMS_FILE = DATA_DIR / "params.json"
TRADES_FILE = DATA_DIR / "trades.json"

DEFAULT_PARAMS = {
    "opening_range_minutes": 15,   # minutes to build the opening range
    "max_positions": 5,            # max concurrent positions
    "risk_per_trade": 0.02,        # 2% portfolio risk per trade
    "max_position_pct": 0.20,      # max 20% of portfolio in one stock
    "stop_loss_buffer": 0.002,     # extra 0.2% below ORB low for stop
    "take_profit_mult": 2.0,       # kept for research logging; live exits use trailing stop
    "max_drawdown": 0.20,          # kill-switch at 20% portfolio drawdown
    "min_volume_mult": 1.5,        # today's intraday vol pace must be Nx avg daily vol
    "min_gap_pct": 0.005,          # require at least 0.5% gap up at open
    "position_size_mult": 1.0,     # global size scalar, reduced in high-vol regimes
    "trail_percent": 2.0,          # trailing stop distance as % of price
    "min_premarket_vol": 50_000,   # minimum pre-market share volume to qualify
    "rs_spy_min": 0.003,           # stock must outperform SPY by at least 0.3% on the day
}


def load_params() -> dict:
    if PARAMS_FILE.exists():
        with open(PARAMS_FILE) as f:
            saved = json.load(f)
        return {**DEFAULT_PARAMS, **saved}
    return DEFAULT_PARAMS.copy()


def save_params(params: dict):
    with open(PARAMS_FILE, "w") as f:
        json.dump(params, f, indent=2)


# Liquid universe — ~95 large-cap equities + sector ETFs.
# All names chosen for: >$5B mkt cap, >2M avg daily volume, reliable pre-market activity.
WATCHLIST = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA",
    # Semis
    "AMD", "AVGO", "QCOM", "INTC", "TXN", "MU", "SMCI", "LRCX", "KLAC",
    "AMAT", "ARM", "MRVL",
    # Software / cloud / cyber
    "CRM", "ADBE", "ORCL", "NOW", "IBM", "INTU", "PANW", "FTNT", "CRWD", "ZS",
    "NFLX", "PLTR", "SNOW", "DDOG", "NET",
    # Finance / banks
    "JPM", "BAC", "GS", "MS", "C", "WFC", "BLK", "SCHW", "AXP",
    # Payments / fintech
    "V", "MA", "COIN", "HOOD", "SOFI",
    # Health / pharma / medtech
    "JNJ", "PFE", "ABBV", "UNH", "LLY", "MRK", "TMO", "DHR", "ABT", "BMY",
    "CVS", "ISRG",
    # Consumer / retail / travel
    "COST", "WMT", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", "PEP", "KO",
    "DIS", "BKNG", "ABNB",
    # Industrial / defense
    "BA", "CAT", "DE", "LMT", "RTX", "GE", "HON",
    # Energy
    "XOM", "CVX", "COP", "OXY", "SLB",
    # Auto
    "F", "GM", "RIVN",
    # Communications
    "CMCSA", "T", "VZ", "ROKU",
    # Index / sector ETFs
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "SMH",
]
