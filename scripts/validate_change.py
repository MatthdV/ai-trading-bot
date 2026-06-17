#!/usr/bin/env python3
"""
Two-Gate Validation Script — Gate 2 (Backtest)

Compare les métriques de backtest avant/après un changement de stratégie.
Utilisé par l'agent reviewer pour décider si un changement passe en production.

Usage:
    # Sauvegarder les métriques actuelles comme baseline
    python scripts/validate_change.py --save-baseline

    # Après modification, comparer avec la baseline
    python scripts/validate_change.py --compare

    # One-shot : afficher les métriques actuelles
    python scripts/validate_change.py --current
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from backtesting.engine import Backtester

BASELINE_FILE = Path(__file__).parent.parent / ".baseline_metrics.json"

# Seuils minimum (gate 2 du CLAUDE.md)
THRESHOLDS = {
    "sharpe_ratio": 1.0,
    "win_rate": 0.50,
    "max_drawdown": 0.15,     # ≤ 15%
    "profit_factor": 1.2,
    "total_trades": 10,       # minimum
}

SYMBOLS = ["AAPL", "MSFT", "GOOGL", "TSLA", "SPY"]
PERIOD = "2y"
CAPITAL = 10_000


def download_data() -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        print("pip install yfinance")
        sys.exit(1)

    frames = []
    for sym in SYMBOLS:
        hist = yf.Ticker(sym).history(period=PERIOD, interval="1d")
        if hist.empty:
            continue
        hist.columns = [c.lower() for c in hist.columns]
        hist = hist.rename(columns={"stock splits": "stock_splits"})
        hist["symbol"] = sym
        frames.append(hist)

    if not frames:
        sys.exit(1)

    return pd.concat(frames).sort_index()


def run_backtest(data: pd.DataFrame) -> dict:
    strategies = [MeanReversionStrategy(), MomentumStrategy({"filter_market_hours": False})]
    bt = Backtester(strategies=strategies, initial_capital=CAPITAL)
    bt.run(data)

    # Extract metrics from backtester
    metrics = bt.get_metrics() if hasattr(bt, "get_metrics") else {}

    # Fallback: parse from report if get_metrics not available
    if not metrics:
        report = bt.get_report() if hasattr(bt, "get_report") else {}
        metrics = {
            "sharpe_ratio": report.get("sharpe_ratio", 0),
            "win_rate": report.get("win_rate", 0),
            "max_drawdown": report.get("max_drawdown", 0),
            "profit_factor": report.get("profit_factor", 0),
            "total_trades": report.get("total_trades", 0),
            "total_return": report.get("total_return", 0),
        }

    return metrics


def save_baseline(metrics: dict) -> None:
    BASELINE_FILE.write_text(json.dumps(metrics, indent=2))
    print(f"Baseline saved to {BASELINE_FILE}")
    print_metrics("BASELINE", metrics)


def load_baseline() -> dict:
    if not BASELINE_FILE.exists():
        print("No baseline found. Run with --save-baseline first.")
        sys.exit(1)
    return json.loads(BASELINE_FILE.read_text())


def print_metrics(label: str, m: dict) -> None:
    print(f"\n{'─' * 50}")
    print(f"  {label}")
    print(f"{'─' * 50}")
    print(f"  Sharpe ratio  : {m.get('sharpe_ratio', 0):.2f}")
    print(f"  Win rate      : {m.get('win_rate', 0):.1%}")
    print(f"  Max drawdown  : {m.get('max_drawdown', 0):.1%}")
    print(f"  Profit factor : {m.get('profit_factor', 0):.2f}")
    print(f"  Total trades  : {m.get('total_trades', 0)}")
    print(f"  Total return  : {m.get('total_return', 0):.1%}")


def compare(before: dict, after: dict) -> bool:
    """Compare metrics. Returns True if change is approved."""
    print("\n" + "=" * 60)
    print("  TWO-GATE VALIDATION — Gate 2 (Backtest)")
    print("=" * 60)

    all_pass = True
    results = []

    for key, threshold in THRESHOLDS.items():
        val_before = before.get(key, 0)
        val_after = after.get(key, 0)

        if key == "max_drawdown":
            # Lower is better
            meets_threshold = val_after <= threshold
            improved = val_after <= val_before
            delta = val_before - val_after  # positive = improvement
        elif key == "total_trades":
            meets_threshold = val_after >= threshold
            improved = True  # no regression check on trade count
            delta = val_after - val_before
        else:
            # Higher is better
            meets_threshold = val_after >= threshold
            improved = val_after >= val_before
            delta = val_after - val_before

        status = "✅" if meets_threshold else "❌"
        trend = "↑" if delta > 0 else ("↓" if delta < 0 else "=")

        results.append((key, val_before, val_after, delta, trend, status, meets_threshold))

        if not meets_threshold:
            all_pass = False

    # Print comparison table
    print(f"\n  {'Metric':<16} {'Before':>8} {'After':>8} {'Delta':>8} {'Status':>6}")
    print(f"  {'─' * 50}")
    for key, vb, va, delta, trend, status, _ in results:
        if key in ("win_rate", "max_drawdown", "total_return"):
            print(f"  {key:<16} {vb:>7.1%} {va:>7.1%} {delta:>+7.1%} {trend} {status}")
        elif key == "total_trades":
            print(f"  {key:<16} {vb:>8.0f} {va:>8.0f} {delta:>+8.0f} {trend} {status}")
        else:
            print(f"  {key:<16} {vb:>8.2f} {va:>8.2f} {delta:>+8.2f} {trend} {status}")

    # Verdict
    print(f"\n  {'=' * 50}")
    if all_pass:
        print("  VERDICT: ✅ APPROVE — Toutes les métriques au-dessus des seuils")
    else:
        print("  VERDICT: ❌ REJECT — Une ou plusieurs métriques sous le seuil")
    print(f"  {'=' * 50}")

    return all_pass


def main():
    parser = argparse.ArgumentParser(description="Two-gate backtest validation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--save-baseline", action="store_true", help="Save current metrics as baseline")
    group.add_argument("--compare", action="store_true", help="Compare current vs baseline")
    group.add_argument("--current", action="store_true", help="Show current metrics only")
    args = parser.parse_args()

    print("Downloading data...")
    data = download_data()

    print("Running backtest...")
    metrics = run_backtest(data)

    if args.save_baseline:
        save_baseline(metrics)
    elif args.current:
        print_metrics("CURRENT", metrics)
    elif args.compare:
        baseline = load_baseline()
        approved = compare(baseline, metrics)
        sys.exit(0 if approved else 1)


if __name__ == "__main__":
    main()
