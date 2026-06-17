#!/usr/bin/env python3
"""
Kelly Criterion Position Sizer
Optimal position sizing based on edge and risk
"""

import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

class KellyPositionSizer:
    """
    Implements Kelly Criterion for optimal position sizing

    Formula: f* = (bp - q) / b
    Where:
    - f* = fraction of portfolio to bet
    - b = average win / average loss (odds)
    - p = probability of win
    - q = probability of loss (1 - p)
    """

    def __init__(self, fractional_kelly: float = 0.25):
        """
        Args:
            fractional_kelly: Use fraction of Kelly (0.25 = quarter Kelly)
                              Reduces volatility while maintaining growth
        """
        self.fractional_kelly = fractional_kelly
        self.min_position_pct = 0.02  # Minimum 2% of portfolio
        self.max_position_pct = 0.10  # Maximum 10% of portfolio

        logger.info(f"Kelly Position Sizer initialized (fractional: {fractional_kelly})")

    def calculate(
        self,
        portfolio_value: float,
        confidence: float,
        win_rate: float = 0.55,
        avg_win: float = 0.04,
        avg_loss: float = 0.02
    ) -> float:
        """
        Calculate optimal position size using Kelly Criterion

        Args:
            portfolio_value: Current portfolio value
            confidence: Strategy confidence (0.0 to 1.0)
            win_rate: Historical win rate (default 55%)
            avg_win: Average win percentage (default 4%)
            avg_loss: Average loss percentage (default 2%)

        Returns:
            Position size in dollars
        """
        # Calculate odds (b)
        b = avg_win / avg_loss if avg_loss > 0 else 2.0

        # Calculate probabilities
        p = win_rate * confidence  # Adjust win rate by confidence
        q = 1 - p

        # Kelly formula: f* = (bp - q) / b
        kelly_fraction = (b * p - q) / b

        # Apply fractional Kelly for safety
        adjusted_kelly = kelly_fraction * self.fractional_kelly

        # Ensure within bounds
        position_pct = max(self.min_position_pct, min(adjusted_kelly, self.max_position_pct))

        # Calculate dollar amount
        position_size = portfolio_value * position_pct

        logger.info(
            f"Kelly sizing: win_rate={win_rate:.2%}, confidence={confidence:.2f}, "
            f"kelly={kelly_fraction:.2%}, adjusted={adjusted_kelly:.2%}, "
            f"final_pct={position_pct:.2%}, size=${position_size:.2f}"
        )

        return position_size

    def calculate_shares(
        self,
        portfolio_value: float,
        current_price: float,
        confidence: float,
        win_rate: float = 0.55,
        avg_win: float = 0.04,
        avg_loss: float = 0.02
    ) -> int:
        """
        Calculate number of shares to buy

        Returns:
            Number of shares (integer)
        """
        position_size = self.calculate(
            portfolio_value=portfolio_value,
            confidence=confidence,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss
        )

        shares = int(position_size / current_price)

        # Ensure at least 1 share
        return max(1, shares)

    def get_kelly_stats(
        self,
        win_rate: float = 0.55,
        avg_win: float = 0.04,
        avg_loss: float = 0.02
    ) -> dict:
        """Get Kelly calculation statistics"""
        b = avg_win / avg_loss if avg_loss > 0 else 2.0
        p = win_rate
        q = 1 - p

        kelly_full = (b * p - q) / b
        kelly_half = kelly_full * 0.5
        kelly_quarter = kelly_full * 0.25

        return {
            'odds': b,
            'win_rate': p,
            'loss_rate': q,
            'full_kelly': kelly_full,
            'half_kelly': kelly_half,
            'quarter_kelly': kelly_quarter,
            'recommended': kelly_quarter  # Conservative approach
        }
