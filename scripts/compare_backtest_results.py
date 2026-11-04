import sys
sys.path.insert(0, ".")
import requests
import json
from pathlib import Path
from scripts.run_real_underlying_backtest import UnderlyingPointBacktester, acquire_real_nifty_spot_candles

csv_path = Path("data/historical/real_nifty_spot_8d.csv")
candles = acquire_real_nifty_spot_candles(csv_path, range_str="8d")
script_res = UnderlyingPointBacktester().run(candles)

api_res = requests.post("http://127.0.0.1:8000/api/v1/backtest/run", json={
    "dataset_id": "HISTORICAL_SPOT_8D",
    "start_date": "2026-10-22",
    "end_date": "2026-11-02"
}).json()

print("=== SCRIPT TRADES (Unscaled Index Points) ===")
script_sum = 0.0
for i, t in enumerate(script_res["trades"]):
    script_sum += t["pnl_points"]
    print(f"Trade {i+1}: {t['date']} {t['direction']:5} Entry={t['entry_price']} Exit={t['exit_price']} -> Points={t['pnl_points']:+6.2f}")
print(f"TOTAL SCRIPT POINTS: {script_sum:+.2f}")

print("\n=== API TRADES (1 Lot = 25 Qty, ₹40 Costs) ===")
detail_res = requests.get(f"http://127.0.0.1:8000/api/v1/backtest/{api_res['summary']['run_id']}").json()
api_net_sum = 0.0
api_gross_sum = 0.0
for i, t in enumerate(detail_res["trades"]):
    api_gross_sum += float(t["gross_pnl"])
    api_net_sum += float(t["net_pnl"])
    print(f"Trade {i+1}: Direction={t['direction']:5} Qty={t['qty']} Entry={t['entry_price']} Exit={t['exit_price']} -> Gross PnL=INR {float(t['gross_pnl']):+8.2f}, Costs=INR {float(t['trading_costs']):.2f}, Net PnL=INR {float(t['net_pnl']):+8.2f}")

print(f"\nTOTAL API GROSS: INR {api_gross_sum:+.2f}")
print(f"TOTAL API COSTS: INR {len(detail_res['trades']) * 40:.2f}")
print(f"TOTAL API NET:   INR {api_net_sum:+.2f}")
print(f"RELATIONSHIP: ({script_sum:.2f} pts * 25 qty) - (8 * 40 costs) = {script_sum * 25 - 320:.2f} == {api_net_sum:.2f}")
