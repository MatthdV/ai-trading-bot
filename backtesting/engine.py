#!/usr/bin/env python3
"""
Backtesting Engine — Simulates strategy execution on historical OHLCV data.

Supports running one or more strategies simultaneously, simulating commissions,
slippage, and producing a comprehensive performance report.

Author: Matthieu de Villele
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Sequence

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Direction, Position, Signal

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """Immutable record of a completed round-trip trade."""
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_pct: float
    commission: float
    slippage: float
    strategy_name: str
    exit_reason: str = ""


class Backtester:
    """
    Event-driven backtesting engine.

    Iterates bar-by-bar over historical OHLCV data, feeds each candle
    window to every registered strategy, manages a virtual portfolio,
    and records equity-curve and trade-level statistics.

    Usage::

        bt = Backtester(
            strategies=[MeanReversionStrategy(), MomentumStrategy()],
            initial_capital=10_000,
        )
        bt.run(candles_df, start_date="2024-01-01", end_date="2026-03-01")
        results = bt.get_results()
        bt.print_report()
    """

    def __init__(
        self,
        strategies: Sequence[BaseStrategy],
        initial_capital: float = 10_000.0,
        commission_pct: float = 0.001,   # 0.1 %
        slippage_pct: float = 0.0005,    # 0.05 %
    ) -> None:
        self.strategies = list(strategies)
        self.initial_capital = initial_capital
        self.commission_pct = commission_pct
        self.slippage_pct = slippage_pct

        # Internal state — reset on each run()
        self._cash: float = initial_capital
        self._positions: dict[str, Position] = {}  # symbol -> Position
        self._trades: list[TradeRecord] = []
        self._equity_curve: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        candles: pd.DataFrame,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None:
        """
        Run the backtest.

        Args:
            candles: DataFrame with columns ``open, high, low, close, volume``
                     and a ``symbol`` column (for multi-asset) **or** a single-
                     asset frame. Must have a DatetimeIndex or a ``timestamp``
                     column.
            start_date: Optional ISO start date filter.
            end_date: Optional ISO end date filter.
        """
        self._reset()

        # Normalise index
        df = candles.copy()
        if "timestamp" in df.columns:
            df.index = pd.to_datetime(df["timestamp"])
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        df = df.sort_index()

        if start_date:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date)]

        if df.empty:
            logger.warning("No data after date filtering")
            return

        # Detect multi-asset vs single-asset
        symbols = df["symbol"].unique().tolist() if "symbol" in df.columns else ["UNKNOWN"]
        is_multi = "symbol" in df.columns

        logger.info(
            f"Backtest: {len(self.strategies)} strategies, "
            f"{len(symbols)} symbols, {len(df)} bars"
        )

        # --- Main loop: iterate bar by bar ---
        dates = df.index.unique().sort_values()
        lookback = max(s.config.get("min_candles", 60) for s in self.strategies)

        for i, current_date in enumerate(dates):
            if i < lookback:
                continue

            # Build the lookback window for each symbol
            window_start = dates[max(0, i - lookback)]
            window = df[window_start:current_date]

            for sym in symbols:
                sym_candles = window[window["symbol"] == sym] if is_multi else window
                if len(sym_candles) < 20:
                    continue

                current_price = float(sym_candles["close"].iloc[-1])

                for strategy in self.strategies:
                    signal = strategy.analyze(sym, sym_candles)

                    pos_key = f"{sym}_{strategy.name}"

                    # --- Check exits first ---
                    if pos_key in self._positions:
                        pos = self._positions[pos_key]
                        if strategy.should_exit(signal, pos):
                            self._close_position(pos_key, current_price, current_date, "strategy_exit")
                        # Also check hard stop / take-profit from Position itself
                        elif pos.stop_loss and current_price <= pos.stop_loss and pos.is_long:
                            self._close_position(pos_key, pos.stop_loss, current_date, "stop_loss")
                        elif pos.take_profit and current_price >= pos.take_profit and pos.is_long:
                            self._close_position(pos_key, pos.take_profit, current_date, "take_profit")
                        elif pos.stop_loss and current_price >= pos.stop_loss and not pos.is_long:
                            self._close_position(pos_key, pos.stop_loss, current_date, "stop_loss")
                        elif pos.take_profit and current_price <= pos.take_profit and not pos.is_long:
                            self._close_position(pos_key, pos.take_profit, current_date, "take_profit")

                    # --- Check entries ---
                    elif strategy.should_enter(signal):
                        alloc = strategy.calculate_position_size(signal, self._portfolio_value(current_price))
                        self._open_position(
                            symbol=sym,
                            direction=signal.direction,
                            alloc_dollars=alloc,
                            price=current_price,
                            timestamp=current_date,
                            strategy_name=strategy.name,
                            pos_key=pos_key,
                        )

            # Record equity
            self._equity_curve.append({
                "date": current_date,
                "equity": self._portfolio_value(
                    self._last_prices(df, current_date, symbols, is_multi)
                ),
            })

        # Close remaining positions at last available prices
        last_date = dates[-1]
        for pos_key in list(self._positions.keys()):
            sym = self._positions[pos_key].symbol
            sym_df = df[df["symbol"] == sym] if is_multi else df
            if not sym_df.empty:
                last_price = float(sym_df["close"].iloc[-1])
                self._close_position(pos_key, last_price, last_date, "end_of_backtest")

        logger.info(f"Backtest complete: {len(self._trades)} trades")

    def get_results(self) -> dict[str, Any]:
        """Return a dict of performance metrics."""
        if not self._equity_curve:
            return {}

        eq = pd.DataFrame(self._equity_curve).set_index("date")["equity"]

        total_return = (eq.iloc[-1] - self.initial_capital) / self.initial_capital

        # Daily returns
        daily_ret = eq.pct_change().dropna()

        # Sharpe ratio (annualised, risk-free = 0)
        if len(daily_ret) > 1 and daily_ret.std() > 0:
            sharpe = (daily_ret.mean() / daily_ret.std()) * math.sqrt(252)
        else:
            sharpe = 0.0

        # Max drawdown
        cummax = eq.cummax()
        drawdown = (cummax - eq) / cummax
        max_dd = float(drawdown.max())

        # Trade stats
        trades = self._trades
        num_trades = len(trades)
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        win_rate = len(winners) / num_trades if num_trades else 0.0

        total_wins = sum(t.pnl for t in winners)
        total_losses = abs(sum(t.pnl for t in losers))
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        # Average trade duration
        if trades:
            durations = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in trades]
            avg_duration_h = sum(durations) / len(durations)
        else:
            avg_duration_h = 0.0

        return {
            "initial_capital": self.initial_capital,
            "final_equity": float(eq.iloc[-1]),
            "total_return_pct": round(total_return * 100, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "win_rate_pct": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2),
            "num_trades": num_trades,
            "avg_trade_duration_hours": round(avg_duration_h, 1),
            "total_commissions": round(sum(t.commission for t in trades), 2),
            "equity_curve": eq,
        }

    def print_report(self) -> None:
        """Print a formatted performance summary."""
        r = self.get_results()
        if not r:
            print("No results — run a backtest first.")
            return

        print()
        print("=" * 56)
        print("  BACKTEST REPORT")
        print("=" * 56)
        strats = ", ".join(s.name for s in self.strategies)
        print(f"  Strategies      : {strats}")
        print(f"  Initial Capital  : ${r['initial_capital']:>12,.2f}")
        print(f"  Final Equity     : ${r['final_equity']:>12,.2f}")
        print(f"  Total Return     : {r['total_return_pct']:>12.2f} %")
        print(f"  Sharpe Ratio     : {r['sharpe_ratio']:>12.3f}")
        print(f"  Max Drawdown     : {r['max_drawdown_pct']:>12.2f} %")
        print(f"  Win Rate         : {r['win_rate_pct']:>12.1f} %")
        print(f"  Profit Factor    : {r['profit_factor']:>12.2f}")
        print(f"  Num Trades       : {r['num_trades']:>12}")
        print(f"  Avg Duration (h) : {r['avg_trade_duration_hours']:>12.1f}")
        print(f"  Total Commissions: ${r['total_commissions']:>12.2f}")
        print("=" * 56)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset(self) -> None:
        self._cash = self.initial_capital
        self._positions = {}
        self._trades = []
        self._equity_curve = []

    def _portfolio_value(self, price_or_prices: float | dict[str, float] = 0.0) -> float:
        """Total portfolio value (cash + open positions)."""
        value = self._cash
        for key, pos in self._positions.items():
            if isinstance(price_or_prices, dict):
                p = price_or_prices.get(pos.symbol, pos.entry_price)
            else:
                p = price_or_prices
            if pos.is_long:
                value += pos.quantity * p
            else:
                # Short P&L
                value += pos.quantity * (2 * pos.entry_price - p)
        return value

    def _last_prices(
        self,
        df: pd.DataFrame,
        as_of: datetime,
        symbols: list[str],
        is_multi: bool,
    ) -> dict[str, float]:
        """Get the last known close for each symbol up to *as_of*."""
        prices: dict[str, float] = {}
        subset = df[df.index <= as_of]
        for sym in symbols:
            sym_df = subset[subset["symbol"] == sym] if is_multi else subset
            if not sym_df.empty:
                prices[sym] = float(sym_df["close"].iloc[-1])
        return prices

    def _apply_slippage(self, price: float, direction: Direction) -> float:
        """Worsen the fill price by the slippage percentage."""
        if direction == Direction.LONG:
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def _open_position(
        self,
        symbol: str,
        direction: Direction,
        alloc_dollars: float,
        price: float,
        timestamp: datetime,
        strategy_name: str,
        pos_key: str,
    ) -> None:
        fill_price = self._apply_slippage(price, direction)
        commission = alloc_dollars * self.commission_pct
        available = alloc_dollars - commission

        if available <= 0 or available > self._cash:
            return

        quantity = available / fill_price
        self._cash -= available + commission

        pos = Position(
            symbol=symbol,
            direction=direction,
            entry_price=fill_price,
            quantity=quantity,
            entry_time=timestamp,
        )
        self._positions[pos_key] = pos
        logger.debug(f"OPEN {pos_key}: {direction.value} {quantity:.2f} @ {fill_price:.2f}")

    def _close_position(
        self,
        pos_key: str,
        price: float,
        timestamp: datetime,
        reason: str,
    ) -> None:
        if pos_key not in self._positions:
            return

        pos = self._positions.pop(pos_key)
        exit_dir = Direction.SHORT if pos.is_long else Direction.LONG
        fill_price = self._apply_slippage(price, exit_dir)

        proceeds = pos.quantity * fill_price
        commission = proceeds * self.commission_pct

        if pos.is_long:
            pnl = (fill_price - pos.entry_price) * pos.quantity - commission
        else:
            pnl = (pos.entry_price - fill_price) * pos.quantity - commission

        pnl_pct = pnl / (pos.entry_price * pos.quantity) if pos.entry_price else 0.0

        self._cash += proceeds - commission

        trade = TradeRecord(
            symbol=pos.symbol,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=fill_price,
            quantity=pos.quantity,
            entry_time=pos.entry_time,
            exit_time=timestamp,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission * 2,  # entry + exit
            slippage=abs(fill_price - price) * pos.quantity,
            strategy_name=pos_key.split("_", 1)[1] if "_" in pos_key else "",
            exit_reason=reason,
        )
        self._trades.append(trade)
        logger.debug(
            f"CLOSE {pos_key}: {fill_price:.2f}, PnL={pnl:+.2f} ({pnl_pct:+.2%}), reason={reason}"
        )
