# Architecture

High-level view of how the US equity bot is structured, how data flows through
it, and where the crypto side sits relative to it.

## Module layout

```
ai-trading-bot/
├── main.py                       # Orchestrator — process entry point
├── core/                         # I/O adapters
│   ├── alpaca_client.py          #   REST client (retry, backoff, cache)
│   ├── state_persistence.py      #   Journal + state snapshot
│   └── position_sizer.py         #   Kelly helper (currently dormant)
├── risk/                         # Pre-trade gates and sizing
│   └── manager.py                #   RiskManager, RiskConfig, SECTOR_MAP
├── strategies/                   # US strategies (python native)
│   ├── base.py                   #   Signal, Position, Direction, BaseStrategy
│   ├── mean_reversion.py         #   RSI + BB + Z-score
│   └── momentum.py               #   MACD + EMA + ADX + Volume + ATR
├── backtesting/                  # Event-driven backtester
│   └── engine.py                 #   Backtester, TradeRecord
├── notifications/                # Telegram
│   └── telegram_bot.py           #   Async notifier used by SyncTelegram
├── freqtrade_config/             # Crypto side
│   ├── config_breakout.json      #   Spot config
│   ├── config_futures.json       #   Futures config
│   └── strategies/
│       ├── BreakoutTrendFollowing.py
│       └── FundingRateArbitrage.py
├── tests/                        # pytest suite
├── scripts/                      # Operational scripts (deploy, validate)
└── data/                         # Runtime artifacts (gitignored)
    ├── trades_journal.jsonl      #   Append-only trade log (0600)
    ├── bot_state.json            #   Latest snapshot (0600)
    └── critical_alerts.jsonl     #   Telegram-failure fallback (0600)
```

## Layered dependency graph

Arrows point from caller to callee. No layer should call upward; in
particular, `risk/` and `strategies/base.py` must not import from `main.py`.

```
       main.py
       │
       ├─▶ notifications.telegram_bot (via SyncTelegram)
       ├─▶ core.alpaca_client
       ├─▶ core.state_persistence
       ├─▶ risk.manager
       └─▶ strategies.{mean_reversion, momentum}
                 │
                 └─▶ strategies.base (Signal, Position, Direction, BaseStrategy)
                            ▲
                            │
                 risk.manager refers to Signal/Position/Direction
                            ▲
                            │
                 backtesting.engine (also depends on strategies.base)
```

Key property: **`risk/manager.py` has no transitive dependency on `main.py`
or `core/alpaca_client.py`**. It is a pure Python module operating on
in-memory dataclasses, which is what makes it unit-testable in isolation and
why [`risk-management.md`](risk-management.md) treats it as the safety kernel.

## Data flow — one main-loop cycle

Every 5 minutes while the US market is open, `main.py::TradingBot.run()`
executes this sequence:

```
         ┌──────────────────────────────────────────┐
         │ 1. Alpaca.is_market_open()?              │
         └───────────────┬──────────────────────────┘
                         │ yes
                         ▼
         ┌──────────────────────────────────────────┐
         │ 2. Alpaca.get_account() → portfolio $    │
         │    Alpaca.get_positions() → open_positions│
         └───────────────┬──────────────────────────┘
                         ▼
         ┌──────────────────────────────────────────┐
         │ 3. _scan_all_symbols() (ThreadPool, n=5) │
         │    Per symbol, per strategy:             │
         │      analyze() → Signal                  │
         │      should_enter / should_exit          │
         │    Returns (entries, exits)              │
         └───────────────┬──────────────────────────┘
                         ▼
         ┌──────────────────────────────────────────┐
         │ 4. Process exits sequentially            │
         │    _execute_sell() for each              │
         └───────────────┬──────────────────────────┘
                         ▼
         ┌──────────────────────────────────────────┐
         │ 5. Sort entries by signal.strength desc  │
         │    For each entry (sequential):          │
         │      risk_manager.check_trade(...)       │
         │      if allowed: _execute_buy(...)       │
         └───────────────┬──────────────────────────┘
                         ▼
         ┌──────────────────────────────────────────┐
         │ 6. Trailing stop update (all positions)  │
         │    risk_manager.update_trailing_stop()   │
         │    If ratcheted: cancel old, submit new  │
         └───────────────┬──────────────────────────┘
                         ▼
         ┌──────────────────────────────────────────┐
         │ 7. state.save_state(snapshot)            │
         │    sleep(300)                            │
         └──────────────────────────────────────────┘
```

See [`execution-flow.md`](execution-flow.md) for line-level details and all
failure paths.

## Concurrency model

The US bot is **single-process, single-main-thread**, with one bounded
parallelism point:

- **Signal scanning** is parallelised via
  `ThreadPoolExecutor(max_workers=5)` in `_scan_all_symbols`. Each worker
  fetches OHLCV and runs the strategies for one symbol. Strategy analysis is
  pure (pandas operations on a fresh DataFrame) so workers do not share
  mutable state.
- **Dict writes to `self._last_atr`** from worker threads are single-key
  writes, atomic under CPython's GIL. This is intentional: the alternative
  (a lock) would serialise the scan and double the cycle time for no safety
  gain.
- **Execution (buys/sells) runs sequentially on the main thread** after
  the scan completes. The max-positions cap and sector cap are checked
  before each buy — no race between parallel entries.
- **Signal handlers** (`SIGTERM`, `SIGINT`) run on the main thread too,
  and set `self.is_running = False` before delegating to `_handle_shutdown`.

There is **no asyncio loop** in the US bot. The Telegram notifier is async
internally but `SyncTelegram` wraps each call in `asyncio.run()`, so callers
see a sync interface. This was a deliberate choice to avoid rewriting the
main loop as coroutines — see the `SyncTelegram` docstring in `main.py:50`.

## State ownership

| State | Lives in | Written by | Read by |
|-------|----------|------------|---------|
| Open positions (ground truth) | Alpaca broker | `submit_order()` | `get_positions()` each cycle |
| Current stop prices | `self._current_stops` (RAM) + broker stop orders | `_execute_buy`, trailing loop | Next cycle's `Position.stop_loss` |
| Stop order ids | `self._stop_order_ids` (RAM) | same as above | Trailing loop (to cancel old) |
| Last known ATR | `self._last_atr` (RAM) | `_fetch_and_analyze` | Trailing loop |
| Risk audit log | `risk.manager._audit_log` (RAM) | Every `check_trade` call | `daily_report()` |
| Trade history | `data/trades_journal.jsonl` | `_execute_buy`, `_execute_sell` | Startup reconciliation (indirectly) |
| Last snapshot | `data/bot_state.json` | End of each cycle + shutdown | `_recover_state()` at startup |
| Unsendable Telegram alerts | `data/critical_alerts.jsonl` | `SyncTelegram.risk_alert` fallback | Manual forensics |

The broker is **always** the source of truth for open positions. The
in-memory caches (`_current_stops`, `_stop_order_ids`, `_last_atr`) are
reconstruction helpers, not authority. If the bot crashes, it rebuilds
positions from `get_positions()` on restart; the cached stops are
intentionally lost and the trailing stops simply don't ratchet until the
next ATR is computed.

This asymmetry is what lets `_recover_state()` be simple: it compares the
last journal snapshot against `get_positions()` and emits discrepancies —
it does not try to reconstruct the trailing-stop caches.

## Configuration surface

| Knob | Location | Purpose |
|------|----------|---------|
| Alpaca keys, URL | `.env` (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL`) | HTTP auth |
| Telegram | `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) | Notifications |
| Binance | `.env` (`BINANCE_API_KEY`, `BINANCE_API_SECRET`) | Freqtrade live |
| Watchlist | `main.py::SYMBOLS` | US bot symbols |
| Risk limits | `main.py::TradingBot.__init__` (`RiskConfig(...)`) | Position/sector/daily/DD caps |
| Strategy defaults | `strategies/<name>.CLASS_DEFAULTS` | Indicator periods, thresholds |
| Freqtrade crypto configs | `freqtrade_config/config_*.json` | Pairlists, timeframes, stake |
| Freqtrade strategy params | In-class `IntParameter`/`DecimalParameter` | Hyperopt-tunable |

No runtime configuration file exists for the US bot. Risk limits and symbol
watchlist are Python literals; changing them means editing `main.py` and
restarting. This is deliberate — it keeps the attack surface for "someone
changed the max-position-pct at runtime" at zero.

## What the Freqtrade side looks like from here

The Python code in `main.py` never imports from `freqtrade_config/`. The
only shared artifacts are:

- `.env` (same file, different variables)
- `scripts/deploy_vps.sh` (knows how to push Freqtrade to the VPS)
- `docker-compose.freqtrade.yml` (container spec)
- Telegram bot token (both surfaces can send via the same bot)

The crypto strategies are standard Freqtrade `IStrategy` subclasses — see
[`strategies.md`](strategies.md) for the details of
`FundingRateArbitrage` (the validated, delta-neutral one) and
`BreakoutTrendFollowing` (the Turtle System 2 dry-run).

## Where the "bot" boundary really is

Given the layering above, the bot is not `main.py` — the bot is the
composition of:

1. A **broker session** (`AlpacaClient`) with retry/backoff and graceful close
2. A **risk kernel** (`RiskManager`) that gates every trade
3. A **persistence layer** (`StatePersistence`) that survives restarts
4. A **strategy registry** (the list constructed in `TradingBot.__init__`)
5. An **execution loop** (`TradingBot.run`) that wires them together

You can swap any of 1–4 without touching the others, provided you respect
the interfaces in `strategies/base.py` and the function signatures on
`RiskManager`. That is what makes the backtester reuse the same strategy
classes as the live bot — it just substitutes a synthetic price feed for
`AlpacaClient`.
