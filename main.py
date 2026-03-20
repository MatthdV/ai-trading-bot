#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point (Synchronous)
Automated trading with MeanReversion + Momentum strategies (backtested).
"""

import os
import sys
import time
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.alpaca_client import AlpacaClient
from core.risk_manager import RiskManager
from strategies.base import BaseStrategy, Direction, Position, Signal
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy

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

# Watchlist — same liquid stocks as before
SYMBOLS = [
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
    'NVDA', 'META', 'NFLX', 'AMD', 'CRM',
    'BABA', 'UBER', 'COIN', 'PLTR', 'RKLB',
]


class TradingBot:
    """Main trading bot orchestrator"""

    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.alpaca = AlpacaClient()
        self.risk_manager = RiskManager()
        self.strategies: list[BaseStrategy] = [
            MeanReversionStrategy(),
            MomentumStrategy({"filter_market_hours": True}),
        ]
        self.symbols = SYMBOLS
        self.is_running = False

        strategy_names = ", ".join(s.name for s in self.strategies)
        logger.info(f"Trading Bot initialized in {mode.upper()} mode")
        print(f"Trading Bot started in {mode.upper()} mode")
        print(f"   Capital paper: $100,000")
        print(f"   Strategies: {strategy_names}")
        print(f"   Symbols watched: {len(self.symbols)}")
        print(f"   Waiting for US market open...")

    # ------------------------------------------------------------------
    # Signal scanning
    # ------------------------------------------------------------------

    def _fetch_and_analyze(
        self, symbol: str
    ) -> list[tuple[Signal, BaseStrategy]]:
        """Fetch OHLCV for *symbol* and run all strategies. Returns actionable results."""
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
                        # Check exit
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
                            break  # one exit signal per symbol is enough
                    else:
                        # Check entry
                        if strategy.should_enter(signal):
                            size = strategy.calculate_position_size(signal, portfolio_value)
                            entries.append((symbol, signal, strategy, size))
                            break  # first strategy to trigger wins

        return entries, exits

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main trading loop"""
        self.is_running = True

        try:
            while self.is_running:
                if not self.alpaca.is_market_open():
                    logger.info("Market closed, waiting...")
                    print(f"{datetime.now().strftime('%H:%M')} - Market closed, next check in 5 min")
                    time.sleep(300)
                    continue

                print(f"\n{datetime.now().strftime('%H:%M:%S')} - Market OPEN")

                # Account info
                account = self.alpaca.get_account()
                portfolio_value = float(account.get('portfolio_value', 0))
                cash = float(account.get('cash', account.get('buying_power', 0)))

                print(f"Portfolio: ${portfolio_value:,.2f}")

                # Risk check
                if not self.risk_manager.check_portfolio_health(portfolio_value):
                    logger.warning("Risk limit reached, stopping...")
                    print("Risk limit reached, stopping bot")
                    break

                # Current positions as {symbol: position_dict}
                positions_list = self.alpaca.get_positions()
                current_positions = {
                    p.get('symbol', ''): p for p in positions_list
                }

                # Scan symbols
                entries, exits = self._scan_all_symbols(portfolio_value, current_positions)

                # --- Process exits first ---
                for symbol, signal in exits:
                    self._execute_sell(symbol, signal)

                # --- Process entries ---
                # Refresh position count after exits
                open_count = len(current_positions) - len(exits)

                if not entries:
                    print("No entry signals")
                else:
                    print(f"{len(entries)} entry signal(s) detected")

                for symbol, signal, strategy, position_size in entries:
                    price = signal.metadata.get('price', 0.0)
                    print(f"\n   {symbol}: {signal.direction.value} "
                          f"(strength: {signal.strength:.0%}, strategy: {strategy.name})")
                    print(f"   Price: ${price:.2f} | {_format_metadata(signal)}")

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
            logger.error(f"Error in trading loop: {e}")
            print(f"Error: {e}")
            self.stop()
            raise

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    def _execute_buy(self, symbol: str, qty_dollars: float, signal: Signal) -> None:
        """Execute buy order with bracket (stop-loss + take-profit)."""
        try:
            # Check if we already have a position
            position = self.alpaca.get_position(symbol)
            if position:
                print(f"      Already in position on {symbol}, skipped")
                return

            price = signal.metadata.get('price', 0.0)
            if price <= 0:
                logger.error(f"Invalid price for {symbol}: {price}")
                return

            shares = int(qty_dollars / price)
            if shares < 1:
                print(f"      Position too small for {symbol} (${qty_dollars:.2f})")
                return

            # Market buy order
            self.alpaca.submit_order(
                symbol=symbol,
                qty=shares,
                side='buy',
                type='market',
                time_in_force='day',
            )

            # Protective stop-loss (-2%)
            stop_loss = round(price * 0.98, 2)
            self.alpaca.submit_order(
                symbol=symbol,
                qty=shares,
                side='sell',
                type='stop',
                time_in_force='gtc',
                stop_price=stop_loss,
            )

            # Take-profit (+4%)
            take_profit = round(price * 1.04, 2)
            self.alpaca.submit_order(
                symbol=symbol,
                qty=shares,
                side='sell',
                type='limit',
                time_in_force='gtc',
                limit_price=take_profit,
            )

            print(f"      BUY {symbol}: {shares} shares @ ${price:.2f}")
            print(f"         Stop: ${stop_loss:.2f} (-2%) | Target: ${take_profit:.2f} (+4%)")
            logger.info(
                f"Buy executed: {symbol} x {shares} @ ${price:.2f} "
                f"(strategy={signal.strategy_name}, strength={signal.strength:.2f})"
            )

        except Exception as e:
            logger.error(f"Error buying {symbol}: {e}")
            print(f"      Error buying {symbol}: {e}")

    def _execute_sell(self, symbol: str, signal: Signal) -> None:
        """Execute sell order to close an existing position."""
        try:
            position = self.alpaca.get_position(symbol)
            if not position:
                print(f"      No position on {symbol}, skipped")
                return

            qty = abs(int(float(position.get('qty', 0))))
            if qty < 1:
                return

            self.alpaca.submit_order(
                symbol=symbol,
                qty=qty,
                side='sell',
                type='market',
                time_in_force='day',
            )

            entry_price = float(position.get('avg_entry_price', 0))
            current_price = signal.metadata.get('price', 0.0)
            pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price else 0.0

            print(f"      SELL {symbol}: {qty} shares @ ${current_price:.2f}")
            print(f"         Entry: ${entry_price:.2f} | P&L: {pnl_pct:+.2f}%")
            logger.info(f"Sell executed: {symbol} x {qty}, P&L: {pnl_pct:.2f}%")

        except Exception as e:
            logger.error(f"Error selling {symbol}: {e}")
            print(f"      Error selling {symbol}: {e}")

    def stop(self) -> None:
        """Stop the trading bot"""
        self.is_running = False
        print("Trading Bot stopped")
        logger.info("Trading Bot stopped")


def _format_metadata(signal: Signal) -> str:
    """Format key signal metadata for display."""
    meta = signal.metadata
    parts: list[str] = []
    if 'rsi' in meta:
        parts.append(f"RSI: {meta['rsi']:.1f}")
    if 'zscore' in meta:
        parts.append(f"Z: {meta['zscore']:.2f}")
    if 'adx' in meta:
        parts.append(f"ADX: {meta['adx']:.1f}")
    if 'macd' in meta:
        parts.append(f"MACD: {meta['macd']:.4f}")
    if 'relative_volume' in meta:
        parts.append(f"RVol: {meta['relative_volume']:.1f}")
    return " | ".join(parts) if parts else "no metadata"


def main() -> None:
    """Entry point"""
    parser = argparse.ArgumentParser(description='AI Trading Bot')
    parser.add_argument('--mode', choices=['paper', 'live'], default='paper',
                        help='Trading mode (paper or live)')
    args = parser.parse_args()

    bot = TradingBot(mode=args.mode)

    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        bot.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        bot.stop()
        sys.exit(1)


if __name__ == '__main__':
    main()
