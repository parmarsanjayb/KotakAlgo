import sqlite3

conn = sqlite3.connect('database.db')
conn.row_factory = sqlite3.Row

rows = conn.execute("SELECT * FROM trades WHERE timestamp LIKE '2026-06-09%'").fetchall()

print("--- TODAY'S TRADES (2026-06-09) ---")
total_pnl = 0.0
closed_count = 0
open_count = 0

for r in rows:
    pnl = r['pnl']
    if pnl is not None:
        total_pnl += pnl
        closed_count += 1
        print(f"CLOSED: {r['ticker']} {r['action']} | Entry: {r['entry_price']} | Exit: {r['exit_price']} | Qty: {r['quantity']} | PNL: ₹{pnl:.2f} ({r['exit_reason']})")
    else:
        open_count += 1
        print(f"OPEN: {r['ticker']} {r['action']} | Entry: {r['entry_price']} | Qty: {r['quantity']}")

print("\n--- SUMMARY ---")
print(f"Total Closed Trades: {closed_count}")
print(f"Total Open Trades: {open_count}")
print(f"Net Realized PNL: ₹{total_pnl:.2f}")

conn.close()
