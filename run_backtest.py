#!/usr/bin/env python3
"""
Run a full backtest with MeanReversion + Momentum strategies.

Usage:
    python run_backtest.py
    python run_backtest.py --symbols AAPL MSFT TSLA --period 6mo --capital 10000
    python run_backtest.py --strategy mean_reversion
    python run_backtest.py --strategy momentum
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import pandas as pd

from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from backtesting.engine import Backtester


def download_data(
    symbols: list[str],
    period: str = "2y",
    interval: str = "1d",
) -> pd.DataFrame:
    """Download OHLCV data via yfinance and return a unified DataFrame."""
    try:
        import yfinance as yf
    except ImportError:
        print("yfinance is required: pip install yfinance")
        sys.exit(1)

    frames = []
    for sym in symbols:
        print(f"  Downloading {sym}...")
        ticker = yf.Ticker(sym)
        hist = ticker.history(period=period, interval=interval)
        if hist.empty:
            print(f"    WARNING: no data for {sym}")
            continue
        hist.columns = [c.lower() for c in hist.columns]
        hist = hist.rename(columns={"stock splits": "stock_splits"})
        hist["symbol"] = sym
        frames.append(hist)

    if not frames:
        print("No data downloaded.")
        sys.exit(1)

    return pd.concat(frames).sort_index()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trading strategy backtest")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["AAPL", "MSFT", "GOOGL", "TSLA", "SPY"],
        help="Ticker symbols to backtest",
    )
    parser.add_argument("--period", default="2y", help="yfinance period (1mo, 3mo, 1y, 2y)")
    parser.add_argument("--interval", default="1d", help="yfinance interval (1h, 1d)")
    parser.add_argument("--capital", type=float, default=10_000, help="Initial capital ($)")
    parser.add_argument(
        "--strategy",
        choices=["mean_reversion", "momentum", "both"],
        default="both",
        help="Strategy to run",
    )
    args = parser.parse_args()

    print("=" * 56)
    print("  AI TRADING BOT — BACKTEST")
    print("=" * 56)
    print(f"  Symbols    : {', '.join(args.symbols)}")
    print(f"  Period     : {args.period}")
    print(f"  Interval   : {args.interval}")
    print(f"  Capital    : ${args.capital:,.2f}")
    print(f"  Strategy   : {args.strategy}")
    print()

    # --- Download data ---
    print("Downloading historical data...")
    data = download_data(args.symbols, args.period, args.interval)
    print(f"  Total bars: {len(data)}")
    print()

    # --- Build strategies ---
    strategies = []
    if args.strategy in ("mean_reversion", "both"):
        strategies.append(MeanReversionStrategy())
    if args.strategy in ("momentum", "both"):
        strategies.append(MomentumStrategy({"filter_market_hours": False}))

    # --- Run backtest: individual strategies ---
    for strat in strategies:
        print(f"\n{'─' * 56}")
        print(f"  Strategy: {strat.name}")
        print(f"{'─' * 56}")
        bt = Backtester(strategies=[strat], initial_capital=args.capital)
        bt.run(data)
        bt.print_report()

    # --- Run combined backtest ---
    if len(strategies) > 1:
        print(f"\n{'─' * 56}")
        print(f"  COMBINED: {' + '.join(s.name for s in strategies)}")
        print(f"{'─' * 56}")
        bt_combined = Backtester(strategies=strategies, initial_capital=args.capital)
        bt_combined.run(data)
        bt_combined.print_report()

    print("\nDone.")


if __name__ == "__main__":
    main()
