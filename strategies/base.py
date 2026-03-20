#!/usr/bin/env python3
"""
Base Strategy Framework — Abstract classes for all trading strategies.

Provides the foundation for building pluggable strategies with a unified
interface for signal generation, entry/exit logic, and position sizing.

Author: Matthieu de Villele
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class Direction(Enum):
    """Trade direction."""
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


@dataclass
class Signal:
    """
    Represents a trading signal emitted by a strategy.

    Attributes:
        symbol: Ticker symbol (e.g. "AAPL").
        direction: LONG, SHORT, or NEUTRAL.
        strength: Signal strength between 0.0 (weakest) and 1.0 (strongest).
        strategy_name: Name of the strategy that generated this signal.
        timestamp: When the signal was generated (UTC).
        metadata: Extra context (indicator values, reasons, etc.).
    """
    symbol: str
    direction: Direction
    strength: float
    strategy_name: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.strength = max(0.0, min(1.0, self.strength))

    @property
    def is_actionable(self) -> bool:
        """Return True when the signal suggests opening or closing a position."""
        return self.direction != Direction.NEUTRAL and self.strength > 0.0

    def __repr__(self) -> str:
        return (
            f"Signal({self.symbol}, {self.direction.value}, "
            f"strength={self.strength:.2f}, strategy={self.strategy_name})"
        )


@dataclass
class Position:
    """
    Represents an open trading position.

    Attributes:
        symbol: Ticker symbol.
        direction: LONG or SHORT.
        entry_price: Price at which the position was entered.
        quantity: Number of shares / contracts.
        entry_time: When the position was opened (UTC).
        stop_loss: Stop-loss price level.
        take_profit: Take-profit price level.
    """
    symbol: str
    direction: Direction
    entry_price: float
    quantity: float
    entry_time: datetime = field(default_factory=datetime.utcnow)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

    @property
    def is_long(self) -> bool:
        return self.direction == Direction.LONG

    @property
    def notional_value(self) -> float:
        """Dollar value of the position at entry."""
        return self.entry_price * self.quantity

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L given the current price."""
        if self.is_long:
            return (current_price - self.entry_price) * self.quantity
        return (self.entry_price - current_price) * self.quantity

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Unrealized P&L as a percentage of the entry price."""
        if self.entry_price == 0:
            return 0.0
        if self.is_long:
            return (current_price - self.entry_price) / self.entry_price
        return (self.entry_price - current_price) / self.entry_price

    def holding_duration(self, now: Optional[datetime] = None) -> float:
        """Return holding duration in hours."""
        now = now or datetime.utcnow()
        # Align timezone awareness to avoid subtract errors
        entry = self.entry_time
        if hasattr(entry, 'tzinfo') and entry.tzinfo is not None and now.tzinfo is None:
            now = now.replace(tzinfo=entry.tzinfo)
        elif hasattr(now, 'tzinfo') and now.tzinfo is not None and (not hasattr(entry, 'tzinfo') or entry.tzinfo is None):
            now = now.replace(tzinfo=None)
        delta = now - entry
        return delta.total_seconds() / 3600.0

    def __repr__(self) -> str:
        return (
            f"Position({self.symbol}, {self.direction.value}, "
            f"qty={self.quantity}, entry={self.entry_price:.2f})"
        )


class BaseStrategy(ABC):
    """
    Abstract base class that all strategies must implement.

    Subclasses define their own indicator calculations and trading logic
    while conforming to a shared interface consumed by the backtesting
    engine and the live trading loop.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """
        Initialise the strategy with a configuration dict.

        Args:
            config: Strategy-specific parameters. Subclasses should define
                    CLASS_DEFAULTS and merge them with the provided config.
        """
        self._config = config or {}
        self._name = self.__class__.__name__
        logger.info(f"Strategy {self._name} initialized")

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return self._name

    @property
    def config(self) -> dict[str, Any]:
        return self._config

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    def analyze(self, symbol: str, candles: pd.DataFrame) -> Signal:
        """
        Analyse a set of OHLCV candles and produce a Signal.

        The DataFrame must have at least the columns:
        ``open``, ``high``, ``low``, ``close``, ``volume``
        and a DatetimeIndex (or a ``timestamp`` column).

        Args:
            symbol: The ticker symbol being analysed.
            candles: OHLCV DataFrame sorted by time ascending.

        Returns:
            A Signal object with direction, strength, and metadata.
        """
        ...

    @abstractmethod
    def should_enter(self, signal: Signal) -> bool:
        """
        Decide whether to enter a new position based on the signal.

        Args:
            signal: The latest Signal from analyze().

        Returns:
            True if a new position should be opened.
        """
        ...

    @abstractmethod
    def should_exit(self, signal: Signal, position: Position) -> bool:
        """
        Decide whether to exit an existing position.

        Args:
            signal: The latest Signal from analyze().
            position: The current open Position.

        Returns:
            True if the position should be closed.
        """
        ...

    def calculate_position_size(
        self,
        signal: Signal,
        portfolio_value: float,
    ) -> float:
        """
        Determine the dollar amount to allocate to a new position.

        Default implementation: allocate ``strength * max_allocation_pct``
        of the portfolio. Subclasses can override for more sophisticated
        sizing (e.g. volatility-adjusted, Kelly Criterion).

        Args:
            signal: The Signal driving the trade.
            portfolio_value: Current total portfolio value in USD.

        Returns:
            Dollar amount to invest in the position.
        """
        max_pct = self._config.get("max_allocation_pct", 0.05)
        return portfolio_value * max_pct * signal.strength
