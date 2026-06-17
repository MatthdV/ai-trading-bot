#!/usr/bin/env python3
"""
Tests for the technical indicators used by MomentumStrategy.

Ports assertions previously held in the (deleted) tests/test_indicators.py
onto strategies.momentum.MomentumStrategy's static helpers.  The original
tests targeted the now-deleted _legacy_rsi_macd_kelly.RSIMACDKellyStrategy,
which worked on python lists; momentum.py uses pandas Series instead, so
the assertions have been adapted accordingly.  RSI is not exposed by
MomentumStrategy and is therefore out of scope here.
"""

import numpy as np
import pandas as pd
import pytest

from strategies.momentum import MomentumStrategy


@pytest.fixture
def mom():
    return MomentumStrategy()


# =====================================================================
# EMA
# =====================================================================

class TestEMA:
    def test_ema_differs_from_sma_on_trending_data(self, mom):
        """EMA weights recent prices more heavily than SMA on a trend."""
        prices = pd.Series([10 + i ** 1.5 for i in range(30)])
        period = 10
        ema_last = mom._ema(prices, period).iloc[-1]
        sma_last = prices.iloc[-period:].mean()
        assert ema_last > sma_last, (
            f"EMA {ema_last:.2f} should be > SMA {sma_last:.2f} on uptrend"
        )

    def test_ema_on_constant_prices_equals_price(self, mom):
        prices = pd.Series([100.0] * 30)
        ema_last = mom._ema(prices, 10).iloc[-1]
        assert abs(ema_last - 100.0) < 1e-9

    def test_ema_incorporates_history_not_just_last_period(self, mom):
        """EMA must use the full price history, not only the last N points."""
        prices_a = pd.Series([50.0] * 20 + [100.0] * 10)
        prices_b = pd.Series([100.0] * 10)
        ema_a = mom._ema(prices_a, 10).iloc[-1]
        ema_b = mom._ema(prices_b, 10).iloc[-1]
        # prices_a seeds from 50s → its EMA should be pulled lower than 100
        assert ema_a < ema_b


# =====================================================================
# MACD
# =====================================================================

class TestMACD:
    def test_signal_line_is_ema_of_macd_not_proportional(self, mom):
        """Signal line must be an EMA of MACD, not macd * constant."""
        prices = pd.Series([100 + i * 0.5 for i in range(60)])
        macd_line, signal_line, _ = mom._macd(prices, fast=12, slow=26, signal=9)
        last_macd = macd_line.iloc[-1]
        last_signal = signal_line.iloc[-1]
        if last_macd != 0:
            ratio = last_signal / last_macd
            # Old bug pattern: signal_line == 0.9 * macd_line
            assert abs(ratio - 0.9) > 0.001, "Signal line should not be 0.9 * MACD"

    def test_histogram_is_macd_minus_signal(self, mom):
        prices = pd.Series([100 + i * 0.3 for i in range(60)])
        macd_line, signal_line, histogram = mom._macd(
            prices, fast=12, slow=26, signal=9
        )
        diff = (histogram - (macd_line - signal_line)).dropna()
        assert (diff.abs() < 1e-9).all()

    def test_macd_positive_on_sustained_uptrend(self, mom):
        """Fast EMA > Slow EMA on an uptrend → positive MACD."""
        prices = pd.Series([50 + i for i in range(60)])
        macd_line, _, _ = mom._macd(prices, fast=12, slow=26, signal=9)
        assert macd_line.iloc[-1] > 0


# =====================================================================
# ATR
# =====================================================================

class TestATR:
    def test_atr_is_positive_for_non_flat_data(self, mom):
        n = 60
        rng = np.random.default_rng(seed=42)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
        high = close + rng.uniform(0.1, 0.5, n)
        low = close - rng.uniform(0.1, 0.5, n)
        atr = mom._atr(high, low, close, period=14)
        assert atr.iloc[-1] > 0

    def test_atr_is_zero_for_flat_prices(self, mom):
        n = 30
        close = pd.Series([100.0] * n)
        high = close.copy()
        low = close.copy()
        atr = mom._atr(high, low, close, period=14)
        assert abs(atr.iloc[-1]) < 1e-9

    def test_atr_grows_with_volatility(self, mom):
        """Wider range bars should produce a larger ATR."""
        n = 60
        close = pd.Series([100.0] * n)
        narrow_high = close + 0.5
        narrow_low = close - 0.5
        wide_high = close + 2.0
        wide_low = close - 2.0
        atr_narrow = mom._atr(narrow_high, narrow_low, close, period=14).iloc[-1]
        atr_wide = mom._atr(wide_high, wide_low, close, period=14).iloc[-1]
        assert atr_wide > atr_narrow


# =====================================================================
# ADX
# =====================================================================

class TestADX:
    def test_adx_high_on_strong_trend(self, mom):
        """A clean uptrend should produce a relatively high ADX."""
        n = 80
        close = pd.Series(np.linspace(100, 160, n))
        high = close + 0.5
        low = close - 0.5
        adx = mom._adx(high, low, close, period=14)
        # ADX > 20 is commonly read as "trending"
        assert adx.iloc[-1] > 20

    def test_adx_low_on_choppy_range(self, mom):
        """A tight mean-reverting range should give a lower ADX than a trend."""
        n = 120
        rng = np.random.default_rng(seed=7)
        close = pd.Series(100 + rng.normal(0, 0.2, n))
        high = close + 0.3
        low = close - 0.3
        adx = mom._adx(high, low, close, period=14)

        trend_close = pd.Series(np.linspace(100, 200, n))
        trend_high = trend_close + 0.3
        trend_low = trend_close - 0.3
        adx_trend = mom._adx(trend_high, trend_low, trend_close, period=14)

        assert adx.iloc[-1] < adx_trend.iloc[-1]
