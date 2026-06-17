#!/usr/bin/env python3
"""
Trading Bot - Main Entry Point (Synchronous)
Automated trading with MeanReversion + Momentum strategies (backtested).
"""

import asyncio
import json
import os
import signal as signal_mod
import sys
import time
import logging
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.alpaca_client import AlpacaClient
from core.state_persistence import StatePersistence
from risk.manager import RiskManager, RiskConfig
from strategies.base import BaseStrategy, Direction, Position, Signal
from notifications.telegram_bot import TelegramNotifier
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


class SyncTelegram:
    """Sync wrapper around the async TelegramNotifier.

    H3: ``risk_alert`` has a disk fallback — if Telegram itself is down
    (network, token expired, rate limit), the alert is appended to
    ``data/critical_alerts.jsonl`` so it is never silently lost. Operators
    can grep that file during post-mortem.
    """

    def __init__(self, data_dir: str | None = None) -> None:
        self._notifier = TelegramNotifier()
        data_path = Path(
            data_dir or os.path.join(os.path.dirname(__file__), "data")
        )
        data_path.mkdir(parents=True, exist_ok=True)
        self._critical_fallback = data_path / "critical_alerts.jsonl"

    def send(self, message: str) -> None:
        try:
            asyncio.run(self._notifier.send_message(message))
        except Exception as e:
            logger.error(f"Telegram send failed: {type(e).__name__}: {e}")

    def trade_alert(self, symbol: str, action: str, qty: float, price: float, pnl: float | None = None) -> None:
        try:
            asyncio.run(self._notifier.send_trade_alert(symbol, action, qty, price, pnl))
        except Exception as e:
            logger.error(
                f"Telegram trade_alert failed: {type(e).__name__}: {e}"
            )

    def risk_alert(self, message: str) -> None:
        try:
            asyncio.run(self._notifier.send_risk_alert(message))
        except Exception as e:
            logger.critical(
                f"Telegram risk_alert FAILED: {type(e).__name__}: {e}"
            )
            # Fallback: persist to disk so the alert is never lost.
            try:
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
                fd = os.open(self._critical_fallback, flags, 0o600)
                with os.fdopen(fd, "a") as f:
                    f.write(json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "message": message,
                        "reason": f"telegram_failed: {type(e).__name__}",
                    }) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                # Last resort — logger.critical above is all we have left.
                pass


class TradingBot:
    """Main trading bot orchestrator"""

    def __init__(self, mode: str = 'paper'):
        self.mode = mode
        self.alpaca = AlpacaClient()
        self.risk_manager = RiskManager(RiskConfig(
            max_position_pct=0.05,
            max_simultaneous_positions=5,
            max_sector_positions=3,
            max_daily_loss_pct=0.02,
            max_portfolio_drawdown_pct=0.10,
            kelly_fraction=0.5,
        ))
        self.strategies: list[BaseStrategy] = [
            MeanReversionStrategy(),
            MomentumStrategy({"filter_market_hours": True}),
        ]
        self.symbols = SYMBOLS
        self.state = StatePersistence()
        self.telegram = SyncTelegram()
        self.is_running = False

        # B2: cross-cycle caches for trailing-stop wiring.
        # open_positions is rebuilt from broker data every cycle, so the
        # stop_loss price and broker stop order id must be kept here.
        self._last_atr: dict[str, float] = {}
        self._stop_order_ids: dict[str, str] = {}
        self._current_stops: dict[str, float] = {}

        # Graceful shutdown on SIGTERM/SIGINT
        signal_mod.signal(signal_mod.SIGTERM, self._handle_shutdown)
        signal_mod.signal(signal_mod.SIGINT, self._handle_shutdown)

        # B3: Crash recovery — reload last state and reconcile with broker.
        # On mismatch we alert but keep running: get_position() in _execute_buy
        # will prevent us from re-opening a symbol the broker already holds.
        self._recover_state()

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
                # B2: cache ATR from any strategy that publishes it so the
                # trailing-stop update loop has something to work with.
                # Dict single-key writes are atomic under CPython's GIL.
                atr = signal.metadata.get('atr', 0.0)
                if atr and atr > 0:
                    self._last_atr[symbol] = float(atr)
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

                # Current positions as {symbol: position_dict}
                positions_list = self.alpaca.get_positions()
                current_positions = {
                    p.get('symbol', ''): p for p in positions_list
                }

                # Build Position objects for risk manager
                open_positions: dict[str, Position] = {}
                for sym, p in current_positions.items():
                    qty = float(p.get('qty', 0))
                    open_positions[sym] = Position(
                        symbol=sym,
                        direction=Direction.LONG if qty > 0 else Direction.SHORT,
                        entry_price=float(p.get('avg_entry_price', 0)),
                        quantity=abs(qty),
                        # B2: pull the last known stop so update_trailing_stop
                        # can decide whether to ratchet without re-submitting
                        # every cycle.
                        stop_loss=self._current_stops.get(sym),
                    )

                # Scan symbols
                entries, exits = self._scan_all_symbols(portfolio_value, current_positions)

                # --- Process exits first ---
                for symbol, signal in exits:
                    self._execute_sell(symbol, signal)

                # --- Process entries ---
                # Remove exited positions from tracking dict
                for symbol, _sig in exits:
                    open_positions.pop(symbol, None)

                if not entries:
                    print("No entry signals")
                else:
                    print(f"{len(entries)} entry signal(s) detected")

                # B4: execute strongest signals first so the max-positions cap
                # doesn't block a high-conviction trade behind a weaker one.
                entries.sort(key=lambda e: e[1].strength, reverse=True)

                for symbol, signal, strategy, position_size in entries:
                    price = signal.metadata.get('price', 0.0)
                    print(f"\n   {symbol}: {signal.direction.value} "
                          f"(strength: {signal.strength:.0%}, strategy: {strategy.name})")
                    print(f"   Price: ${price:.2f} | {_format_metadata(signal)}")

                    # Unified risk check (positions, sector, drawdown, daily loss)
                    # B7: wrap in try/except so an exception in the risk math
                    # cannot crash the bot. Default on error = BLOCK.
                    try:
                        allowed, reason = self.risk_manager.check_trade(
                            signal, portfolio_value, open_positions
                        )
                    except Exception as exc:
                        logger.critical(f"Risk check raised for {symbol}: {exc}")
                        self.telegram.risk_alert(
                            f"Risk check error for {symbol} — trade BLOCKED"
                        )
                        continue
                    if not allowed:
                        print(f"      Risk blocked: {reason}")
                        continue

                    self._execute_buy(symbol, position_size, signal, open_positions)

                # B2: Trailing stop update for open positions.
                # Runs after entries so that brand-new positions can also be
                # considered next cycle; does nothing if ATR or live price
                # are unavailable, or if the trail hasn't ratcheted up.
                for sym, pos in open_positions.items():
                    try:
                        quote = self.alpaca.get_latest_trade(sym)
                        current_price = float(quote.get('p', 0)) if quote else 0.0
                        if current_price <= 0:
                            continue
                        atr = self._last_atr.get(sym)
                        if not atr:
                            continue
                        new_stop = self.risk_manager.update_trailing_stop(
                            pos, current_price, atr
                        )
                        if new_stop is None:
                            continue
                        new_stop = round(new_stop, 2)
                        if pos.stop_loss is not None and new_stop == pos.stop_loss:
                            continue
                        # Cancel old stop, submit new one
                        old_id = self._stop_order_ids.get(sym)
                        if old_id:
                            try:
                                self.alpaca.cancel_order(old_id)
                            except Exception as exc:
                                logger.warning(
                                    f"Could not cancel old stop for {sym}: {exc}"
                                )
                        new_order = self.alpaca.submit_order(
                            symbol=sym, qty=pos.quantity, side='sell',
                            type='stop', time_in_force='gtc', stop_price=new_stop,
                        )
                        if new_order:
                            pos.stop_loss = new_stop
                            self._current_stops[sym] = new_stop
                            self._stop_order_ids[sym] = new_order.get('id', '')
                            logger.info(
                                f"Trailing stop raised for {sym}: {new_stop:.2f}"
                            )
                    except Exception as exc:
                        logger.error(f"Trailing stop update failed for {sym}: {exc}")

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

                # Save state snapshot
                self.state.save_state({
                    "portfolio_value": portfolio_value,
                    "cash": cash,
                    "open_positions": [
                        {"symbol": s, "qty": p.quantity, "entry_price": p.entry_price}
                        for s, p in open_positions.items()
                    ],
                })

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

    def _execute_buy(
        self, symbol: str, qty_dollars: float, signal: Signal,
        open_positions: dict[str, Position] | None = None,
    ) -> bool:
        """Execute buy order with ATR-based bracket (stop-loss + take-profit).

        Returns True if the full bracket was placed successfully.
        """
        # --- Section 1: order placement (any failure here returns False) ---
        # B6: keep this try/except narrow. It MUST NOT wrap the journal/notify
        # section below — otherwise a failing append_trade would make the caller
        # believe the trade never happened and retry it.
        try:
            position = self.alpaca.get_position(symbol)
            if position:
                print(f"      Already in position on {symbol}, skipped")
                return False

            price = signal.metadata.get('price', 0.0)
            if price <= 0:
                logger.error(f"Invalid price for {symbol}: {price}")
                return False

            shares = int(qty_dollars / price)
            if shares < 1:
                print(f"      Position too small for {symbol} (${qty_dollars:.2f})")
                return False

            # ATR-based stops (fall back to 2% fixed if ATR unavailable)
            atr = signal.metadata.get('atr', 0.0)
            if atr > 0:
                stop_loss, take_profit = self.risk_manager.calculate_stops(
                    price, signal.direction, atr
                )
                stop_loss = round(stop_loss, 2)
                take_profit = round(take_profit, 2)
            else:
                # B5: don't silently fall back — log + alert so the operator
                # notices strategies that fail to populate ATR metadata.
                logger.warning(
                    f"No ATR in signal metadata for {symbol} "
                    f"({signal.strategy_name}) — falling back to fixed "
                    f"2%/4% stop. Strategy should populate ATR."
                )
                try:
                    self.telegram.risk_alert(
                        f"{symbol}: no ATR, fallback stop 2%/4% "
                        f"(strategy={signal.strategy_name})"
                    )
                except Exception as exc:
                    logger.error(f"ATR fallback alert failed for {symbol}: {exc}")
                stop_loss = round(price * 0.98, 2)
                take_profit = round(price * 1.04, 2)

            # 1) Market buy
            buy_order = self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='buy',
                type='market', time_in_force='day',
            )
            if not buy_order:
                logger.error(f"Buy order failed for {symbol}")
                return False

            # 2) Protective stop-loss — CRITICAL
            stop_order = self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='sell',
                type='stop', time_in_force='gtc', stop_price=stop_loss,
            )
            if not stop_order:
                # B1: do NOT alert "cancelled" before the cancel is confirmed.
                # Retry cancel up to 3x; on failure, flatten with a market sell;
                # on flatten failure, escalate loudly to the operator.
                logger.critical(
                    f"STOP-LOSS FAILED for {symbol} — attempting cancel of buy"
                )
                cancel_ok = False
                for attempt in range(3):
                    try:
                        self.alpaca.cancel_order(buy_order.get('id'))
                        cancel_ok = True
                        break
                    except Exception as exc:
                        logger.error(
                            f"Cancel attempt {attempt + 1} failed for {symbol}: {exc}"
                        )
                        time.sleep(1)
                if cancel_ok:
                    self.telegram.risk_alert(
                        f"Stop-loss FAILED for {symbol} — buy successfully cancelled"
                    )
                else:
                    # Last resort: flatten with a market sell
                    try:
                        self.alpaca.submit_order(
                            symbol=symbol, qty=shares, side='sell',
                            type='market', time_in_force='day',
                        )
                        self.telegram.risk_alert(
                            f"NAKED POSITION {symbol} — flatten sell submitted. "
                            f"VERIFY MANUALLY."
                        )
                    except Exception as exc:
                        logger.critical(f"FLATTEN FAILED for {symbol}: {exc}")
                        self.telegram.risk_alert(
                            f"CRITICAL — {symbol} {shares} shares, no stop, "
                            f"no cancel, no flatten. MANUAL INTERVENTION IMMEDIATE."
                        )
                return False

            # B2: cache the live stop price and order id so the next cycle's
            # update_trailing_stop has the right baseline and so we can cancel
            # the old stop order when ratcheting.
            self._current_stops[symbol] = stop_loss
            self._stop_order_ids[symbol] = stop_order.get('id', '')

            # 3) Take-profit — nice to have (stop is already in place)
            tp_order = self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='sell',
                type='limit', time_in_force='gtc', limit_price=take_profit,
            )
            if not tp_order:
                logger.warning(f"Take-profit failed for {symbol} — stop-loss is in place")

            # Track in open_positions for subsequent risk checks this cycle
            if open_positions is not None:
                open_positions[symbol] = Position(
                    symbol=symbol, direction=signal.direction,
                    entry_price=price, quantity=shares,
                    stop_loss=stop_loss, take_profit=take_profit,
                )

        except Exception as e:
            logger.error(f"Error placing orders for {symbol}: {e}")
            print(f"      Error buying {symbol}: {e}")
            return False

        # --- Section 2: journal + notify (must NEVER return False) ---
        # B6: these orders are already live on Alpaca. A failure here is bad
        # but must not make the caller retry the trade.
        try:
            self.state.append_trade({
                "symbol": symbol, "side": "buy", "qty": shares,
                "price": price, "strategy": signal.strategy_name,
                "stop_loss": stop_loss, "take_profit": take_profit,
                "reason": f"strength={signal.strength:.2f}",
            })
        except Exception as exc:
            logger.critical(f"Journal append failed for {symbol}: {exc}")
            try:
                self.telegram.risk_alert(
                    f"Trade journal failed for {symbol} — position is open"
                )
            except Exception:
                pass

        try:
            self.telegram.trade_alert(symbol, "buy", shares, price)
        except Exception as exc:
            logger.warning(f"Trade alert failed for {symbol}: {exc}")

        print(f"      BUY {symbol}: {shares} shares @ ${price:.2f}")
        print(f"         Stop: ${stop_loss:.2f} | Target: ${take_profit:.2f}")
        logger.info(
            f"Buy executed: {symbol} x {shares} @ ${price:.2f} "
            f"(strategy={signal.strategy_name}, strength={signal.strength:.2f})"
        )
        return True

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

            # Journal + notify
            self.state.append_trade({
                "symbol": symbol, "side": "sell", "qty": qty,
                "price": current_price, "entry_price": entry_price,
                "pnl_pct": round(pnl_pct, 2),
                "strategy": signal.strategy_name,
            })
            self.telegram.trade_alert(symbol, "sell", qty, current_price, pnl_pct)

            print(f"      SELL {symbol}: {qty} shares @ ${current_price:.2f}")
            print(f"         Entry: ${entry_price:.2f} | P&L: {pnl_pct:+.2f}%")
            logger.info(f"Sell executed: {symbol} x {qty}, P&L: {pnl_pct:.2f}%")

        except Exception as e:
            logger.error(f"Error selling {symbol}: {e}")
            print(f"      Error selling {symbol}: {e}")

    def _recover_state(self) -> None:
        """B3: load last snapshot, reconcile with broker, alert on mismatch.

        Called once at startup from __init__ (after telegram is ready).
        Never raises — state recovery must not prevent the bot from starting.
        """
        try:
            prev_state = self.state.load_state()
            if not prev_state:
                logger.info("No previous state found — starting fresh")
                return

            logger.info(
                f"Previous state loaded (saved_at={prev_state.get('saved_at')})"
            )

            # Broker positions as the source of truth
            broker_positions_raw = self.alpaca.get_positions()
            broker_positions = {
                p.get('symbol', ''): {'qty': p.get('qty', 0)}
                for p in broker_positions_raw
                if p.get('symbol')
            }
            journal_positions = {
                p['symbol']: {'qty': p.get('qty', 0)}
                for p in prev_state.get('open_positions', [])
                if p.get('symbol')
            }

            discrepancies = self.state.reconcile_with_broker(
                journal_positions, broker_positions
            )
            if discrepancies:
                msg = (
                    f"Startup reconciliation found {len(discrepancies)} "
                    f"discrepancies"
                )
                logger.warning(msg)
                self.telegram.risk_alert(
                    msg + "\n" + "\n".join(discrepancies[:5])
                )
        except Exception as exc:
            logger.critical(f"State recovery failed: {exc}")
            try:
                self.telegram.risk_alert(
                    f"State recovery error: {exc} — starting with clean state"
                )
            except Exception:
                pass

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        """Graceful shutdown: cancel orders, save state, notify.

        B8: this method had two bugs. (1) `n_pos` was computed with
        `'positions_list' in dir()` which returns module globals, not
        local scope, so it was always 0. (2) The cancel loop was wrapped
        in a single try, so one failing cancel aborted all subsequent
        cancels. Both are fixed here.
        """
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.is_running = False

        # 1. Snapshot positions BEFORE the first try so positions_list
        # always exists for the notify step at the end.
        positions_list: list = []
        try:
            positions_list = self.alpaca.get_positions()
        except Exception as exc:
            logger.error(f"Could not fetch positions on shutdown: {exc}")

        # 2. Cancel pending orders one-by-one — a failing cancel must
        # NOT abort the rest of the loop.
        try:
            open_orders = self.alpaca.get_orders(status='open')
        except Exception as exc:
            logger.error(f"Could not list open orders on shutdown: {exc}")
            open_orders = []

        failed_cancels = 0
        for order in open_orders:
            order_id = order.get('id')
            try:
                self.alpaca.cancel_order(order_id)
                logger.info(
                    f"Cancelled order {order_id} "
                    f"({order.get('symbol', '?')})"
                )
            except Exception as exc:
                logger.error(f"Failed to cancel order {order_id}: {exc}")
                failed_cancels += 1

        # 3. Save final state (never raise)
        try:
            self.state.save_state({
                "shutdown": True,
                "shutdown_at": datetime.now(timezone.utc).isoformat(),
                "open_positions_count": len(positions_list),
                "failed_cancels": failed_cancels,
                "open_positions": [
                    {"symbol": p.get("symbol"), "qty": p.get("qty"),
                     "entry_price": p.get("avg_entry_price")}
                    for p in positions_list
                ],
            })
        except Exception as exc:
            logger.error(f"Error saving state during shutdown: {exc}")

        # 4. Telegram alert — verbose, mentions failed cancels loudly.
        try:
            self.telegram.send(
                f"Bot stopped (signal {signum}) — "
                f"{len(positions_list)} positions open, "
                f"{failed_cancels} order cancellations failed"
            )
        except Exception as exc:
            logger.error(f"Shutdown Telegram failed: {exc}")

        # 5. Close HTTP session LAST so nothing above raises on a
        # closed client.
        try:
            self.alpaca.close()
        except Exception:
            pass

        sys.exit(0)

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
