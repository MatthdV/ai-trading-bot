#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point (Synchronous)
Automated trading with RSI + MACD + Kelly Criterion
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.alpaca_client import AlpacaClient
from core.risk_manager import RiskManager
from core.position_sizer import KellyPositionSizer
from strategies.rsi_macd_kelly import RSIMACDKellyStrategy

# Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingBot:
    """Main trading bot orchestrator"""
    
    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.alpaca = AlpacaClient()
        self.risk_manager = RiskManager()
        self.position_sizer = KellyPositionSizer()
        self.strategy = RSIMACDKellyStrategy()
        self.is_running = False
        
        logger.info(f"🚀 Trading Bot initialized in {mode.upper()} mode")
        print(f"🚀 Trading Bot démarré en mode {mode.upper()}")
        print(f"   Capital paper: $100,000")
        print(f"   Stratégie: RSI + MACD + Kelly")
        print(f"   Stocks surveillés: {len(self.strategy.symbols)}")
        print(f"   En attente de l'ouverture du marché US...")
    
    def run(self):
        """Main trading loop"""
        self.is_running = True
        
        try:
            while self.is_running:
                # Check if market is open
                if not self.alpaca.is_market_open():
                    logger.info("⏳ Marché fermé, attente...")
                    print(f"⏳ {datetime.now().strftime('%H:%M')} - Marché fermé, prochaine vérification dans 5 min")
                    time.sleep(300)  # Check every 5 minutes
                    continue
                
                print(f"\n🔔 {datetime.now().strftime('%H:%M:%S')} - Marché OUVERT")
                
                # Get account info
                account = self.alpaca.get_account()
                portfolio_value = float(account.portfolio_value)
                
                print(f"💰 Portfolio: ${portfolio_value:,.2f}")
                
                # Risk check
                if not self.risk_manager.check_portfolio_health(portfolio_value):
                    logger.warning("⚠️ Risk limit reached, stopping...")
                    print("⚠️ Limite de risque atteinte, arrêt du bot")
                    break
                
                # Run strategy
                signals = self.strategy.generate_signals(self.alpaca)
                
                if not signals:
                    print("📊 Aucun signal de trading pour le moment")
                else:
                    print(f"📊 {len(signals)} signaux détectés")
                
                for signal in signals:
                    symbol = signal.symbol
                    action = signal.action
                    confidence = signal.confidence
                    
                    print(f"\n   {symbol}: {action.upper()} (confiance: {confidence:.0%})")
                    print(f"   Prix: ${signal.price:.2f} | RSI: {signal.rsi:.1f} | MACD: {signal.macd:.4f}")
                    
                    # Calculate position size using Kelly Criterion
                    position_size = self.position_sizer.calculate(
                        portfolio_value=portfolio_value,
                        confidence=confidence,
                        win_rate=0.55,
                        avg_win=0.04,
                        avg_loss=0.02
                    )
                    
                    # Execute trade
                    if action == 'buy':
                        self._execute_buy(symbol, position_size, signal)
                    elif action == 'sell':
                        self._execute_sell(symbol, signal)
                
                # Show open positions
                positions = self.alpaca.get_positions()
                if positions:
                    print(f"\n📈 Positions ouvertes: {len(positions)}")
                    for pos in positions:
                        pnl_pct = (float(pos.current_price) - float(pos.avg_entry_price)) / float(pos.avg_entry_price) * 100
                        emoji = "🟢" if pnl_pct > 0 else "🔴"
                        print(f"   {emoji} {pos.symbol}: {pos.qty} @ ${float(pos.avg_entry_price):.2f} (P&L: {pnl_pct:+.2f}%)")
                
                # Wait for next iteration
                print(f"\n⏳ Prochain scan dans 5 minutes...")
                time.sleep(300)  # Check every 5 minutes
                
        except KeyboardInterrupt:
            print("\n🛑 Arrêt demandé par l'utilisateur")
            self.stop()
        except Exception as e:
            logger.error(f"❌ Error in trading loop: {e}")
            print(f"❌ Erreur: {e}")
            self.stop()
            raise
    
    def _execute_buy(self, symbol: str, qty_dollars: float, signal):
        """Execute buy order"""
        try:
            # Check if we already have position
            position = self.alpaca.get_position(symbol)
            if position:
                print(f"      ℹ️ Déjà en position sur {symbol}, ignoré")
                return
            
            # Calculate shares
            shares = int(qty_dollars / signal.price)
            if shares < 1:
                print(f"      ℹ️ Position trop petite pour {symbol} (${qty_dollars:.2f})")
                return
            
            # Submit order
            order = self.alpaca.submit_order(
                symbol=symbol,
                qty=shares,
                side='buy',
                type='market',
                time_in_force='day'
            )
            
            # Set stop-loss and take-profit levels
            stop_loss = signal.price * 0.98
            take_profit = signal.price * 1.04
            
            print(f"      🟢 ACHAT {symbol}: {shares} actions @ ${signal.price:.2f}")
            print(f"         Stop: ${stop_loss:.2f} (-2%) | Target: ${take_profit:.2f} (+4%)")
            logger.info(f"Buy executed: {symbol} x {shares} @ ${signal.price:.2f}")
            
        except Exception as e:
            logger.error(f"Error buying {symbol}: {e}")
            print(f"      ❌ Erreur achat {symbol}: {e}")
    
    def _execute_sell(self, symbol: str, signal):
        """Execute sell order"""
        try:
            # Get current position
            position = self.alpaca.get_position(symbol)
            if not position:
                print(f"      ℹ️ Pas de position sur {symbol}, ignoré")
                return
            
            qty = abs(int(float(position.qty)))
            
            # Submit order
            order = self.alpaca.submit_order(
                symbol=symbol,
                qty=qty,
                side='sell',
                type='market',
                time_in_force='day'
            )
            
            # Calculate P&L
            entry_price = float(position.avg_entry_price)
            current_price = signal.price
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
            
            emoji = "✅" if pnl_pct > 0 else "❌"
            print(f"      🔴 VENTE {symbol}: {qty} actions @ ${current_price:.2f}")
            print(f"         Entrée: ${entry_price:.2f} | P&L: {emoji} {pnl_pct:+.2f}%")
            logger.info(f"Sell executed: {symbol} x {qty}, P&L: {pnl_pct:.2f}%")
            
        except Exception as e:
            logger.error(f"Error selling {symbol}: {e}")
            print(f"      ❌ Erreur vente {symbol}: {e}")
    
    def stop(self):
        """Stop the trading bot"""
        self.is_running = False
        print("🛑 Trading Bot arrêté")
        logger.info("Trading Bot stopped")

def main():
    """Entry point"""
    parser = argparse.ArgumentParser(description='AI Trading Bot')
    parser.add_argument('--mode', choices=['paper', 'live'], default='paper',
                       help='Trading mode (paper or live)')
    args = parser.parse_args()
    
    bot = TradingBot(mode=args.mode)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Au revoir!")
        bot.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"💥 Erreur fatale: {e}")
        bot.stop()
        sys.exit(1)

if __name__ == '__main__':
    main()
