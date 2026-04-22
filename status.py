"""Quick status check — run anytime: py status.py"""
import config
from broker import Broker
from datetime import datetime

b = Broker()
now = datetime.now(config.ET).strftime("%Y-%m-%d %H:%M ET")
acc = b.get_account()
clock = b.get_clock()
positions = b.get_positions()

print(f"\n=== Bot Status — {now} ===")
print(f"Market open:    {clock.is_open}")
print(f"Next open:      {clock.next_open.astimezone(config.ET).strftime('%a %b %d %H:%M ET')}")
print(f"Account value:  ${float(acc.portfolio_value):,.2f}")
print(f"Cash:           ${float(acc.cash):,.2f}")
print(f"Budget cap:     ${config.MAX_CAPITAL:,.2f}")
print(f"Day P&L:        ${float(acc.portfolio_value) - float(acc.last_equity):+,.2f}")

if positions:
    print(f"\nOpen positions ({len(positions)}):")
    for sym, p in positions.items():
        pnl = float(p.unrealized_pl)
        print(f"  {sym:6s}  qty={p.qty:>4}  entry=${float(p.avg_entry_price):.2f}  unrealized P&L=${pnl:+.2f}")
else:
    print("\nNo open positions.")

params = config.load_params()
print(f"\nStrategy params:")
print(f"  OR window:    {params['opening_range_minutes']} min")
print(f"  Min gap:      {params['min_gap_pct']:.1%}")
print(f"  Min vol mult: {params['min_volume_mult']:.1f}x")
print(f"  TP mult:      {params['take_profit_mult']:.1f}x risk")
print()
