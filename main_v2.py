#!/usr/bin/env python3
"""
Enhanced Trading Bot with News Analysis
Uses MeanReversion + Momentum strategies (backtested) + news sentiment filter.
"""

import os
import sys
import time
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.alpaca_client import AlpacaClient
from core.risk_manager import RiskManager
from strategies.base import BaseStrategy, Direction, Position, Signal
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
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

# Watchlist
SYMBOLS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
    'NVDA', 'META', 'NFLX', 'AMD', 'CRM',
    'BABA', 'UBER', 'COIN', 'PLTR', 'RKLB',
]

# Map Direction to the string expected by news_analyzer
_DIRECTION_ACTION = {
    Direction.LONG: 'buy',
    Direction.SHORT: 'sell',
    Direction.NEUTRAL: 'hold',
}


class TradingBot:
    """Trading bot with news sentiment filter"""

    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.alpaca = AlpacaClient()
        self.risk_manager = RiskManager()
        self.strategies: list[BaseStrategy] = [
            MeanReversionStrategy(),
            MomentumStrategy({"filter_market_hours": True}),
        ]
        self.symbols = SYMBOLS
        self.news_analyzer = get_analyzer()
        self.is_running = False

        strategy_names = ", ".join(s.name for s in self.strategies)
        logger.info(f"Trading Bot v2 initialized in {mode.upper()} mode")
        print(f"\nTrading Bot v2.0 started")
        print(f"   Mode: {mode.upper()}")
        print(f"   Capital: $100,000")
        print(f"   Strategies: {strategy_names} + News Sentiment")
        print(f"   Symbols: {len(self.symbols)}")

        self._check_market_conditions()

    # ------------------------------------------------------------------
    # Market conditions
    # ------------------------------------------------------------------

    def _check_market_conditions(self) -> None:
        print("\nMarket conditions analysis...")

        sentiment = self.news_analyzer.get_market_sentiment()
        print(f"   Overall sentiment: {sentiment['overall_sentiment']:+.2f}")

        events = self.news_analyzer.check_upcoming_events(7)
        if events:
            print(f"   Important events this week: {len(events)}")
            for event in events[:3]:
                print(f"      - {event.date}: {event.description}")
        else:
            print(f"   No major event this week")

        rec = self.news_analyzer.get_trading_recommendation()
        print(f"\n   Recommendation: {rec['reason']}")
        if not rec['trade_allowed']:
            print(f"   TRADING SUSPENDED")
        else:
            print(f"   Max position: {rec['max_position_size_pct']*100:.0f}%")
            print(f"   Min cash: {rec['cash_reserve_pct']*100:.0f}%")

        print(f"\nWaiting for market open...")

    # ------------------------------------------------------------------
    # Signal scanning
    # ------------------------------------------------------------------

    def _fetch_and_analyze(self, symbol: str) -> list[tuple[Signal, BaseStrategy]]:
        """Fetch OHLCV and run all strategies for *symbol*."""
        results: list[tuple[Signal, BaseStrategy]] = []
        try:
            df = self.alpaca.get_ohlcv_dataframe(symbol, timeframe='1D', limit=100)
            for strategy in self.strategies:
                signal = strategy.analyze(symbol, df)
                results.append((signal, strategy))
        except Exception as exc:
            logger.error(f"Error analyzing {symbol}: {exc}")
        return results

    def _scan_all_symbols(
        self,
        portfolio_value: float,
        current_positions: dict[str, dict],
        news_size_factor: float,
    ) -> tuple[list[tuple[str, Signal, BaseStrategy, float]], list[tuple[str, Signal]]]:
        """Scan symbols in parallel. Returns (entries, exits)."""
        entries: list[tuple[str, Signal, BaseStrategy, float]] = []
        exits: list[tuple[str, Signal]] = []

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(self._fetch_and_analyze, sym): sym
                for sym in self.symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results = future.result()
                except Exception as exc:
                    logger.error(f"Error scanning {symbol}: {exc}")
                    continue

                for signal, strategy in results:
                    if symbol in current_positions:
                        pos_data = current_positions[symbol]
                        qty = float(pos_data.get('qty', 0))
                        position = Position(
                            symbol=symbol,
                            direction=Direction.LONG if qty > 0 else Direction.SHORT,
                            entry_price=float(pos_data.get('avg_entry_price', 0)),
                            quantity=abs(qty),
                        )
                        if strategy.should_exit(signal, position):
                            exits.append((symbol, signal))
                            break
                    else:
                        if strategy.should_enter(signal):
                            # Scale position size by news recommendation factor
                            size = strategy.calculate_position_size(
                                signal, portfolio_value * news_size_factor
                            )
                            entries.append((symbol, signal, strategy, size))
                            break

        return entries, exits

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.is_running = True

        try:
            while self.is_running:
                if not self.alpaca.is_market_open():
                    logger.info("Market closed, waiting...")
                    time.sleep(300)
                    continue

                print(f"\n{'='*60}")
                print(f"{datetime.now().strftime('%H:%M:%S')} - Market OPEN")
                print(f"{'='*60}")

                # News recommendation gate
                rec = self.news_analyzer.get_trading_recommendation()
                if not rec['trade_allowed']:
                    print(f"Trading suspended: {rec['reason']}")
                    time.sleep(600)
                    continue

                news_size_factor: float = rec.get('max_position_size_pct', 1.0)

                # Account info
                account = self.alpaca.get_account()
                portfolio_value = float(account.get('portfolio_value', 0))
                cash = float(account.get('cash', account.get('buying_power', 0)))

                print(f"Portfolio: ${portfolio_value:,.2f}")

                if not self.risk_manager.check_portfolio_health(portfolio_value):
                    print("Risk limit reached!")
                    break

                positions_list = self.alpaca.get_positions()
                current_positions = {p.get('symbol', ''): p for p in positions_list}

                entries, exits = self._scan_all_symbols(
                    portfolio_value, current_positions, news_size_factor
                )

                # --- Process exits first (no news filter on exits) ---
                for symbol, signal in exits:
                    self._execute_sell(symbol, signal)

                # --- Process entries with news filter ---
                open_count = len(current_positions) - len(exits)

                if not entries:
                    print("No entry signals")
                else:
                    print(f"\n{len(entries)} entry signal(s) — applying news filter...")
                    print(f"{'─'*60}")

                for symbol, signal, strategy, position_size in entries:
                    action = _DIRECTION_ACTION[signal.direction]

                    # News sentiment adjustment
                    adj_confidence, sentiment, reason = self.news_analyzer.adjust_signal_confidence(
                        symbol, signal.strength, action
                    )

                    price = signal.metadata.get('price', 0.0)
                    print(f"\n   {symbol}: {signal.direction.value} ({strategy.name})")
                    print(f"   Price: ${price:.2f}")
                    print(f"   Technical strength: {signal.strength:.0%}")
                    print(f"   News sentiment: {sentiment:+.2f}")
                    print(f"   Adjusted confidence: {adj_confidence:.0%}")
                    print(f"   Reason: {reason}")

                    # Skip if news dragged confidence too low
                    if adj_confidence < 0.5:
                        print(f"   Skipped (confidence too low after news adjustment)")
                        continue

                    # Risk checks
                    if not self.risk_manager.can_open_position(open_count):
                        print(f"      Max positions reached, {symbol} skipped")
                        continue
                    if not self.risk_manager.check_cash_reserve(cash, portfolio_value):
                        print(f"      Insufficient cash reserve, {symbol} skipped")
                        continue
                    if not self.risk_manager.check_position_size(position_size, portfolio_value):
                        print(f"      Position too large, {symbol} skipped")
                        continue

                    self._execute_buy(symbol, position_size, signal)
                    open_count += 1

                # Show open positions
                positions_list = self.alpaca.get_positions()
                if positions_list:
                    print(f"\nOpen positions: {len(positions_list)}")
                    for pos in positions_list:
                        entry_px = float(pos.get('avg_entry_price', 0))
                        cur_px = float(pos.get('current_price', entry_px))
                        pnl_pct = ((cur_px - entry_px) / entry_px * 100) if entry_px else 0.0
                        arrow = "+" if pnl_pct > 0 else ""
                        print(f"   {pos.get('symbol')}: {pos.get('qty')} "
                              f"@ ${entry_px:.2f} (P&L: {arrow}{pnl_pct:.2f}%)")

                print(f"\nNext scan in 5 minutes...")
                time.sleep(300)

        except KeyboardInterrupt:
            print("\nShutdown requested")
            self.stop()
        except Exception as e:
            logger.error(f"Error: {e}")
            print(f"Error: {e}")
            self.stop()
            raise

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def _execute_buy(self, symbol: str, qty_dollars: float, signal: Signal) -> None:
        try:
            position = self.alpaca.get_position(symbol)
            if position:
                print(f"      Already in position on {symbol}")
                return

            price = signal.metadata.get('price', 0.0)
            if price <= 0:
                logger.error(f"Invalid price for {symbol}: {price}")
                return

            shares = int(qty_dollars / price)
            if shares < 1:
                print(f"      Position too small for {symbol} (${qty_dollars:.2f})")
                return

            self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='buy',
                type='market', time_in_force='day',
            )

            stop_loss = round(price * 0.98, 2)
            self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='sell',
                type='stop', time_in_force='gtc', stop_price=stop_loss,
            )

            take_profit = round(price * 1.04, 2)
            self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='sell',
                type='limit', time_in_force='gtc', limit_price=take_profit,
            )

            print(f"      BUY {symbol}: {shares} @ ${price:.2f}")
            print(f"         Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f}")
            logger.info(
                f"Buy: {symbol} x {shares} @ ${price:.2f} "
                f"(strategy={signal.strategy_name}, strength={signal.strength:.2f})"
            )

        except Exception as e:
            logger.error(f"Error buying {symbol}: {e}")
            print(f"      Error: {e}")

    def _execute_sell(self, symbol: str, signal: Signal) -> None:
        try:
            position = self.alpaca.get_position(symbol)
            if not position:
                print(f"      No position on {symbol}")
                return

            qty = abs(int(float(position.get('qty', 0))))
            if qty < 1:
                return

            self.alpaca.submit_order(
                symbol=symbol, qty=qty, side='sell',
                type='market', time_in_force='day',
            )

            entry_price = float(position.get('avg_entry_price', 0))
            current_price = signal.metadata.get('price', 0.0)
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0

            print(f"      SELL {symbol}: {qty} @ ${current_price:.2f}")
            print(f"         P&L: {pnl_pct:+.2f}%")
            logger.info(f"Sell: {symbol} x {qty}, P&L: {pnl_pct:.2f}%")

        except Exception as e:
            logger.error(f"Error selling {symbol}: {e}")
            print(f"      Error: {e}")

    def stop(self) -> None:
        self.is_running = False
        print("Trading Bot stopped")
        logger.info("Bot stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description='AI Trading Bot with News Analysis')
    parser.add_argument('--mode', choices=['paper', 'live'], default='paper')
    args = parser.parse_args()

    bot = TradingBot(mode=args.mode)

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        bot.stop()
    except Exception as e:
        logger.error(f"Fatal: {e}")
        bot.stop()
        sys.exit(1)


if __name__ == '__main__':
    main()
