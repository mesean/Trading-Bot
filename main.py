"""
24/7 autonomous trading bot — main loop.

Schedule:
  Weekdays
    ~9:15 ET   pre_market_scan()          (15 min before market open)
    9:30–9:45  update_opening_ranges()    (every minute during OR window)
    9:45–3:45  check_entries()            (every minute during trading hours)
    3:45       close_all_positions()      (EOD flat)
    ~4:15      generate_brief()           (daily log file + stdout)

  Saturday 08:00 ET
    run_weekly_research()               (parameter self-optimisation)
"""
import logging
import time
from datetime import datetime, date

import config
from broker import Broker
from strategy import ORBStrategy
from research import save_day_trades, run_weekly_research, detect_market_regime
from daily_brief import generate_brief
from claude_research import run_claude_research
from notifications import notify_eod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    handlers=[logging.StreamHandler(stream=__import__("sys").stdout)],
)
log = logging.getLogger("main")

PRE_MARKET_SCAN_MINS_BEFORE_OPEN = 15
EOD_CLOSE_MINS_BEFORE_CLOSE = 15
BRIEF_MINS_AFTER_CLOSE = 20
LOOP_SLEEP_SECONDS = 60


def main():
    broker = Broker()
    strategy = ORBStrategy(broker)

    account = broker.get_account()
    log.info(
        f"Bot started | {'PAPER' if config.PAPER_TRADING else 'LIVE'} | "
        f"Portfolio: ${float(account.portfolio_value):,.2f}"
    )

    last_day: date | None = None
    last_weekly_research_day: date | None = None
    last_claude_research_day: date | None = None

    while True:
        try:
            now = datetime.now(config.ET)
            today = now.date()

            # Daily state reset
            if today != last_day:
                strategy.reset_day()
                last_day = today

            weekday = now.weekday()  # 0=Mon … 6=Sun

            # Saturday: weekly research + Claude analysis
            if weekday == 5 and last_weekly_research_day != today:
                if now.hour == 8 and now.minute < LOOP_SLEEP_SECONDS // 60 + 1:
                    log.info("=== Weekly research ===")
                    regime = detect_market_regime(broker)
                    run_weekly_research(regime)
                    last_weekly_research_day = today
                    if last_claude_research_day != today:
                        log.info("=== Claude research (Saturday) ===")
                        result = run_claude_research(broker, filter_stats=getattr(strategy, "filter_stats", None))
                        if result:
                            log.info(f"Claude research: {result}")
                        last_claude_research_day = today

            if weekday >= 5:
                time.sleep(LOOP_SLEEP_SECONDS)
                continue

            # --- Weekday market logic ---
            try:
                clock = broker.get_clock()
            except Exception as e:
                log.error(f"Clock fetch failed: {e} — sleeping")
                time.sleep(LOOP_SLEEP_SECONDS)
                continue

            if clock.is_open:
                next_close = clock.next_close.astimezone(config.ET)
                mins_to_close = (next_close - now).total_seconds() / 60

                if mins_to_close <= EOD_CLOSE_MINS_BEFORE_CLOSE and not strategy.eod_close_done:
                    positions_before = len(broker.get_positions())
                    log.info(f"EOD — closing all positions ({mins_to_close:.0f} min to close)")
                    broker.close_all_positions()
                    strategy.eod_close_done = True
                    try:
                        acct = broker.get_account()
                        day_pnl = float(acct.portfolio_value) - float(acct.last_equity or acct.portfolio_value)
                        notify_eod(positions_before, day_pnl)
                    except Exception as e:
                        log.warning(f"EOD notification failed: {e}")
                    # Catch any last fills from the close
                    strategy.check_new_exits()

                else:
                    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
                    mins_since_open = (now - market_open).total_seconds() / 60
                    or_window = strategy.params["opening_range_minutes"]

                    if not strategy.scan_done:
                        # Market is open but we missed pre-market — run scan now
                        regime = detect_market_regime(broker)
                        strategy.update_regime(regime)
                        strategy.pre_market_scan()

                    if mins_since_open < or_window:
                        strategy.update_opening_ranges()
                    else:
                        # Late restart — backfill opening ranges from historical bars
                        # so the bot can still trade today even if it came up after 9:45.
                        if strategy.candidates and not strategy.opening_ranges:
                            log.info("Late start — backfilling opening ranges from history")
                            strategy.update_opening_ranges()
                        if not strategy.eod_close_done:
                            strategy.check_entries()
                            strategy.check_new_exits()

            else:
                # Market is closed
                next_open = clock.next_open.astimezone(config.ET)
                mins_to_open = (next_open - now).total_seconds() / 60

                # Pre-market scan 15 min before open
                if (
                    mins_to_open <= PRE_MARKET_SCAN_MINS_BEFORE_OPEN
                    and mins_to_open > 0
                    and not strategy.scan_done
                ):
                    log.info("Pre-market scan triggered")
                    regime = detect_market_regime(broker)
                    strategy.update_regime(regime)
                    strategy.pre_market_scan()

                # Daily brief ~20 min after close
                if (
                    strategy.eod_close_done
                    and not strategy.brief_done
                    and mins_to_open > 60  # not the very next open
                ):
                    strategy.reconcile_trades()
                    save_day_trades(strategy.trades_today)
                    brief_path = generate_brief(broker, strategy)
                    log.info(f"Daily brief written → {brief_path}")
                    strategy.brief_done = True
                    if last_claude_research_day != today:
                        log.info("=== Claude research (EOD) ===")
                        result = run_claude_research(broker, filter_stats=getattr(strategy, "filter_stats", None))
                        if result:
                            log.info(f"Claude research: {result}")
                        last_claude_research_day = today

        except Exception as e:
            log.error(f"Main loop error: {e}", exc_info=True)

        time.sleep(LOOP_SLEEP_SECONDS)


if __name__ == "__main__":
    main()
