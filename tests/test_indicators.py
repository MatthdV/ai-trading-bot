#!/usr/bin/env python3
"""Tests for technical indicators in _legacy_rsi_macd_kelly.py"""

import pytest
from strategies._legacy_rsi_macd_kelly import RSIMACDKellyStrategy


@pytest.fixture
def strategy():
    return RSIMACDKellyStrategy()


# --- EMA ---

class TestEMA:
    def test_ema_differs_from_sma_on_trending_data(self, strategy):
        """EMA should weight recent prices more, diverging from SMA on a trend."""
        # Accelerating uptrend (non-linear so EMA != SMA)
        prices = [10 + i ** 1.5 for i in range(30)]
        period = 10

        ema = strategy._calculate_ema(prices, period)
        sma = sum(prices[-period:]) / period

        # EMA should be higher than trailing SMA on accelerating uptrend
        assert ema > sma, f"EMA {ema} should be > SMA {sma} on uptrend"

    def test_ema_on_constant_prices_equals_price(self, strategy):
        """EMA of constant prices should equal that constant."""
        prices = [100.0] * 30
        ema = strategy._calculate_ema(prices, 10)
        assert abs(ema - 100.0) < 1e-10

    def test_ema_uses_all_prices_not_just_last_period(self, strategy):
        """EMA must use full price history, not just the last N prices."""
        # If EMA only looks at last 10 prices, these two should be identical
        prices_a = [50.0] * 20 + [100.0] * 10
        prices_b = [100.0] * 10

        ema_a = strategy._calculate_ema(prices_a, 10)
        ema_b = strategy._calculate_ema(prices_b, 10)

        # prices_a has a lower seed SMA, so its EMA should be lower
        assert ema_a < ema_b, "EMA should incorporate history beyond last period"


# --- MACD ---

class TestMACD:
    def test_signal_line_is_not_proportional_to_macd(self, strategy):
        """Signal line must be an EMA of MACD history, not macd_line * constant."""
        prices = [100 + i * 0.5 for i in range(60)]
        macd_line, signal_line, histogram = strategy._calculate_macd(prices)

        # Old bug: signal_line = macd_line * 0.9
        if macd_line != 0:
            ratio = signal_line / macd_line
            assert abs(ratio - 0.9) > 0.001, "Signal line should not be 0.9 * MACD"

    def test_histogram_is_macd_minus_signal(self, strategy):
        prices = [100 + i * 0.3 for i in range(60)]
        macd_line, signal_line, histogram = strategy._calculate_macd(prices)
        assert abs(histogram - (macd_line - signal_line)) < 1e-10

    def test_macd_positive_on_uptrend(self, strategy):
        """Fast EMA > Slow EMA on uptrend → positive MACD."""
        prices = [50 + i for i in range(60)]
        macd_line, _, _ = strategy._calculate_macd(prices)
        assert macd_line > 0


# --- RSI ---

class TestRSI:
    def test_rsi_wilder_known_values(self, strategy):
        """Test RSI against known Wilder's smoothing output."""
        # 15 prices → 14 deltas → enough for period=14 seed
        prices = [
            44.34, 44.09, 43.61, 44.33, 44.83,
            45.10, 45.42, 45.84, 46.08, 45.89,
            46.03, 45.61, 46.28, 46.28, 46.00,
            46.03, 46.41, 46.22, 45.64,
        ]
        rsi = strategy._calculate_rsi(prices, period=14)

        # Wilder's RSI for this series should be around 58-62
        assert 50 < rsi < 70, f"RSI {rsi} out of expected Wilder's range"

    def test_rsi_100_on_pure_gains(self, strategy):
        """All gains → RSI = 100."""
        prices = [float(i) for i in range(20)]
        rsi = strategy._calculate_rsi(prices, period=14)
        assert rsi == 100.0

    def test_rsi_0_on_pure_losses(self, strategy):
        """All losses → RSI = 0."""
        prices = [float(20 - i) for i in range(20)]
        rsi = strategy._calculate_rsi(prices, period=14)
        assert rsi == 0.0

    def test_rsi_uses_wilder_smoothing_not_simple_average(self, strategy):
        """RSI with Wilder's smoothing should differ from simple SMA-based RSI."""
        # Generate prices with a clear regime change
        prices = [100 + i for i in range(20)] + [120 - i * 0.5 for i in range(20)]

        rsi = strategy._calculate_rsi(prices, period=14)

        # Simple average RSI of last 14 deltas
        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        last_14 = deltas[-14:]
        avg_gain = sum(d for d in last_14 if d > 0) / 14
        avg_loss = sum(-d for d in last_14 if d < 0) / 14
        simple_rsi = 100 - (100 / (1 + avg_gain / avg_loss)) if avg_loss != 0 else 100

        assert abs(rsi - simple_rsi) > 0.5, (
            f"Wilder RSI ({rsi:.2f}) should differ from simple RSI ({simple_rsi:.2f})"
        )


# --- EMA Series ---

class TestEMASeries:
    def test_last_value_matches_single_ema(self, strategy):
        """_calculate_ema_series last element should match _calculate_ema."""
        prices = [100 + i * 0.5 for i in range(40)]
        period = 12

        ema_single = strategy._calculate_ema(prices, period)
        ema_series = strategy._calculate_ema_series(prices, period)

        assert abs(ema_series[-1] - ema_single) < 1e-10
