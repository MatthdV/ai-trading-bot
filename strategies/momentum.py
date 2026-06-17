#!/usr/bin/env python3
"""
Momentum Strategy — MACD + EMA Crossover + ADX + Volume.

Rides established trends using four confirmation layers:
  1. MACD crossover for momentum direction
  2. EMA(9) vs EMA(21) for trend alignment
  3. ADX for trend strength filtering
  4. Relative volume for participation confirmation

Only trades during US market hours (09:30-16:00 ET).

Author: Matthieu de Villele
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Direction, Position, Signal

logger = logging.getLogger(__name__)

# US Eastern timezone — handles EDT/EST transitions automatically
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN = time(9, 30)
_MARKET_CLOSE = time(16, 0)


class MomentumStrategy(BaseStrategy):
    """
    Trend-following momentum strategy.

    Entry (LONG):
      MACD crosses above signal line
      AND EMA(9) > EMA(21)
      AND ADX > 25  (strong trend)
      AND relative volume > 1.5

    Entry (SHORT):
      MACD crosses below signal line
      AND EMA(9) < EMA(21)
      AND ADX > 25
      AND relative volume > 1.5

    Exit:
      - MACD crosses in the opposite direction
      - ADX drops below 20 (trend lost)
      - Trailing stop: 1.5 * ATR(14)

    Strength is derived from MACD crossover amplitude, ADX value,
    and relative volume.
    """

    CLASS_DEFAULTS: dict[str, Any] = {
        # MACD
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        # EMA crossover
        "ema_fast": 9,
        "ema_slow": 21,
        # ADX
        "adx_period": 14,
        "adx_entry_threshold": 18.0,
        "adx_exit_threshold": 14.0,
        # Volume
        "volume_avg_period": 20,
        "volume_min_relative": 1.0,
        # ATR trailing stop
        "atr_period": 14,
        "atr_trailing_multiplier": 2.5,
        # Sizing
        "max_allocation_pct": 0.20,
        # Minimum candles
        "min_candles": 60,
        # Market hours filter (ET)
        "filter_market_hours": True,
        # Strength weights
        "weight_macd": 0.40,
        "weight_adx": 0.35,
        "weight_volume": 0.25,
    }

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        merged = {**self.CLASS_DEFAULTS, **(config or {})}
        super().__init__(merged)

    # ------------------------------------------------------------------
    # Indicator helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, min_periods=period, adjust=False).mean()

    @classmethod
    def _macd(
        cls, closes: pd.Series, fast: int, slow: int, signal: int
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Return (macd_line, signal_line, histogram)."""
        ema_fast = cls._ema(closes, fast)
        ema_slow = cls._ema(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = cls._ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def _adx(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int
    ) -> pd.Series:
        """
        Compute the Average Directional Index (ADX).
        Uses Wilder's smoothing (EMA with alpha=1/period).
        """
        prev_high = high.shift(1)
        prev_low = low.shift(1)
        prev_close = close.shift(1)

        # True Range
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        plus_dm = (high - prev_high).clip(lower=0)
        minus_dm = (prev_low - low).clip(lower=0)

        # When both are positive, keep the larger one
        mask = plus_dm > minus_dm
        plus_dm = plus_dm.where(mask, 0)
        minus_dm = minus_dm.where(~mask, 0)

        # Smoothed with Wilder's method
        alpha = 1 / period
        atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        plus_di = 100 * (
            plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr
        )
        minus_di = 100 * (
            minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean() / atr
        )

        di_sum = plus_di + minus_di
        dx = (100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan))
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        return adx

    @staticmethod
    def _atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int
    ) -> pd.Series:
        """Average True Range."""
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def _relative_volume(volume: pd.Series, period: int) -> pd.Series:
        """Current volume divided by rolling average volume."""
        avg = volume.rolling(window=period, min_periods=period).mean()
        return volume / avg.replace(0, np.nan)

    @staticmethod
    def _is_us_market_hours(ts: datetime | None = None) -> bool:
        """Check whether *ts* falls inside US equity market hours."""
        ts = ts or datetime.now(_ET)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_ET)
        et_time = ts.astimezone(_ET).time()
        return _MARKET_OPEN <= et_time <= _MARKET_CLOSE

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def analyze(self, symbol: str, candles: pd.DataFrame) -> Signal:
        cfg = self.config
        min_c = cfg["min_candles"]

        # --- guard: insufficient data ---
        if candles is None or len(candles) < min_c:
            return Signal(
                symbol=symbol,
                direction=Direction.NEUTRAL,
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "insufficient_data"},
            )

        # --- guard: market hours filter ---
        if cfg["filter_market_hours"] and not self._is_us_market_hours():
            return Signal(
                symbol=symbol,
                direction=Direction.NEUTRAL,
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "outside_market_hours"},
            )

        closes = candles["close"].astype(float)
        highs = candles["high"].astype(float)
        lows = candles["low"].astype(float)
        volumes = candles["volume"].astype(float)

        # Compute indicators
        macd_line, signal_line, histogram = self._macd(
            closes, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"]
        )
        ema_fast = self._ema(closes, cfg["ema_fast"])
        ema_slow = self._ema(closes, cfg["ema_slow"])
        adx = self._adx(highs, lows, closes, cfg["adx_period"])
        rel_vol = self._relative_volume(volumes, cfg["volume_avg_period"])
        atr = self._atr(highs, lows, closes, cfg["atr_period"])

        # Current & previous values
        cur_macd = macd_line.iloc[-1]
        cur_signal = signal_line.iloc[-1]
        prev_macd = macd_line.iloc[-2] if len(macd_line) > 1 else np.nan
        prev_signal = signal_line.iloc[-2] if len(signal_line) > 1 else np.nan
        cur_ema_f = ema_fast.iloc[-1]
        cur_ema_s = ema_slow.iloc[-1]
        cur_adx = adx.iloc[-1]
        cur_rvol = rel_vol.iloc[-1]
        cur_atr = atr.iloc[-1]
        cur_price = closes.iloc[-1]

        if any(np.isnan(v) for v in (cur_macd, cur_signal, prev_macd, prev_signal, cur_adx, cur_rvol, cur_atr)):
            return Signal(
                symbol=symbol,
                direction=Direction.NEUTRAL,
                strength=0.0,
                strategy_name=self.name,
                metadata={"reason": "nan_indicators"},
            )

        # Detect MACD crossover
        macd_cross_up = prev_macd <= prev_signal and cur_macd > cur_signal
        macd_cross_down = prev_macd >= prev_signal and cur_macd < cur_signal

        direction = Direction.NEUTRAL
        strength = 0.0

        adx_ok = cur_adx > cfg["adx_entry_threshold"]
        vol_ok = cur_rvol > cfg["volume_min_relative"]

        # LONG
        if macd_cross_up and cur_ema_f > cur_ema_s and adx_ok and vol_ok:
            direction = Direction.LONG
            macd_amp = abs(cur_macd - cur_signal)
            # Normalise each component to 0-1
            macd_str = min(1.0, macd_amp / (cur_atr if cur_atr else 1.0))
            adx_str = min(1.0, (cur_adx - cfg["adx_entry_threshold"]) / 25.0)
            vol_str = min(1.0, (cur_rvol - 1.0) / 2.0)
            strength = (
                cfg["weight_macd"] * macd_str
                + cfg["weight_adx"] * adx_str
                + cfg["weight_volume"] * vol_str
            )

        # SHORT
        elif macd_cross_down and cur_ema_f < cur_ema_s and adx_ok and vol_ok:
            direction = Direction.SHORT
            macd_amp = abs(cur_macd - cur_signal)
            macd_str = min(1.0, macd_amp / (cur_atr if cur_atr else 1.0))
            adx_str = min(1.0, (cur_adx - cfg["adx_entry_threshold"]) / 25.0)
            vol_str = min(1.0, (cur_rvol - 1.0) / 2.0)
            strength = (
                cfg["weight_macd"] * macd_str
                + cfg["weight_adx"] * adx_str
                + cfg["weight_volume"] * vol_str
            )

        strength = max(0.0, min(1.0, strength))

        metadata = {
            "macd": round(float(cur_macd), 6),
            "macd_signal": round(float(cur_signal), 6),
            "macd_histogram": round(float(histogram.iloc[-1]), 6),
            "ema_fast": round(float(cur_ema_f), 4),
            "ema_slow": round(float(cur_ema_s), 4),
            "adx": round(float(cur_adx), 2),
            "relative_volume": round(float(cur_rvol), 2),
            "atr": round(float(cur_atr), 4),
            "price": round(float(cur_price), 4),
            "macd_cross_up": bool(macd_cross_up),
            "macd_cross_down": bool(macd_cross_down),
        }

        return Signal(
            symbol=symbol,
            direction=direction,
            strength=strength,
            strategy_name=self.name,
            metadata=metadata,
        )

    def should_enter(self, signal: Signal) -> bool:
        return signal.is_actionable and signal.strength >= 0.3

    def should_exit(self, signal: Signal, position: Position) -> bool:
        """
        Exit when:
          1. MACD crosses in the opposite direction
          2. ADX drops below 20 (trend exhaustion)
          3. Trailing stop at 1.5 * ATR from the highest close since entry
        """
        cfg = self.config
        meta = signal.metadata
        price = meta.get("price", 0.0)
        adx = meta.get("adx", 50.0)
        atr = meta.get("atr", 0.0)

        # 1. MACD opposite cross
        if position.is_long and meta.get("macd_cross_down", False):
            logger.info(f"[{self.name}] {position.symbol}: exit on bearish MACD cross")
            return True
        if not position.is_long and meta.get("macd_cross_up", False):
            logger.info(f"[{self.name}] {position.symbol}: exit on bullish MACD cross")
            return True

        # 2. ADX collapse
        if adx < cfg["adx_exit_threshold"]:
            logger.info(
                f"[{self.name}] {position.symbol}: exit — ADX={adx:.1f} "
                f"< {cfg['adx_exit_threshold']}"
            )
            return True

        # 3. Trailing stop based on ATR
        trailing_dist = cfg["atr_trailing_multiplier"] * atr
        if position.is_long:
            trailing_stop = price - trailing_dist
            # If price dropped below trailing stop relative to entry
            pnl_pct = position.unrealized_pnl_pct(price)
            # Activate trailing stop only after +1 % gain
            if pnl_pct >= 0.01 and price <= position.entry_price + (position.entry_price * 0.01) - trailing_dist:
                logger.info(
                    f"[{self.name}] {position.symbol}: trailing stop hit "
                    f"(ATR trail={trailing_dist:.2f})"
                )
                return True
        else:
            pnl_pct = position.unrealized_pnl_pct(price)
            if pnl_pct >= 0.01 and price >= position.entry_price - (position.entry_price * 0.01) + trailing_dist:
                logger.info(
                    f"[{self.name}] {position.symbol}: trailing stop hit (short)"
                )
                return True

        # Simple stop-loss fallback at 3 % (wider than mean-reversion)
        if position.unrealized_pnl_pct(price) <= -0.03:
            logger.info(f"[{self.name}] {position.symbol}: hard stop-loss at -3 %")
            return True

        return False
