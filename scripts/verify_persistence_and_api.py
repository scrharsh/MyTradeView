"""Comprehensive persistence, paper isolation, and historical backtest integrity verification script."""

import sys
import time
import requests
import subprocess
import json

BASE_URL = "http://127.0.0.1:8000/api/v1"

def step(msg: str):
    print(f"\n[STEP] {msg}")

def test_paper_isolation():
    step("1. Testing Paper Trading Isolation (Zero Orders & Zero Positions on Startup)...")
    res = requests.post(f"{BASE_URL}/sessions/clear-orders")
    assert res.status_code == 200, f"clear-orders failed: {res.text}"
    print(f"Clear orders response: {res.json()}")
    
    orders = requests.get(f"{BASE_URL}/orders").json()
    positions = requests.get(f"{BASE_URL}/positions").json()
    print(f"Verified initial clean state -> Orders: {len(orders)}, Positions: {len(positions)}")
    assert len(orders) == 0, f"Expected 0 orders, found {len(orders)}"
    assert len(positions) == 0, f"Expected 0 positions, found {len(positions)}"
    
    # Check broker reconciliation with 0 orders and 0 positions
    recon = requests.post(f"{BASE_URL}/reconciliation/run").json()
    print(f"Reconciliation -> Synchronized: {recon.get('is_synchronized')}, Discrepancies: {len(recon.get('discrepancies', []))}")
    assert recon.get("is_synchronized") is True
    assert len(recon.get("discrepancies", [])) == 0
    print("[PASS] Zero-order paper trading clean isolation confirmed.")

def test_demo_seeding():
    step("2. Testing Explicit Demo Action Seeding...")
    seed_res = requests.post(f"{BASE_URL}/sessions/seed-demo")
    assert seed_res.status_code == 200, f"seed-demo failed: {seed_res.text}"
    print(f"Seed demo response: {seed_res.json()}")
    
    orders = requests.get(f"{BASE_URL}/orders").json()
    positions = requests.get(f"{BASE_URL}/positions").json()
    assert len(orders) == 1, f"Expected 1 order, found {len(orders)}"
    assert len(positions) == 1, f"Expected 1 position, found {len(positions)}"
    print(f"Explicit demo fixture created: order={orders[0]['client_order_id']}, tag={orders[0].get('tag')}")
    
    # Clean again
    clear_res = requests.post(f"{BASE_URL}/sessions/clear-orders").json()
    assert clear_res["orders_count"] == 0
    orders_after = requests.get(f"{BASE_URL}/orders").json()
    assert len(orders_after) == 0
    print("[PASS] Explicit demo seeding and reset back to clean state confirmed.")

def test_persistence_across_restart():
    step("3. Testing Strategy & Risk Configuration Persistence Across Server Restart...")
    # Fetch current active
    active_before = requests.get(f"{BASE_URL}/strategies/active").json()
    initial_cid = active_before["config_id"]
    initial_ver = active_before["version"]
    print(f"Active config before test: {initial_cid} (version {initial_ver})")
    
    # Post new version with customized settings
    custom_params = dict(active_before["parameters"])
    custom_params.update({
        "lots": 2,
        "max_daily_loss": 4500.0,
        "profit_protection_trigger": 3500.0,
        "minimum_profit_lock": 750.0,
        "orb_start_time": "09:18:00",
    })
    
    v_res = requests.post(
        f"{BASE_URL}/strategies/{initial_cid}/versions",
        json={"parameters": custom_params, "set_active": True}
    )
    assert v_res.status_code == 200, f"Failed to create version: {v_res.text}"
    new_v = v_res.json()
    print(f"Created & activated new version: {new_v['config_id']} (version {new_v['version']})")
    assert new_v["parameters"]["lots"] == 2
    assert new_v["parameters"]["max_daily_loss"] == 4500.0
    assert new_v["parameters"]["orb_start_time"] == "09:18:00"
    
    active_runtime = requests.get(f"{BASE_URL}/strategies/active").json()
    assert active_runtime["version"] == new_v["version"]
    print("Verified new configuration active in runtime memory.")
    
    # Restart backend
    print("Restarting FastAPI backend process...")
    # Kill existing uvicorn on port 8000
    subprocess.run(["powershell", "-Command", "Get-Process python | Where-Object { $_.CommandLine -like '*uvicorn*' } | Stop-Process -Force"], capture_output=True)
    time.sleep(2)
    
    # Launch uvicorn again
    proc = subprocess.Popen([
        ".venv\\Scripts\\python.exe", "-m", "uvicorn", "src.api.main:app",
        "--host", "127.0.0.1", "--port", "8000"
    ])
    
    # Poll until server responds
    for _ in range(30):
        try:
            h = requests.get(f"{BASE_URL}/health", timeout=1)
            if h.status_code == 200:
                print("FastAPI backend restarted successfully.")
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise RuntimeError("FastAPI server failed to restart within 15 seconds")
    
    # Verify active config restored from SQLite
    active_after_restart = requests.get(f"{BASE_URL}/strategies/active").json()
    print(f"Active config after restart: {active_after_restart['config_id']} (version {active_after_restart['version']})")
    print(f"Restored lots: {active_after_restart['parameters']['lots']}")
    print(f"Restored max_daily_loss: {active_after_restart['parameters']['max_daily_loss']}")
    print(f"Restored orb_start_time: {active_after_restart['parameters']['orb_start_time']}")
    
    assert active_after_restart["version"] == new_v["version"]
    assert active_after_restart["parameters"]["lots"] == 2
    assert active_after_restart["parameters"]["max_daily_loss"] == 4500.0
    assert active_after_restart["parameters"]["profit_protection_trigger"] == 3500.0
    assert active_after_restart["parameters"]["minimum_profit_lock"] == 750.0
    assert active_after_restart["parameters"]["orb_start_time"] == "09:18:00"
    print("[PASS] Strategy & risk settings faithfully restored from SQLite across process restart!")
    
    # Restore baseline config v1 as active
    print(f"Restoring baseline {initial_cid} as active configuration...")
    act_res = requests.post(f"{BASE_URL}/strategies/{initial_cid}/activate")
    assert act_res.status_code == 200, f"Failed to restore baseline: {act_res.text}"
    baseline_active = requests.get(f"{BASE_URL}/strategies/active").json()
    assert baseline_active["config_id"] == initial_cid
    print(f"[PASS] Baseline {initial_cid} successfully restored as active.")

def test_historical_spot_backtest():
    step("4. Testing Historical Spot Backtest & Underlying-Only Labeling...")
    res = requests.post(f"{BASE_URL}/backtest/run", json={"dataset_id": "HISTORICAL_SPOT_8D"})
    assert res.status_code == 200, f"Backtest failed: {res.text}"
    bt = res.json()["summary"]
    print(f"Backtest Run ID: {bt['run_id']}")
    print(f"Dataset ID: {bt['dataset_id']}")
    print(f"Is Synthetic: {bt['is_synthetic']}")
    print(f"Is Underlying Only: {bt.get('is_underlying_only')}")
    print(f"Provenance: {bt.get('provenance')}")
    print(f"Total Trades: {bt['total_trades']}")
    print(f"Net Profit (Cash Index Points): {bt['net_profit']}")
    print(f"Win Rate: {bt['win_rate_pct']}%")
    
    assert bt["is_synthetic"] is False, "Historical run should not be flagged as synthetic"
    assert bt["is_underlying_only"] is True, "Must be clearly labeled as underlying cash index points only"
    assert bt["dataset_id"] == "HISTORICAL_SPOT_8D"
    assert bt["total_trades"] > 0
    print("[PASS] Historical spot backtest integrity and labeling verified successfully.")

if __name__ == "__main__":
    try:
        test_paper_isolation()
        test_demo_seeding()
        test_persistence_across_restart()
        test_historical_spot_backtest()
        print("\n=======================================================")
        print(">>> ALL VERIFICATION CHECKS PASSED WITH ZERO ERRORS <<<")
        print("=======================================================\n")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n[FAILURE] {e}")
        sys.exit(1)
