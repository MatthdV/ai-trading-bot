#!/usr/bin/env python3
"""
Enhanced Trading Bot with News Analysis
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.alpaca_client import AlpacaClient
from core.risk_manager import RiskManager
from core.position_sizer import KellyPositionSizer
from strategies.rsi_macd_kelly import RSIMACDKellyStrategy
from analysis.news_analyzer import get_analyzer

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
    """Main trading bot with news analysis"""
    
    def __init__(self, mode='paper'):
        self.mode = mode
        self.alpaca = AlpacaClient()
        self.risk_manager = RiskManager()
        self.position_sizer = KellyPositionSizer()
        self.strategy = RSIMACDKellyStrategy()
        self.news_analyzer = get_analyzer()
        self.is_running = False
        
        logger.info(f"🚀 Trading Bot v2.0 (with News Analysis) initialized in {mode.upper()} mode")
        print(f"\n🚀 Trading Bot v2.0 démarré")
        print(f"   Mode: {mode.upper()}")
        print(f"   Capital: $100,000")
        print(f"   Fonctionnalités: RSI/MACD + News Sentiment")
        print(f"   Stocks: {len(self.strategy.symbols)}")
        
        # Check market conditions
        self._check_market_conditions()
    
    def _check_market_conditions(self):
        """Check overall market conditions before trading"""
        print("\n📊 Analyse des conditions de marché...")
        
        # Get market sentiment
        sentiment = self.news_analyzer.get_market_sentiment()
        print(f"   Sentiment global: {sentiment['overall_sentiment']:+.2f}")
        
        # Check upcoming events
        events = self.news_analyzer.check_upcoming_events(7)
        if events:
            print(f"   ⚠️  Événements importants cette semaine: {len(events)}")
            for event in events[:3]:  # Show first 3
                print(f"      - {event.date}: {event.description}")
        else:
            print(f"   ✅ Pas d'événement majeur cette semaine")
        
        # Get trading recommendation
        rec = self.news_analyzer.get_trading_recommendation()
        print(f"\n   Recommandation: {rec['reason']}")
        if not rec['trade_allowed']:
            print(f"   ⚠️  TRADING SUSPENDU")
        else:
            print(f"   Position max: {rec['max_position_size_pct']*100:.0f}%")
            print(f"   Cash minimum: {rec['cash_reserve_pct']*100:.0f}%")
        
        print(f"\n⏳ En attente de l'ouverture du marché...")
    
    def run(self):
        """Main trading loop"""
        self.is_running = True
        
        try:
            while self.is_running:
                # Check if market is open
                if not self.alpaca.is_market_open():
                    logger.info("Marché fermé, attente...")
                    time.sleep(300)
                    continue
                
                print(f"\n{'='*60}")
                print(f"🔔 {datetime.now().strftime('%H:%M:%S')} - Marché OUVERT")
                print(f"{'='*60}")
                
                # Get market recommendation
                rec = self.news_analyzer.get_trading_recommendation()
                if not rec['trade_allowed']:
                    print(f"⛔ Trading suspendu: {rec['reason']}")
                    time.sleep(600)
                    continue
                
                # Get account info
                account = self.alpaca.get_account()
                portfolio_value = float(account.portfolio_value)
                print(f"💰 Portfolio: ${portfolio_value:,.2f}")
                
                # Risk check
                if not self.risk_manager.check_portfolio_health(portfolio_value):
                    print("⚠️ Limite de risque atteinte!")
                    break
                
                # Generate signals
                signals = self.strategy.generate_signals(self.alpaca)
                
                if not signals:
                    print("📊 Aucun signal technique")
                else:
                    print(f"📊 {len(signals)} signaux techniques détectés")
                    print(f"\n{'─'*60}")
                    print("Analyse News + Technique:")
                    print(f"{'─'*60}")
                
                for signal in signals:
                    symbol = signal.symbol
                    action = signal.action
                    tech_confidence = signal.confidence
                    
                    # Adjust with news sentiment
                    adj_confidence, sentiment, reason = self.news_analyzer.adjust_signal_confidence(
                        symbol, tech_confidence, action
                    )
                    
                    print(f"\n   {symbol}: {action.upper()}")
                    print(f"   Prix: ${signal.price:.2f} | RSI: {signal.rsi:.1f}")
                    print(f"   Conf. technique: {tech_confidence:.0%}")
                    print(f"   Sentiment news: {sentiment:+.2f}")
                    print(f"   Conf. ajustée: {adj_confidence:.0%}")
                    print(f"   Raison: {reason}")
                    
                    # Skip if confidence too low after adjustment
                    if adj_confidence < 0.5:
                        print(f"   ⏭️  Ignoré (confiance trop faible)")
                        continue
                    
                    # Calculate position size with adjustment
                    position_size = self.position_sizer.calculate(
                        portfolio_value=portfolio_value * rec['max_position_size_pct'],
                        confidence=adj_confidence,
                        win_rate=0.55,
                        avg_win=0.04,
                        avg_loss=0.02
                    )
                    
                    # Execute
                    if action == 'buy':
                        self._execute_buy(symbol, position_size, signal)
                    elif action == 'sell':
                        self._execute_sell(symbol, signal)
                
                # Show positions
                positions = self.alpaca.get_positions()
                if positions:
                    print(f"\n📈 Positions ouvertes: {len(positions)}")
                    for pos in positions:
                        pnl_pct = (float(pos.current_price) - float(pos.avg_entry_price)) / float(pos.avg_entry_price) * 100
                        emoji = "🟢" if pnl_pct > 0 else "🔴"
                        print(f"   {emoji} {pos.symbol}: {pos.qty} @ ${float(pos.avg_entry_price):.2f} (P&L: {pnl_pct:+.2f}%)")
                
                print(f"\n⏳ Prochain scan dans 5 minutes...")
                time.sleep(300)
                
        except KeyboardInterrupt:
            print("\n🛑 Arrêt demandé")
            self.stop()
        except Exception as e:
            logger.error(f"Erreur: {e}")
            print(f"❌ Erreur: {e}")
            self.stop()
            raise
    
    def _execute_buy(self, symbol, qty_dollars, signal):
        """Execute buy order"""
        try:
            position = self.alpaca.get_position(symbol)
            if position:
                print(f"      ℹ️ Déjà en position sur {symbol}")
                return
            
            shares = int(qty_dollars / signal.price)
            if shares < 1:
                print(f"      ℹ️ Position trop petite")
                return
            
            order = self.alpaca.submit_order(
                symbol=symbol,
                qty=shares,
                side='buy',
                type='market',
                time_in_force='day'
            )
            
            stop_loss = signal.price * 0.98
            take_profit = signal.price * 1.04
            
            print(f"      🟢 ACHAT {symbol}: {shares} @ ${signal.price:.2f}")
            print(f"         Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f}")
            logger.info(f"Buy: {symbol} x {shares}")
            
        except Exception as e:
            logger.error(f"Error buying {symbol}: {e}")
            print(f"      ❌ Erreur: {e}")
    
    def _execute_sell(self, symbol, signal):
        """Execute sell order"""
        try:
            position = self.alpaca.get_position(symbol)
            if not position:
                print(f"      ℹ️ Pas de position sur {symbol}")
                return
            
            qty = abs(int(float(position.qty)))
            order = self.alpaca.submit_order(
                symbol=symbol,
                qty=qty,
                side='sell',
                type='market',
                time_in_force='day'
            )
            
            entry_price = float(position.avg_entry_price)
            pnl_pct = ((signal.price - entry_price) / entry_price) * 100
            emoji = "✅" if pnl_pct > 0 else "❌"
            
            print(f"      🔴 VENTE {symbol}: {qty} @ ${signal.price:.2f}")
            print(f"         P&L: {emoji} {pnl_pct:+.2f}%")
            logger.info(f"Sell: {symbol} x {qty}, P&L: {pnl_pct:.2f}%")
            
        except Exception as e:
            logger.error(f"Error selling {symbol}: {e}")
            print(f"      ❌ Erreur: {e}")
    
    def stop(self):
        self.is_running = False
        print("🛑 Trading Bot arrêté")
        logger.info("Bot stopped")

def main():
    parser = argparse.ArgumentParser(description='AI Trading Bot with News Analysis')
    parser.add_argument('--mode', choices=['paper', 'live'], default='paper')
    args = parser.parse_args()
    
    bot = TradingBot(mode=args.mode)
    
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\n👋 Au revoir!")
        bot.stop()
    except Exception as e:
        logger.error(f"Fatal: {e}")
        bot.stop()
        sys.exit(1)

if __name__ == '__main__':
    main()
