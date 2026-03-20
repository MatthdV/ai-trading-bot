#!/usr/bin/env python3
"""
Mean Reversion Strategy — RSI + Bollinger Bands + Z-Score.

Identifies oversold / overbought conditions using three complementary
indicators and trades the expected return to the mean.

Author: Matthieu de Villele
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Direction, Position, Signal

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    Mean-reversion strategy combining:
      - RSI (Relative Strength Index) for overbought / oversold detection
      - Bollinger Bands for volatility-adjusted price extremes
      - Z-score of the price relative to its moving average

    Entry:
      LONG  when RSI < 30 AND price below lower Bollinger AND z-score < -2
      SHORT when RSI > 70 AND price above upper Bollinger AND z-score > 2

    Exit:
      Take-profit when z-score reverts to [-0.5, 0.5]
      Stop-loss at 2 % from entry
      Timeout after 48 h if no convergence
    """

    CLASS_DEFAULTS: dict[str, Any] = {
        # RSI
        "rsi_period": 14,
        "rsi_oversold": 35.0,
        "rsi_overbought": 65.0,
        # Bollinger Bands
        "bb_period": 20,
        "bb_std": 1.5,
        # Z-score
        "zscore_period": 20,
        "zscore_entry_threshold": 1.5,
        "zscore_exit_low": -0.2,
        "zscore_exit_high": 0.2,
        # Risk
        "stop_loss_pct": 0.05,
        "timeout_hours": 168.0,
        # Sizing
        "max_allocation_pct": 0.15,
        # Minimum candles required to compute all indicators
        "min_candles": 50,
        # Strength weights (RSI, Bollinger, Z-score)
        "weight_rsi": 0.40,
        "weight_bb": 0.30,
        "weight_zscore": 0.30,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        merged = {**self.CLASS_DEFAULTS, **(config or {})}
        super().__init__(merged)

    # ------------------------------------------------------------------
    # Indicator helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _rsi(closes: pd.Series, period: int) -> pd.Series:
        """Compute RSI over *closes* using Wilder's smoothing."""
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _bollinger_bands(
        closes: pd.Series, period: int, num_std: float
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Return (middle, upper, lower) Bollinger Bands."""
        sma = closes.rolling(window=period, min_periods=period).mean()
        std = closes.rolling(window=period, min_periods=period).std(ddof=0)
        upper = sma + num_std * std
        lower = sma - num_std * std
        return sma, upper, lower

    @staticmethod
    def _zscore(closes: pd.Series, period: int) -> pd.Series:
        """Z-score of the current price relative to the rolling mean."""
        sma = closes.rolling(window=period, min_periods=period).mean()
        std = closes.rolling(window=period, min_periods=period).std(ddof=0)
        return (closes - sma) / std.replace(0, np.nan)

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def analyze(self, symbol: str, candles: pd.DataFrame) -> Signal:
        """Compute indicators and emit a Signal."""
        cfg = self.config
        min_candles = cfg["min_candles"]

        # Validate input
        if candles is None or len(candles) < min_candles:
            logger.warning(
                f"[{self.name}] {symbol}: insufficient data "
                f"({0 if candles is None else len(candles)} < {min_candles})"
            )
            return Signal(
                symbol=symbol,
                direction=Direction.NEUTRAL,
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "insufficient_data"},
            )

        closes = candles["close"].astype(float)

        # Compute indicators on the full series
        rsi_series = self._rsi(closes, cfg["rsi_period"])
        bb_mid, bb_upper, bb_lower = self._bollinger_bands(
            closes, cfg["bb_period"], cfg["bb_std"]
        )
        zscore_series = self._zscore(closes, cfg["zscore_period"])

        # Current values (last row)
        rsi = rsi_series.iloc[-1]
        zscore = zscore_series.iloc[-1]
        price = closes.iloc[-1]
        bb_up = bb_upper.iloc[-1]
        bb_lo = bb_lower.iloc[-1]
        bb_m = bb_mid.iloc[-1]

        # Handle NaN from insufficient rolling window
        if any(np.isnan(v) for v in (rsi, zscore, bb_up, bb_lo)):
            return Signal(
                symbol=symbol,
                direction=Direction.NEUTRAL,
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "nan_indicators"},
            )

        # --- Determine direction and strength ---
        direction = Direction.NEUTRAL
        strength = 0.0

        w_rsi = cfg["weight_rsi"]
        w_bb = cfg["weight_bb"]
        w_zs = cfg["weight_zscore"]
        zs_thresh = cfg["zscore_entry_threshold"]

        # LONG conditions
        if (
            rsi < cfg["rsi_oversold"]
            and price < bb_lo
            and zscore < -zs_thresh
        ):
            direction = Direction.LONG
            # Component strengths (0-1 each)
            rsi_str = min(1.0, (cfg["rsi_oversold"] - rsi) / cfg["rsi_oversold"])
            bb_str = min(1.0, (bb_lo - price) / (bb_up - bb_lo)) if bb_up != bb_lo else 0.5
            zs_str = min(1.0, (abs(zscore) - zs_thresh) / zs_thresh) if zs_thresh else 0.5
            strength = w_rsi * rsi_str + w_bb * bb_str + w_zs * zs_str

        # SHORT conditions
        elif (
            rsi > cfg["rsi_overbought"]
            and price > bb_up
            and zscore > zs_thresh
        ):
            direction = Direction.SHORT
            rsi_str = min(1.0, (rsi - cfg["rsi_overbought"]) / (100 - cfg["rsi_overbought"]))
            bb_str = min(1.0, (price - bb_up) / (bb_up - bb_lo)) if bb_up != bb_lo else 0.5
            zs_str = min(1.0, (zscore - zs_thresh) / zs_thresh) if zs_thresh else 0.5
            strength = w_rsi * rsi_str + w_bb * bb_str + w_zs * zs_str

        strength = max(0.0, min(1.0, strength))

        metadata = {
            "rsi": round(float(rsi), 2),
            "zscore": round(float(zscore), 4),
            "price": round(float(price), 4),
            "bb_upper": round(float(bb_up), 4),
            "bb_lower": round(float(bb_lo), 4),
            "bb_mid": round(float(bb_m), 4),
        }

        return Signal(
            symbol=symbol,
            direction=direction,
            strength=strength,
            strategy_name=self.name,
            metadata=metadata,
        )

    def should_enter(self, signal: Signal) -> bool:
        """Enter when signal is actionable with non-trivial strength."""
        return signal.is_actionable and signal.strength >= 0.3

    def should_exit(self, signal: Signal, position: Position) -> bool:
        """
        Exit on:
          1. Take-profit — z-score reverts to the mean band
          2. Stop-loss — price moved 2 % against the position
          3. Timeout — position open longer than 48 h
        """
        cfg = self.config
        zscore = signal.metadata.get("zscore", 0.0)
        price = signal.metadata.get("price", 0.0)

        # 1. Take profit: z-score back to mean
        if cfg["zscore_exit_low"] <= zscore <= cfg["zscore_exit_high"]:
            logger.info(
                f"[{self.name}] {position.symbol}: TP hit "
                f"(z-score={zscore:.2f} in exit band)"
            )
            return True

        # 2. Stop-loss
        pnl_pct = position.unrealized_pnl_pct(price)
        if pnl_pct <= -cfg["stop_loss_pct"]:
            logger.info(
                f"[{self.name}] {position.symbol}: SL hit "
                f"(pnl={pnl_pct:.2%} <= -{cfg['stop_loss_pct']:.2%})"
            )
            return True

        # 3. Timeout
        if position.holding_duration() >= cfg["timeout_hours"]:
            logger.info(
                f"[{self.name}] {position.symbol}: Timeout "
                f"({position.holding_duration():.1f}h >= {cfg['timeout_hours']}h)"
            )
            return True

        return False
