#!/usr/bin/env python3
"""
RSI + MACD + Kelly Strategy (Pure Python, no numpy)
Mean reversion strategy with momentum confirmation
"""

import logging
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
        """Generate trading signals for all symbols"""
        signals = []
        
        for symbol in self.symbols:
            try:
                signal = self._analyze_symbol(symbol, alpaca_client)
                if signal and signal.action != 'hold':
                    signals.append(signal)
            except Exception as e:
                logger.error(f"Error analyzing {symbol}: {e}")
                continue
        
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
        """Calculate RSI (Relative Strength Index)"""
        if len(prices) < period + 1:
            return 50.0
        
        # Calculate price changes
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        
        # Get gains and losses
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """Calculate Exponential Moving Average"""
        if len(prices) < period:
            return prices[-1]
        
        # Use last 'period' prices
        data = prices[-period:]
        
        # Calculate SMA first
        sma = sum(data[:period]) / period
        
        # Calculate EMA
        multiplier = 2 / (period + 1)
        ema = sma
        
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        
        return ema
    
    def _calculate_macd(self, prices: List[float]) -> tuple:
        """Calculate MACD (Moving Average Convergence Divergence)"""
        ema_fast = self._calculate_ema(prices, self.macd_fast)
        ema_slow = self._calculate_ema(prices, self.macd_slow)
        
        macd_line = ema_fast - ema_slow
        
        # For signal line, we need MACD history
        # Simplified: use recent price EMA as proxy
        signal_line = macd_line * 0.9  # Simplified approximation
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
