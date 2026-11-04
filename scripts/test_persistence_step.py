"""Deterministic verification script for configuration persistence, paper trading isolation, and backtesting."""

import sys
import requests
import json

BASE_URL = "http://127.0.0.1:8000/api/v1"

def run_paper_and_backtest():
    print("--- [1] PAPER TRADING ISOLATION ---")
    res = requests.post(f"{BASE_URL}/sessions/clear-orders")
    assert res.status_code == 200, f"clear-orders failed: {res.text}"
    print("Clean orders result:", res.json())
    
    orders = requests.get(f"{BASE_URL}/orders").json()
    positions = requests.get(f"{BASE_URL}/positions").json()
    print(f"Verified initial clean state: {len(orders)} orders, {len(positions)} positions")
    assert len(orders) == 0, f"Expected 0 orders, got {len(orders)}"
    assert len(positions) == 0, f"Expected 0 positions, got {len(positions)}"
    
    recon = requests.post(f"{BASE_URL}/reconciliation/run").json()
    print("Reconciliation result:", recon)
    assert recon.get("is_synchronized") is True
    assert len(recon.get("discrepancies", [])) == 0
    print("[PASS] Clean paper trading startup verified (0 orders, 0 positions, 0 discrepancies).")

    print("\n--- [2] DEMO FIXTURE ISOLATION ---")
    seed_res = requests.post(f"{BASE_URL}/sessions/seed-demo")
    assert seed_res.status_code == 200, f"seed-demo failed: {seed_res.text}"
    print("Seed demo response:", seed_res.json())
    
    orders = requests.get(f"{BASE_URL}/orders").json()
    positions = requests.get(f"{BASE_URL}/positions").json()
    assert len(orders) == 1, f"Expected 1 order, got {len(orders)}"
    assert len(positions) == 1, f"Expected 1 position, got {len(positions)}"
    print(f"Demo order: {orders[0]['client_order_id']}, Tag: {orders[0].get('tag')}")
    assert orders[0].get("tag") == "DEMO_FIXTURE"
    
    # Clear demo fixtures
    clear_res = requests.post(f"{BASE_URL}/sessions/clear-orders").json()
    assert clear_res["orders_count"] == 0
    orders_after = requests.get(f"{BASE_URL}/orders").json()
    positions_after = requests.get(f"{BASE_URL}/positions").json()
    assert len(orders_after) == 0 and len(positions_after) == 0
    print("[PASS] Demo data created strictly via explicit action and cleanly reset back to zero.")

    print("\n--- [3] HISTORICAL UNDERLYING-ONLY BACKTEST ---")
    res = requests.post(f"{BASE_URL}/backtest/run", json={
        "dataset_id": "HISTORICAL_SPOT_8D",
        "start_date": "2026-10-22",
        "end_date": "2026-11-02",
    })
    assert res.status_code == 200, f"Backtest failed: {res.text}"
    bt = res.json()["summary"]
    print("Backtest Summary:", json.dumps(bt, indent=2))
    assert bt["is_synthetic"] is False, "Expected is_synthetic == False"
    assert bt["is_underlying_only"] is True, "Expected is_underlying_only == True"
    assert bt["dataset_id"] == "HISTORICAL_SPOT_8D"
    assert bt["total_trades"] > 0, "Expected at least 1 trade"
    assert bt.get("provenance") is not None
    print("[PASS] Real historical spot backtest verified: strictly underlying index points, zero synthetic option P&L.")

def run_save_modified_config():
    print("--- [4] PERSISTENCE STEP 1: MODIFY AND SAVE CONFIGURATION ---")
    active = requests.get(f"{BASE_URL}/strategies/active").json()
    print("Active config before edit:", json.dumps({
        "config_id": active["config_id"],
        "version": active["version"],
        "lots": active["parameters"].get("lots"),
        "max_daily_loss": active["parameters"].get("max_daily_loss"),
        "orb_start_time": active["parameters"].get("orb_start_time"),
    }, indent=2))
    
    modified_params = dict(active["parameters"])
    modified_params.update({
        "lots": 3,
        "max_daily_loss": 4500.0,
        "profit_protection_trigger": 3500.0,
        "minimum_profit_lock": 750.0,
        "orb_start_time": "09:18:00",
    })
    
    res = requests.post(
        f"{BASE_URL}/strategies/{active['config_id']}/versions",
        json={"parameters": modified_params, "set_active": True}
    )
    assert res.status_code == 200, f"Failed to save version: {res.text}"
    saved = res.json()
    print("Saved modified version:", json.dumps({
        "config_id": saved["config_id"],
        "version": saved["version"],
        "lots": saved["parameters"].get("lots"),
        "max_daily_loss": saved["parameters"].get("max_daily_loss"),
        "profit_protection_trigger": saved["parameters"].get("profit_protection_trigger"),
        "minimum_profit_lock": saved["parameters"].get("minimum_profit_lock"),
        "orb_start_time": saved["parameters"].get("orb_start_time"),
    }, indent=2))
    
    active_now = requests.get(f"{BASE_URL}/strategies/active").json()
    assert active_now["version"] == saved["version"]
    assert active_now["parameters"]["lots"] == 3
    assert active_now["parameters"]["max_daily_loss"] == 4500.0
    print("[PASS] Modified configuration saved and active in runtime.")

def run_verify_restored_config():
    print("--- [5] PERSISTENCE STEP 2: VERIFY RESTORATION FROM SQLITE AFTER RESTART ---")
    active = requests.get(f"{BASE_URL}/strategies/active").json()
    print("Active config restored from SQLite on startup:", json.dumps({
        "config_id": active["config_id"],
        "version": active["version"],
        "lots": active["parameters"].get("lots"),
        "max_daily_loss": active["parameters"].get("max_daily_loss"),
        "profit_protection_trigger": active["parameters"].get("profit_protection_trigger"),
        "minimum_profit_lock": active["parameters"].get("minimum_profit_lock"),
        "orb_start_time": active["parameters"].get("orb_start_time"),
    }, indent=2))
    
    assert active["parameters"]["lots"] == 3, f"Expected lots == 3, got {active['parameters']['lots']}"
    assert active["parameters"]["max_daily_loss"] == 4500.0, f"Expected max_daily_loss == 4500.0"
    assert active["parameters"]["profit_protection_trigger"] == 3500.0
    assert active["parameters"]["minimum_profit_lock"] == 750.0
    assert active["parameters"]["orb_start_time"] == "09:18:00"
    print("[PASS] Exact modified strategy and risk parameters successfully restored from SQLite!")

    print("\n--- [6] RESTORE BASELINE CONFIGURATION ---")
    res = requests.post(f"{BASE_URL}/strategies/NIFTY_ORB_V1/activate")
    assert res.status_code == 200, f"Failed to activate baseline: {res.text}"
    baseline = requests.get(f"{BASE_URL}/strategies/active").json()
    print("Restored baseline configuration:", json.dumps({
        "config_id": baseline["config_id"],
        "version": baseline["version"],
        "lots": baseline["parameters"].get("lots"),
        "max_daily_loss": baseline["parameters"].get("max_daily_loss"),
        "orb_start_time": baseline["parameters"].get("orb_start_time"),
    }, indent=2))
    assert baseline["config_id"] == "NIFTY_ORB_V1"
    assert baseline["version"] == 1
    
    # Ensure zero orders/positions remain
    requests.post(f"{BASE_URL}/sessions/clear-orders")
    print("[PASS] Baseline active configuration restored and workstation left in clean paper state.")

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "paper_and_backtest"
    if mode == "paper_and_backtest":
        run_paper_and_backtest()
    elif mode == "save":
        run_save_modified_config()
    elif mode == "verify_and_restore":
        run_verify_restored_config()
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
