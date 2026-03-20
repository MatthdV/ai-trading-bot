#!/usr/bin/env python3
"""
Backtest Module
Test strategy on historical data
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.alpaca_client import AlpacaClient
from core.position_sizer import KellyPositionSizer
from strategies._legacy_rsi_macd_kelly import RSIMACDKellyStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Backtester:
    """Backtest trading strategy"""
    
    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions = {}
        self.trades = []
        self.equity_curve = []
        
    def run(self, symbol: str, days: int = 180):
        """Run backtest for a symbol"""
        print(f"\n📊 Backtest: {symbol}")
        print(f"   Période: {days} jours")
        print(f"   Capital initial: ${self.initial_capital:,.2f}")
        
        # Get historical data
        client = AlpacaClient()
        end = datetime.now()
        start = end - timedelta(days=days)
        
        bars = client.get_bars(
            symbol=symbol,
            timeframe='1D',
            limit=days,
            start=start.isoformat(),
            end=end.isoformat()
        )
        
        if len(bars) < 50:
            print(f"   ❌ Données insuffisantes: {len(bars)} jours")
            return None
        
        print(f"   Données: {len(bars)} jours")
        
        # Simulate trading
        sizer = KellyPositionSizer(fractional_kelly=0.25)
        
        for i in range(50, len(bars)):
            current_bar = bars[i]
            price = current_bar['c']
            date = current_bar['t']
            
            # Get historical window
            window = bars[i-50:i+1]
            closes = [b['c'] for b in window]
            
            # Calculate indicators
            strategy = RSIMACDKellyStrategy()
            rsi = strategy._calculate_rsi(closes)
            macd, macd_signal, _ = strategy._calculate_macd(closes)
            ema_20 = strategy._calculate_ema(closes, 20)
            ema_50 = strategy._calculate_ema(closes, 50)
            
            # Check for signals
            if symbol not in self.positions:
                # Buy signal
                if rsi < 30 and macd > macd_signal and ema_20 > ema_50:
                    position_size = sizer.calculate(
                        portfolio_value=self.capital,
                        confidence=0.7,
                        win_rate=0.55,
                        avg_win=0.04,
                        avg_loss=0.02
                    )
                    shares = int(position_size / price)
                    
                    if shares > 0:
                        cost = shares * price
                        self.capital -= cost
                        self.positions[symbol] = {
                            'shares': shares,
                            'entry_price': price,
                            'entry_date': date,
                            'stop_loss': price * 0.98,
                            'take_profit': price * 1.04
                        }
                        
                        self.trades.append({
                            'date': date,
                            'symbol': symbol,
                            'action': 'buy',
                            'shares': shares,
                            'price': price,
                            'value': cost
                        })
            
            else:
                position = self.positions[symbol]
                
                # Check exit conditions
                exit_trade = False
                exit_price = price
                exit_reason = ''
                
                # Stop loss
                if price <= position['stop_loss']:
                    exit_trade = True
                    exit_reason = 'stop_loss'
                
                # Take profit
                elif price >= position['take_profit']:
                    exit_trade = True
                    exit_reason = 'take_profit'
                
                # Sell signal
                elif rsi > 70 and macd < macd_signal and ema_20 < ema_50:
                    exit_trade = True
                    exit_reason = 'signal'
                
                if exit_trade:
                    proceeds = position['shares'] * exit_price
                    pnl = proceeds - (position['shares'] * position['entry_price'])
                    pnl_pct = (exit_price - position['entry_price']) / position['entry_price'] * 100
                    
                    self.capital += proceeds
                    
                    self.trades.append({
                        'date': date,
                        'symbol': symbol,
                        'action': 'sell',
                        'shares': position['shares'],
                        'price': exit_price,
                        'value': proceeds,
                        'pnl': pnl,
                        'pnl_pct': pnl_pct,
                        'reason': exit_reason,
                        'holding_days': (datetime.fromisoformat(date.replace('Z', '+00:00')) - 
                                       datetime.fromisoformat(position['entry_date'].replace('Z', '+00:00'))).days
                    })
                    
                    del self.positions[symbol]
            
            # Record equity
            portfolio_value = self.capital
            for sym, pos in self.positions.items():
                portfolio_value += pos['shares'] * price
            
            self.equity_curve.append({
                'date': date,
                'value': portfolio_value
            })
        
        # Close any open positions at last price
        for sym, pos in self.positions.items():
            proceeds = pos['shares'] * bars[-1]['c']
            self.capital += proceeds
        
        self.positions = {}
        
        return self._calculate_metrics()
    
    def _calculate_metrics(self) -> Dict:
        """Calculate performance metrics"""
        final_value = self.equity_curve[-1]['value'] if self.equity_curve else self.initial_capital
        total_return = (final_value - self.initial_capital) / self.initial_capital * 100
        
        # Calculate daily returns
        returns = []
        for i in range(1, len(self.equity_curve)):
            ret = (self.equity_curve[i]['value'] - self.equity_curve[i-1]['value']) / self.equity_curve[i-1]['value']
            returns.append(ret)
        
        # Sharpe ratio (simplified, assuming risk-free rate = 0)
        if len(returns) > 1:
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = variance ** 0.5
            sharpe = (avg_return / std_dev) * (252 ** 0.5) if std_dev > 0 else 0
        else:
            sharpe = 0
        
        # Win rate
        closed_trades = [t for t in self.trades if t['action'] == 'sell']
        winning_trades = [t for t in closed_trades if t.get('pnl', 0) > 0]
        win_rate = len(winning_trades) / len(closed_trades) * 100 if closed_trades else 0
        
        # Max drawdown
        peak = self.initial_capital
        max_dd = 0
        for point in self.equity_curve:
            if point['value'] > peak:
                peak = point['value']
            dd = (peak - point['value']) / peak
            if dd > max_dd:
                max_dd = dd
        
        return {
            'initial_capital': self.initial_capital,
            'final_value': final_value,
            'total_return': total_return,
            'sharpe_ratio': sharpe,
            'win_rate': win_rate,
            'max_drawdown': max_dd * 100,
            'total_trades': len(closed_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(closed_trades) - len(winning_trades)
        }

def main():
    """Run backtest"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Backtest trading strategy')
    parser.add_argument('--symbol', default='AAPL', help='Stock symbol to backtest')
    parser.add_argument('--days', type=int, default=180, help='Number of days to backtest')
    args = parser.parse_args()
    
    backtester = Backtester(initial_capital=100000)
    results = backtester.run(args.symbol, args.days)
    
    if results:
        print("\n" + "="*50)
        print("📈 RÉSULTATS DU BACKTEST")
        print("="*50)
        print(f"Capital initial:    ${results['initial_capital']:>12,.2f}")
        print(f"Capital final:      ${results['final_value']:>12,.2f}")
        print(f"Rendement total:    {results['total_return']:>12.2f}%")
        print(f"Sharpe ratio:       {results['sharpe_ratio']:>12.2f}")
        print(f"Max drawdown:       {results['max_drawdown']:>12.2f}%")
        print(f"Win rate:           {results['win_rate']:>12.1f}%")
        print(f"Trades gagnants:    {results['winning_trades']:>12}")
        print(f"Trades perdants:    {results['losing_trades']:>12}")
        print(f"Total trades:       {results['total_trades']:>12}")
        print("="*50)

if __name__ == '__main__':
    main()
