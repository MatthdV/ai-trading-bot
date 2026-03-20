#!/usr/bin/env python3
"""
Parameter optimization for trading strategies.
Tests multiple parameter sets and reports results.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from backtesting.engine import Backtester


def download_data(symbols: list[str], period: str = "2y") -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for sym in symbols:
        hist = yf.Ticker(sym).history(period=period, interval="1d")
        if hist.empty:
            continue
        hist.columns = [c.lower() for c in hist.columns]
        hist = hist.rename(columns={"stock splits": "stock_splits"})
        hist["symbol"] = sym
        frames.append(hist)

    return pd.concat(frames).sort_index()


# ── Parameter sets to test ──────────────────────────────────────

MEAN_REVERSION_CONFIGS = {
    "baseline": {},  # CLASS_DEFAULTS
    "loose_entry": {
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        "zscore_entry_threshold": 1.5,
        "bb_std": 1.5,
    },
    "wider_stops": {
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        "zscore_entry_threshold": 1.5,
        "bb_std": 1.5,
        "stop_loss_pct": 0.04,
        "timeout_hours": 120.0,
        "zscore_exit_low": -0.3,
        "zscore_exit_high": 0.3,
    },
    "aggressive": {
        "rsi_oversold": 40.0,
        "rsi_overbought": 60.0,
        "zscore_entry_threshold": 1.0,
        "bb_std": 1.5,
        "stop_loss_pct": 0.05,
        "timeout_hours": 168.0,
        "max_allocation_pct": 0.15,
        "zscore_exit_low": -0.3,
        "zscore_exit_high": 0.3,
    },
    "max_signals": {
        "rsi_oversold": 45.0,
        "rsi_overbought": 55.0,
        "zscore_entry_threshold": 0.8,
        "bb_std": 1.2,
        "stop_loss_pct": 0.05,
        "timeout_hours": 168.0,
        "max_allocation_pct": 0.20,
        "zscore_exit_low": -0.2,
        "zscore_exit_high": 0.2,
        "min_candles": 30,
    },
}

MOMENTUM_CONFIGS = {
    "baseline": {"filter_market_hours": False},
    "loose_entry": {
        "filter_market_hours": False,
        "adx_entry_threshold": 20.0,
        "volume_min_relative": 1.2,
    },
    "wider_stops": {
        "filter_market_hours": False,
        "adx_entry_threshold": 20.0,
        "volume_min_relative": 1.2,
        "atr_trailing_multiplier": 2.5,
        "adx_exit_threshold": 15.0,
    },
    "aggressive": {
        "filter_market_hours": False,
        "adx_entry_threshold": 18.0,
        "adx_exit_threshold": 14.0,
        "volume_min_relative": 1.0,
        "atr_trailing_multiplier": 2.5,
        "max_allocation_pct": 0.15,
    },
    "max_signals": {
        "filter_market_hours": False,
        "adx_entry_threshold": 15.0,
        "adx_exit_threshold": 12.0,
        "volume_min_relative": 0.8,
        "atr_trailing_multiplier": 3.0,
        "max_allocation_pct": 0.20,
        "min_candles": 40,
    },
}


def run_single(
    name: str,
    strategies: list,
    data: pd.DataFrame,
    capital: float,
) -> dict:
    bt = Backtester(strategies=strategies, initial_capital=capital)
    bt.run(data)
    r = bt.get_results()
    if not r:
        return {"name": name, "return": 0, "sharpe": 0, "trades": 0, "win_rate": 0, "pf": 0, "dd": 0}
    return {
        "name": name,
        "return": r["total_return_pct"],
        "sharpe": r["sharpe_ratio"],
        "trades": r["num_trades"],
        "win_rate": r["win_rate_pct"],
        "pf": r["profit_factor"],
        "dd": r["max_drawdown_pct"],
    }


def main() -> None:
    symbols = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "META", "AMD", "AMZN", "SPY", "QQQ"]
    capital = 10_000.0

    print("Downloading data for 10 symbols (2y)...")
    data = download_data(symbols, "2y")
    print(f"  {len(data)} bars loaded\n")

    # ── Mean Reversion sweep ────────────────────────────────────
    print("=" * 80)
    print("  MEAN REVERSION — PARAMETER SWEEP")
    print("=" * 80)
    print(f"{'Config':<16} {'Return%':>8} {'Sharpe':>8} {'Trades':>7} {'WinRate%':>9} {'PF':>6} {'MaxDD%':>7}")
    print("-" * 80)

    mr_results = []
    for cfg_name, overrides in MEAN_REVERSION_CONFIGS.items():
        strat = MeanReversionStrategy(overrides if overrides else None)
        r = run_single(cfg_name, [strat], data, capital)
        mr_results.append(r)
        print(
            f"{r['name']:<16} {r['return']:>8.2f} {r['sharpe']:>8.3f} "
            f"{r['trades']:>7} {r['win_rate']:>9.1f} {r['pf']:>6.2f} {r['dd']:>7.2f}"
        )

    # ── Momentum sweep ──────────────────────────────────────────
    print()
    print("=" * 80)
    print("  MOMENTUM — PARAMETER SWEEP")
    print("=" * 80)
    print(f"{'Config':<16} {'Return%':>8} {'Sharpe':>8} {'Trades':>7} {'WinRate%':>9} {'PF':>6} {'MaxDD%':>7}")
    print("-" * 80)

    mom_results = []
    for cfg_name, overrides in MOMENTUM_CONFIGS.items():
        strat = MomentumStrategy(overrides)
        r = run_single(cfg_name, [strat], data, capital)
        mom_results.append(r)
        print(
            f"{r['name']:<16} {r['return']:>8.2f} {r['sharpe']:>8.3f} "
            f"{r['trades']:>7} {r['win_rate']:>9.1f} {r['pf']:>6.2f} {r['dd']:>7.2f}"
        )

    # ── Refined configs based on sweep insights ──────────────────
    print()
    print("=" * 80)
    print("  REFINED CONFIGS — TARGETED OPTIMIZATION")
    print("=" * 80)
    print(f"{'Config':<24} {'Return%':>8} {'Sharpe':>8} {'Trades':>7} {'WinRate%':>9} {'PF':>6} {'MaxDD%':>7}")
    print("-" * 80)

    refined_configs = [
        (
            "MR:tuned+Mom:tuned",
            [
                MeanReversionStrategy({
                    "rsi_oversold": 38.0,
                    "rsi_overbought": 62.0,
                    "zscore_entry_threshold": 1.3,
                    "bb_std": 1.5,
                    "stop_loss_pct": 0.04,
                    "timeout_hours": 120.0,
                    "max_allocation_pct": 0.12,
                    "zscore_exit_low": -0.3,
                    "zscore_exit_high": 0.3,
                }),
                MomentumStrategy({
                    "filter_market_hours": False,
                    "adx_entry_threshold": 15.0,
                    "adx_exit_threshold": 12.0,
                    "volume_min_relative": 0.8,
                    "atr_trailing_multiplier": 3.0,
                    "max_allocation_pct": 0.18,
                    "min_candles": 40,
                }),
            ],
        ),
        (
            "MR:moderate+Mom:max",
            [
                MeanReversionStrategy({
                    "rsi_oversold": 35.0,
                    "rsi_overbought": 65.0,
                    "zscore_entry_threshold": 1.5,
                    "bb_std": 1.5,
                    "stop_loss_pct": 0.03,
                    "timeout_hours": 96.0,
                    "max_allocation_pct": 0.10,
                    "zscore_exit_low": -0.3,
                    "zscore_exit_high": 0.3,
                }),
                MomentumStrategy({
                    "filter_market_hours": False,
                    "adx_entry_threshold": 15.0,
                    "adx_exit_threshold": 12.0,
                    "volume_min_relative": 0.8,
                    "atr_trailing_multiplier": 3.0,
                    "max_allocation_pct": 0.20,
                    "min_candles": 40,
                }),
            ],
        ),
        (
            "Mom:only-max-alloc",
            [
                MomentumStrategy({
                    "filter_market_hours": False,
                    "adx_entry_threshold": 15.0,
                    "adx_exit_threshold": 12.0,
                    "volume_min_relative": 0.8,
                    "atr_trailing_multiplier": 3.0,
                    "max_allocation_pct": 0.25,
                    "min_candles": 40,
                }),
            ],
        ),
        (
            "MR:quality+Mom:quality",
            [
                MeanReversionStrategy({
                    "rsi_oversold": 35.0,
                    "rsi_overbought": 65.0,
                    "zscore_entry_threshold": 1.5,
                    "bb_std": 1.5,
                    "stop_loss_pct": 0.05,
                    "timeout_hours": 168.0,
                    "max_allocation_pct": 0.15,
                    "zscore_exit_low": -0.2,
                    "zscore_exit_high": 0.2,
                    "weight_rsi": 0.40,
                    "weight_bb": 0.30,
                    "weight_zscore": 0.30,
                }),
                MomentumStrategy({
                    "filter_market_hours": False,
                    "adx_entry_threshold": 18.0,
                    "adx_exit_threshold": 14.0,
                    "volume_min_relative": 1.0,
                    "atr_trailing_multiplier": 2.5,
                    "max_allocation_pct": 0.20,
                }),
            ],
        ),
    ]

    refined_results = []
    for cfg_name, strats in refined_configs:
        r = run_single(cfg_name, strats, data, capital)
        refined_results.append((cfg_name, strats, r))
        print(
            f"{r['name']:<24} {r['return']:>8.2f} {r['sharpe']:>8.3f} "
            f"{r['trades']:>7} {r['win_rate']:>9.1f} {r['pf']:>6.2f} {r['dd']:>7.2f}"
        )

    # Find best refined by Sharpe (quality metric, not just return)
    best_refined_name, best_refined_strats, best_refined_r = max(
        refined_results, key=lambda x: x[2]["sharpe"]
    )

    print()
    print("=" * 80)
    print(f"  BEST REFINED: {best_refined_name} (Sharpe={best_refined_r['sharpe']:.3f})")
    print("=" * 80)

    bt = Backtester(strategies=best_refined_strats, initial_capital=capital)
    bt.run(data)
    bt.print_report()

    # Monthly breakdown
    r = bt.get_results()
    if r and "equity_curve" in r:
        eq = r["equity_curve"]
        monthly = eq.resample("M").last()
        monthly_ret = monthly.pct_change().dropna() * 100
        print("\n  MONTHLY RETURNS:")
        print("  " + "-" * 40)
        for date, ret in monthly_ret.items():
            bar = "+" * int(max(0, ret)) + "-" * int(max(0, -ret))
            print(f"  {date.strftime('%Y-%m')}:  {ret:>+7.2f}%  {bar}")
        print(f"\n  Avg monthly: {monthly_ret.mean():>+.2f}%")
        print(f"  Best month:  {monthly_ret.max():>+.2f}%")
        print(f"  Worst month: {monthly_ret.min():>+.2f}%")
        positive_months = (monthly_ret > 0).sum()
        total_months = len(monthly_ret)
        print(f"  Positive months: {positive_months}/{total_months} ({positive_months/total_months*100:.0f}%)")

    # ── Also show best sweep combined for comparison ────────────
    best_mr = max(mr_results, key=lambda x: x["return"])
    best_mom = max(mom_results, key=lambda x: x["return"])

    print()
    print("=" * 80)
    print(f"  BEST COMBINED: MR={best_mr['name']} + Mom={best_mom['name']}")
    print("=" * 80)

    mr_cfg = MEAN_REVERSION_CONFIGS[best_mr["name"]]
    mom_cfg = MOMENTUM_CONFIGS[best_mom["name"]]

    strats = [
        MeanReversionStrategy(mr_cfg if mr_cfg else None),
        MomentumStrategy(mom_cfg),
    ]
    bt = Backtester(strategies=strats, initial_capital=capital)
    bt.run(data)
    bt.print_report()

    # Monthly breakdown
    r = bt.get_results()
    if r and "equity_curve" in r:
        eq = r["equity_curve"]
        monthly = eq.resample("M").last()
        monthly_ret = monthly.pct_change().dropna() * 100
        print("\n  MONTHLY RETURNS:")
        print("  " + "-" * 40)
        for date, ret in monthly_ret.items():
            bar = "+" * int(max(0, ret)) + "-" * int(max(0, -ret))
            print(f"  {date.strftime('%Y-%m')}:  {ret:>+7.2f}%  {bar}")
        print(f"\n  Avg monthly: {monthly_ret.mean():>+.2f}%")
        print(f"  Best month:  {monthly_ret.max():>+.2f}%")
        print(f"  Worst month: {monthly_ret.min():>+.2f}%")


if __name__ == "__main__":
    main()
