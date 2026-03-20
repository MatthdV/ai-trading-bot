#!/usr/bin/env python3
"""
Advanced Risk Manager — Pre-trade checks, Kelly sizing, dynamic stops.

Enforces portfolio-level risk limits, computes half-Kelly position sizes,
manages trailing stops, and logs every decision for audit.

Author: Matthieu de Villele
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Optional

import numpy as np
import pandas as pd

from strategies.base import Direction, Position, Signal

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    """All configurable risk parameters in one place."""
    # Position limits
    max_position_pct: float = 0.05          # Max 5 % of portfolio per trade
    max_simultaneous_positions: int = 5
    max_sector_positions: int = 3           # Max 3 in the same sector

    # Daily / drawdown limits
    max_daily_loss_pct: float = 0.02        # -2 % → stop trading for the day
    max_portfolio_drawdown_pct: float = 0.10  # -10 % → halt + alert

    # Kelly Criterion
    kelly_fraction: float = 0.5             # Half-Kelly (conservative)

    # Stops
    atr_stop_multiplier: float = 2.0        # Initial SL = 2 * ATR
    min_risk_reward: float = 2.0            # TP / SL >= 2:1
    trailing_activation_pct: float = 0.01   # Activate trail after +1 %
    trailing_atr_multiplier: float = 1.0    # Trail distance = 1 * ATR


# Sector mapping for the default watchlist
SECTOR_MAP: dict[str, str] = {
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "tech", "AMZN": "consumer",
    "TSLA": "auto", "NVDA": "semi", "META": "tech", "NFLX": "consumer",
    "AMD": "semi", "CRM": "tech", "BABA": "consumer", "UBER": "transport",
    "COIN": "crypto", "PLTR": "tech", "RKLB": "aerospace", "SPY": "index",
}


class RiskManager:
    """
    Centralised risk management layer sitting between strategy signals and
    order execution.

    Responsibilities:
      1. Pre-trade validation  (position limits, daily loss, drawdown)
      2. Position sizing        (Half-Kelly Criterion)
      3. Stop-loss / take-profit calculation (ATR-based)
      4. Structured audit logging
      5. Daily risk report
    """

    def __init__(self, config: RiskConfig | None = None) -> None:
        self.cfg = config or RiskConfig()

        # Tracking state
        self._peak_equity: float = 0.0
        self._day_start_equity: float = 0.0
        self._current_day: date | None = None
        self._daily_pnl: float = 0.0
        self._trading_halted: bool = False
        self._halt_reason: str = ""

        # Open position tracking
        self._positions: dict[str, Position] = {}

        # Audit log
        self._audit_log: list[dict[str, Any]] = []

        logger.info("RiskManager initialized")

    # ------------------------------------------------------------------
    # 1. Pre-trade checks
    # ------------------------------------------------------------------

    def check_trade(
        self,
        signal: Signal,
        portfolio_value: float,
        open_positions: dict[str, Position] | None = None,
    ) -> tuple[bool, str]:
        """
        Run all pre-trade validation checks.

        Returns:
            (allowed, reason) — True if the trade can proceed.
        """
        positions = open_positions or self._positions

        # --- Daily reset ---
        self._maybe_reset_day(portfolio_value)

        # --- Halt check ---
        if self._trading_halted:
            reason = f"Trading halted: {self._halt_reason}"
            self._log_check(signal, False, reason)
            return False, reason

        # --- Max drawdown ---
        dd = self._current_drawdown(portfolio_value)
        if dd >= self.cfg.max_portfolio_drawdown_pct:
            self._trading_halted = True
            self._halt_reason = (
                f"Max drawdown breached: {dd:.2%} >= {self.cfg.max_portfolio_drawdown_pct:.2%}"
            )
            self._log_check(signal, False, self._halt_reason)
            return False, self._halt_reason

        # --- Daily loss ---
        daily_ret = (portfolio_value - self._day_start_equity) / self._day_start_equity if self._day_start_equity else 0
        if daily_ret <= -self.cfg.max_daily_loss_pct:
            reason = (
                f"Daily loss limit: {daily_ret:.2%} <= -{self.cfg.max_daily_loss_pct:.2%}"
            )
            self._trading_halted = True
            self._halt_reason = reason
            self._log_check(signal, False, reason)
            return False, reason

        # --- Max simultaneous positions ---
        if len(positions) >= self.cfg.max_simultaneous_positions:
            reason = f"Max positions reached: {len(positions)}/{self.cfg.max_simultaneous_positions}"
            self._log_check(signal, False, reason)
            return False, reason

        # --- Sector concentration ---
        sym_sector = SECTOR_MAP.get(signal.symbol, "unknown")
        sector_count = sum(
            1 for p in positions.values()
            if SECTOR_MAP.get(p.symbol, "unknown") == sym_sector
        )
        if sector_count >= self.cfg.max_sector_positions:
            reason = f"Sector limit ({sym_sector}): {sector_count}/{self.cfg.max_sector_positions}"
            self._log_check(signal, False, reason)
            return False, reason

        # --- Already in this symbol ---
        if signal.symbol in {p.symbol for p in positions.values()}:
            reason = f"Already holding {signal.symbol}"
            self._log_check(signal, False, reason)
            return False, reason

        self._log_check(signal, True, "All checks passed")
        return True, "OK"

    # ------------------------------------------------------------------
    # 2. Position sizing — Half-Kelly Criterion
    # ------------------------------------------------------------------

    def calculate_size(
        self,
        portfolio_value: float,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """
        Compute the optimal position size as a dollar amount using
        the Kelly Criterion, halved for conservatism, and capped at
        ``max_position_pct``.

        Kelly formula: f* = (b*p - q) / b
        where  b = avg_win / avg_loss
               p = win_rate
               q = 1 - p

        Returns:
            Dollar amount to allocate.
        """
        if avg_loss <= 0 or win_rate <= 0:
            return 0.0

        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p

        kelly_full = (b * p - q) / b
        kelly_adj = kelly_full * self.cfg.kelly_fraction

        # Clamp
        pct = max(0.0, min(kelly_adj, self.cfg.max_position_pct))
        size = portfolio_value * pct

        logger.info(
            f"Kelly sizing: b={b:.2f}, p={p:.2%}, full={kelly_full:.2%}, "
            f"adj={kelly_adj:.2%}, capped={pct:.2%}, size=${size:,.2f}"
        )
        return size

    # ------------------------------------------------------------------
    # 3. Stop-loss & take-profit (ATR-based)
    # ------------------------------------------------------------------

    def calculate_stops(
        self,
        entry_price: float,
        direction: Direction,
        atr: float,
    ) -> tuple[float, float]:
        """
        Compute initial stop-loss and take-profit levels.

        Args:
            entry_price: Fill price.
            direction: LONG or SHORT.
            atr: Current Average True Range value.

        Returns:
            (stop_loss, take_profit) prices.
        """
        sl_dist = self.cfg.atr_stop_multiplier * atr
        tp_dist = sl_dist * self.cfg.min_risk_reward  # 2:1 R/R minimum

        if direction == Direction.LONG:
            stop_loss = entry_price - sl_dist
            take_profit = entry_price + tp_dist
        else:
            stop_loss = entry_price + sl_dist
            take_profit = entry_price - tp_dist

        logger.info(
            f"Stops: entry={entry_price:.2f}, SL={stop_loss:.2f}, "
            f"TP={take_profit:.2f}, ATR={atr:.4f}"
        )
        return stop_loss, take_profit

    def update_trailing_stop(
        self,
        position: Position,
        current_price: float,
        atr: float,
    ) -> float | None:
        """
        Compute a trailing stop level.  Returns the new stop or None
        if the trailing stop hasn't activated yet.

        Activation: after +1 % unrealised gain.
        Trail distance: 1 * ATR from the current price.
        """
        pnl_pct = position.unrealized_pnl_pct(current_price)
        if pnl_pct < self.cfg.trailing_activation_pct:
            return None

        trail_dist = self.cfg.trailing_atr_multiplier * atr

        if position.is_long:
            new_sl = current_price - trail_dist
            # Only ratchet upward
            if position.stop_loss and new_sl <= position.stop_loss:
                return None
            return new_sl
        else:
            new_sl = current_price + trail_dist
            if position.stop_loss and new_sl >= position.stop_loss:
                return None
            return new_sl

    # ------------------------------------------------------------------
    # 4. Audit log
    # ------------------------------------------------------------------

    def _log_check(self, signal: Signal, allowed: bool, reason: str) -> None:
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": signal.symbol,
            "direction": signal.direction.value,
            "strength": signal.strength,
            "strategy": signal.strategy_name,
            "allowed": allowed,
            "reason": reason,
        }
        self._audit_log.append(entry)
        level = logging.INFO if allowed else logging.WARNING
        logger.log(level, f"RISK CHECK: {json.dumps(entry)}")

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the full audit log."""
        return list(self._audit_log)

    # ------------------------------------------------------------------
    # 5. Daily report
    # ------------------------------------------------------------------

    def daily_report(
        self,
        portfolio_value: float,
        open_positions: dict[str, Position] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a daily risk summary.

        Returns a dict with P&L, exposure, drawdown, and per-position info.
        """
        positions = open_positions or self._positions

        daily_ret = (
            (portfolio_value - self._day_start_equity) / self._day_start_equity
            if self._day_start_equity
            else 0.0
        )
        dd = self._current_drawdown(portfolio_value)

        # Exposure
        total_exposure = sum(p.notional_value for p in positions.values())
        exposure_pct = total_exposure / portfolio_value if portfolio_value else 0.0

        pos_details = []
        for key, pos in positions.items():
            pos_details.append({
                "symbol": pos.symbol,
                "direction": pos.direction.value,
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
            })

        report = {
            "date": datetime.utcnow().isoformat(),
            "portfolio_value": round(portfolio_value, 2),
            "daily_pnl_pct": round(daily_ret * 100, 2),
            "drawdown_pct": round(dd * 100, 2),
            "exposure_pct": round(exposure_pct * 100, 2),
            "open_positions": len(positions),
            "trading_halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "positions": pos_details,
            "checks_today": len([
                e for e in self._audit_log
                if e["timestamp"][:10] == datetime.utcnow().strftime("%Y-%m-%d")
            ]),
        }

        logger.info(f"DAILY REPORT: {json.dumps(report)}")
        return report

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_reset_day(self, portfolio_value: float) -> None:
        """Reset daily counters on a new trading day."""
        today = date.today()
        if self._current_day != today:
            self._current_day = today
            self._day_start_equity = portfolio_value
            self._daily_pnl = 0.0
            self._trading_halted = False
            self._halt_reason = ""

        if portfolio_value > self._peak_equity:
            self._peak_equity = portfolio_value

    def _current_drawdown(self, portfolio_value: float) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - portfolio_value) / self._peak_equity
