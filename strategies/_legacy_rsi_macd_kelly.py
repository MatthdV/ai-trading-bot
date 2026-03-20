#!/usr/bin/env python3
"""
RSI + MACD + Kelly Strategy (Pure Python, no numpy)
Mean reversion strategy with momentum confirmation
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class Signal:
    """Trading signal"""
    symbol: str
    action: str  # 'buy', 'sell', 'hold'
    confidence: float  # 0.0 to 1.0
    price: float
    rsi: float
    macd: float
    macd_signal: float
    ema_20: float
    ema_50: float

class RSIMACDKellyStrategy:
    """
    Trading strategy combining:
    - RSI for mean reversion (oversold/overbought)
    - MACD for momentum confirmation
    - EMA crossovers for trend direction
    """
    
    def __init__(
        self,
        rsi_period: int = 14,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        ema_short: int = 20,
        ema_long: int = 50
    ):
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.ema_short = ema_short
        self.ema_long = ema_long
        
        # Watchlist - popular liquid stocks
        self.symbols = [
            'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
            'NVDA', 'META', 'NFLX', 'AMD', 'CRM',
            'BABA', 'UBER', 'COIN', 'PLTR', 'RKLB'
        ]
        
        logger.info(f"Strategy initialized with {len(self.symbols)} symbols")
    
    def generate_signals(self, alpaca_client) -> List[Signal]:
        """Generate trading signals for all symbols (parallel fetching)"""
        signals = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._analyze_symbol, symbol, alpaca_client): symbol
                for symbol in self.symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    signal = future.result()
                    if signal and signal.action != 'hold':
                        signals.append(signal)
                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")

        return signals
    
    def _analyze_symbol(
        self,
        symbol: str,
        alpaca_client
    ) -> Optional[Signal]:
        """Analyze a single symbol"""
        # Get historical bars
        bars = alpaca_client.get_bars(
            symbol=symbol,
            timeframe='1D',
            limit=100
        )
        
        if len(bars) < 50:
            logger.warning(f"Insufficient data for {symbol}: {len(bars)} bars")
            return None
        
        # Extract prices
        closes = [bar['c'] for bar in bars]
        
        current_price = closes[-1]
        
        # Calculate indicators
        rsi = self._calculate_rsi(closes)
        macd_line, signal_line, histogram = self._calculate_macd(closes)
        ema_20 = self._calculate_ema(closes, self.ema_short)
        ema_50 = self._calculate_ema(closes, self.ema_long)
        
        # Generate signal
        action = 'hold'
        confidence = 0.0
        
        # Buy conditions
        if rsi < self.rsi_oversold:  # Oversold
            if macd_line > signal_line:  # Bullish momentum
                if ema_20 > ema_50:  # Uptrend
                    action = 'buy'
                    confidence = self._calculate_confidence(
                        rsi, macd_line, signal_line, ema_20, ema_50, 'buy'
                    )
        
        # Sell conditions
        elif rsi > self.rsi_overbought:  # Overbought
            if macd_line < signal_line:  # Bearish momentum
                if ema_20 < ema_50:  # Downtrend
                    action = 'sell'
                    confidence = self._calculate_confidence(
                        rsi, macd_line, signal_line, ema_20, ema_50, 'sell'
                    )
        
        return Signal(
            symbol=symbol,
            action=action,
            confidence=confidence,
            price=current_price,
            rsi=rsi,
            macd=macd_line,
            macd_signal=signal_line,
            ema_20=ema_20,
            ema_50=ema_50
        )
    
    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """Calculate RSI using Wilder's smoothing"""
        if len(prices) < period + 1:
            return 50.0

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

        # Seed with SMA over first `period` deltas
        gains_seed = [d if d > 0 else 0 for d in deltas[:period]]
        losses_seed = [-d if d < 0 else 0 for d in deltas[:period]]

        avg_gain = sum(gains_seed) / period
        avg_loss = sum(losses_seed) / period

        # Wilder's smoothing for remaining deltas
        for d in deltas[period:]:
            gain = d if d > 0 else 0
            loss = -d if d < 0 else 0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return prices[-1]

        # Seed EMA with SMA of first `period` prices
        sma = sum(prices[:period]) / period
        multiplier = 2 / (period + 1)
        ema = sma

        # Iterate over remaining prices
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_ema_series(self, values: List[float], period: int) -> List[float]:
        """Calculate EMA series (returns list of EMA values from index period-1 onward)"""
        if len(values) < period:
            return values[:]

        sma = sum(values[:period]) / period
        multiplier = 2 / (period + 1)
        ema_values = [sma]

        for val in values[period:]:
            ema_values.append((val - ema_values[-1]) * multiplier + ema_values[-1])

        return ema_values
    
    def _calculate_macd(self, prices: List[float]) -> tuple:
        """Calculate MACD (Moving Average Convergence Divergence)"""
        # Build full EMA series for fast and slow
        ema_fast_series = self._calculate_ema_series(prices, self.macd_fast)
        ema_slow_series = self._calculate_ema_series(prices, self.macd_slow)

        # Align: slow series starts at index (macd_slow - 1),
        # fast series starts at index (macd_fast - 1)
        offset = self.macd_slow - self.macd_fast
        macd_series = [
            ema_fast_series[i + offset] - ema_slow_series[i]
            for i in range(len(ema_slow_series))
        ]

        if len(macd_series) < self.macd_signal:
            # Not enough data — fallback
            macd_line = macd_series[-1] if macd_series else 0.0
            return macd_line, macd_line, 0.0

        # Signal line = EMA(9) of MACD series
        signal_series = self._calculate_ema_series(macd_series, self.macd_signal)

        macd_line = macd_series[-1]
        signal_line = signal_series[-1]
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram
    
    def _calculate_confidence(
        self,
        rsi: float,
        macd: float,
        macd_signal: float,
        ema_20: float,
        ema_50: float,
        action: str
    ) -> float:
        """Calculate signal confidence (0.0 to 1.0)"""
        confidence = 0.5  # Base confidence
        
        if action == 'buy':
            # Stronger oversold = higher confidence
            rsi_factor = (self.rsi_oversold - rsi) / self.rsi_oversold
            confidence += rsi_factor * 0.2
            
            # MACD confirmation
            if macd > macd_signal:
                confidence += 0.15
            
            # Trend confirmation
            if ema_20 > ema_50:
                confidence += 0.15
        
        elif action == 'sell':
            # Stronger overbought = higher confidence
            rsi_factor = (rsi - self.rsi_overbought) / (100 - self.rsi_overbought)
            confidence += rsi_factor * 0.2
            
            # MACD confirmation
            if macd < macd_signal:
                confidence += 0.15
            
            # Trend confirmation
            if ema_20 < ema_50:
                confidence += 0.15
        
        return min(1.0, max(0.0, confidence))
