#!/usr/bin/env python3
"""
Risk Manager
Portfolio-level risk management and monitoring
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class RiskLimits:
    """Risk configuration"""
    max_portfolio_drawdown: float = 0.15  # 15% max drawdown
    max_position_size_pct: float = 0.10   # 10% max per position
    max_daily_loss_pct: float = 0.05      # 5% max daily loss
    max_open_positions: int = 5           # Max 5 concurrent positions
    min_cash_reserve_pct: float = 0.20    # Keep 20% cash

class RiskManager:
    """Manages portfolio risk"""
    
    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.initial_portfolio_value: Optional[float] = None
        self.daily_pnl: float = 0.0
        self.peak_portfolio_value: float = 0.0
        
        logger.info("Risk Manager initialized")
    
    def check_portfolio_health(self, portfolio_value: float) -> bool:
        """Check if portfolio is within risk limits"""
        # Track initial value
        if self.initial_portfolio_value is None:
            self.initial_portfolio_value = portfolio_value
            self.peak_portfolio_value = portfolio_value
            return True
        
        # Update peak value
        if portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = portfolio_value
        
        # Check drawdown
        drawdown = (self.peak_portfolio_value - portfolio_value) / self.peak_portfolio_value
        if drawdown > self.limits.max_portfolio_drawdown:
            logger.error(f"Max drawdown exceeded: {drawdown:.2%}")
            return False
        
        # Check daily loss
        daily_return = (portfolio_value - self.initial_portfolio_value) / self.initial_portfolio_value
        if daily_return < -self.limits.max_daily_loss_pct:
            logger.error(f"Max daily loss exceeded: {daily_return:.2%}")
            return False
        
        return True
    
    def check_position_size(self, position_value: float, portfolio_value: float) -> bool:
        """Check if position size is within limits"""
        position_pct = position_value / portfolio_value
        
        if position_pct > self.limits.max_position_size_pct:
            logger.warning(f"Position size {position_pct:.2%} exceeds limit {self.limits.max_position_size_pct:.2%}")
            return False
        
        return True
    
    def check_cash_reserve(self, cash: float, portfolio_value: float) -> bool:
        """Check if enough cash is reserved"""
        cash_pct = cash / portfolio_value
        
        if cash_pct < self.limits.min_cash_reserve_pct:
            logger.warning(f"Cash reserve {cash_pct:.2%} below minimum {self.limits.min_cash_reserve_pct:.2%}")
            return False
        
        return True
    
    def can_open_position(self, num_open_positions: int) -> bool:
        """Check if we can open a new position"""
        if num_open_positions >= self.limits.max_open_positions:
            logger.info(f"Max positions reached: {num_open_positions}")
            return False
        
        return True
    
    def calculate_stop_loss(self, entry_price: float, side: str = 'long') -> float:
        """Calculate stop-loss price"""
        if side == 'long':
            return entry_price * 0.98  # 2% stop-loss
        else:
            return entry_price * 1.02  # 2% stop-loss for short
    
    def calculate_take_profit(self, entry_price: float, side: str = 'long') -> float:
        """Calculate take-profit price"""
        if side == 'long':
            return entry_price * 1.04  # 4% take-profit (2:1 ratio)
        else:
            return entry_price * 0.96  # 4% take-profit for short
    
    def get_risk_summary(self, portfolio_value: float) -> Dict:
        """Get current risk metrics summary"""
        drawdown = 0.0
        if self.peak_portfolio_value > 0:
            drawdown = (self.peak_portfolio_value - portfolio_value) / self.peak_portfolio_value
        
        return {
            'current_drawdown': drawdown,
            'max_drawdown_limit': self.limits.max_portfolio_drawdown,
            'daily_pnl': self.daily_pnl,
            'peak_value': self.peak_portfolio_value,
            'initial_value': self.initial_portfolio_value
        }
