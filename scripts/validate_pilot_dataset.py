"""Validate historical pilot dataset directory using OfflineDatasetValidator.

Usage:
    python scripts/validate_pilot_dataset.py [path_to_directory]
"""

import sys
from pathlib import Path
from src.data.validator import OfflineDatasetValidator


def main() -> None:
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/historical/pilot_20241024")
    print("=" * 80)
    print(f"VALIDATING PILOT DATASET DIRECTORY: {target_dir}")
    print("=" * 80)

    if not target_dir.exists():
        print(f"\n[ERROR] Directory does not exist: {target_dir}")
        print("Please place the historical CSV/Parquet files in this directory before validating.")
        sys.exit(1)

    validator = OfflineDatasetValidator()
    manifest, candles_by_symbol, contracts = validator.validate_directory(
        dir_path=target_dir,
        provenance="PILOT_HISTORICAL_ACQUISITION",
        is_synthetic=False,
    )

    print("\n--- DATASET MANIFEST ---")
    print(f"Dataset ID:          {manifest.dataset_id}")
    print(f"Fingerprint:         {manifest.fingerprint}")
    print(f"Is Synthetic:        {manifest.is_synthetic}")
    print(f"Usable for Replay:   {manifest.is_usable_for_backtest}")
    print(f"Total Bars:          {manifest.total_bars}")
    print(f"Trading Sessions:    {manifest.trading_sessions}")
    print(f"Underlying Symbols:  {manifest.underlying_symbols}")
    print(f"Option Symbols:      {manifest.option_symbols}")
    print(f"Lot Sizes:           {manifest.lot_sizes}")

    if manifest.validation_errors:
        print("\n--- VALIDATION ERRORS ---")
        for err in manifest.validation_errors:
            print(f"  [ERROR] {err}")

    if manifest.warnings:
        print("\n--- WARNINGS ---")
        for w in manifest.warnings:
            print(f"  [WARN] {w}")

    print("\n--- QUALITY REPORTS PER SYMBOL ---")
    for sym, q in manifest.quality_reports.items():
        print(f"Symbol: {sym:30} Bars: {q.total_records:5} Gaps: {q.gap_intervals:3} Anomalies: {q.anomalous_records:3} Duplicates: {q.duplicate_records:3} Usable: {q.is_usable_for_backtest}")

    if not manifest.is_usable_for_backtest:
        print("\n[VERDICT] DATASET REJECTED — Does not satisfy quality criteria for replay.")
        sys.exit(1)
    else:
        print("\n[VERDICT] DATASET ACCEPTED — Ready for discrete event backtest replay.")
        sys.exit(0)


if __name__ == "__main__":
    main()

