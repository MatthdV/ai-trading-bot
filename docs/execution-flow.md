# Execution Flow

Deep-dive into `main.py`, the single synchronous event loop that drives US
equity trading. This document maps every function involved in one cycle,
documents the error paths, and explains the design decisions that make the
loop safe against partial failures.

## Entry points

- `main.py::main()` — argparse entry, constructs `TradingBot(mode='paper')`
  and calls `run()`. Catches `KeyboardInterrupt` and logs any fatal
  exception before exiting 1.
- `main.py::TradingBot.__init__` — wires the Alpaca client, the risk
  manager with Phase-2c thresholds, the two US strategies, the state
  persistence layer, the `SyncTelegram` wrapper, and the signal handlers.
  **Critically**, `_recover_state()` is called at the end of `__init__`, so
  any startup reconciliation runs *before* the first `run()` iteration and
  before any order can be placed.

## Constructor wiring (`main.py:108-150`)

```
TradingBot.__init__(mode='paper')
├── AlpacaClient()                       # raises ValueError if .env empty
├── RiskManager(RiskConfig(              # hard Phase-2c numbers
│     max_position_pct=0.05,
│     max_simultaneous_positions=5,
│     max_sector_positions=3,
│     max_daily_loss_pct=0.02,
│     max_portfolio_drawdown_pct=0.10,
│     kelly_fraction=0.5,                # dormant — see note below
│   ))
├── [MeanReversionStrategy(), MomentumStrategy({"filter_market_hours": True})]
├── StatePersistence()                   # creates data/ at 0700
├── SyncTelegram()                       # async→sync shim
├── _last_atr, _stop_order_ids,          # cross-cycle caches
│   _current_stops  = {}, {}, {}
├── signal.signal(SIGTERM, _handle_shutdown)
├── signal.signal(SIGINT,  _handle_shutdown)
└── _recover_state()                     # B3 — may send Telegram alerts
```

**Why `kelly_fraction=0.5` is set but not used at runtime**: `RiskManager`
exposes a `calculate_size()` method implementing Half-Kelly, but the bot's
execution path uses `strategy.calculate_position_size()` instead. See
[`risk-management.md`](risk-management.md) §"Sizing" and the "Sizing"
section of [`../CLAUDE.md`](../CLAUDE.md) for why.

## The main loop (`main.py:225-389`)

```
run():
  self.is_running = True
  while self.is_running:
    ┌─ 1. market closed check ─────────────────────┐
    │    is_market_open() == False → sleep 300     │
    └──────────────────────────────────────────────┘
    ┌─ 2. account + positions refresh ─────────────┐
    │    portfolio_value, cash ← get_account()     │
    │    positions_list ← get_positions()          │
    │    Build open_positions: dict[symbol,Position]│
    │      with stop_loss = _current_stops[sym]    │  ← B2 critical
    └──────────────────────────────────────────────┘
    ┌─ 3. scan ─────────────────────────────────────┐
    │    entries, exits = _scan_all_symbols(...)    │
    └──────────────────────────────────────────────┘
    ┌─ 4. process exits (sequential) ───────────────┐
    │    for each exit: _execute_sell()             │
    └──────────────────────────────────────────────┘
    ┌─ 5. process entries ──────────────────────────┐
    │    entries.sort(strength desc)       ← B4     │
    │    for (sym, signal, strat, size):            │
    │      try risk_manager.check_trade(...)        │
    │      except: risk_alert + continue   ← B7     │
    │      if not allowed: continue                 │
    │      _execute_buy(...)                        │
    └──────────────────────────────────────────────┘
    ┌─ 6. trailing stops ───────────────────────────┐
    │    for sym, pos in open_positions:            │
    │      quote ← get_latest_trade(sym)            │
    │      atr ← _last_atr[sym] or skip             │
    │      new_stop ← update_trailing_stop(...)     │
    │      if ratcheted:                            │
    │        cancel old stop, submit new stop       │
    │        pos.stop_loss = new_stop               │
    │        _current_stops[sym] = new_stop         │
    │        _stop_order_ids[sym] = new_order.id    │
    └──────────────────────────────────────────────┘
    ┌─ 7. snapshot + sleep ─────────────────────────┐
    │    state.save_state({...})                    │
    │    sleep(300)                                 │
    └──────────────────────────────────────────────┘
```

### Step 2 — why `stop_loss` is pulled from `_current_stops`

Broker-returned positions don't include the stop-loss price you attached;
that lives on a separate stop order. If we built `Position(stop_loss=None)`
every cycle, the trailing update at step 6 would see `pos.stop_loss is None`
and always compute a new stop — re-submitting a stop order on every loop.

The cache `_current_stops[sym]` is written:
- In `_execute_buy` immediately after the protective stop is placed
- In the trailing-stop block after a successful ratchet

And read at step 2 when rebuilding `open_positions`. This makes
`update_trailing_stop` idempotent: it returns `None` if the computed stop
is not strictly higher than the cached one, so nothing is re-submitted.

### Step 3 — `_scan_all_symbols` (`main.py:176-219`)

```
_scan_all_symbols(portfolio_value, current_positions) → (entries, exits):
    entries, exits = [], []

    with ThreadPoolExecutor(max_workers=5):
        for symbol in SYMBOLS (parallel):
            df ← alpaca.get_ohlcv_dataframe(symbol, '1D', 100)
            for strategy in [MeanReversion, Momentum]:
                signal ← strategy.analyze(symbol, df)
                if signal.metadata['atr'] > 0:
                    self._last_atr[symbol] = atr       ← worker writes

    for each completed future:
        if symbol in current_positions:
            if strategy.should_exit(signal, position):
                exits.append((symbol, signal))
                break    # one exit signal is enough
        else:
            if strategy.should_enter(signal):
                size = strategy.calculate_position_size(signal, portfolio_value)
                entries.append((symbol, signal, strategy, size))
                break    # first strategy to trigger wins

    return entries, exits
```

Two subtleties:

1. **`break` after the first triggered strategy**. Only one strategy is
   allowed to fire per symbol per cycle. This prevents both strategies
   from queueing an entry on the same symbol in the same cycle, which
   would bypass the already-holding check.

2. **`_last_atr` concurrent writes**. Workers write `self._last_atr[symbol]`
   from different threads. Single-key dict writes are atomic under the
   CPython GIL, so no lock is needed. If we ever migrated to a
   multi-process model, this would need a `multiprocessing.Manager().dict()`.

### Step 5 — `_execute_buy` structure (`main.py:395-561`)

This is the most subtle function in the file. It is split into two
try/except blocks **intentionally**:

```
_execute_buy(symbol, qty_dollars, signal, open_positions):

  ┌── Section 1: order placement ───────────────────────────────┐
  │ try:                                                         │
  │   position = alpaca.get_position(symbol)                     │
  │   if position: return False          ← idempotency           │
  │                                                              │
  │   price  = signal.metadata['price']                          │
  │   shares = int(qty_dollars / price)                          │
  │                                                              │
  │   atr = signal.metadata.get('atr', 0)                        │
  │   if atr > 0:                                                │
  │     stop, tp = risk_manager.calculate_stops(...)             │
  │   else:                                                      │
  │     logger.warning + telegram.risk_alert  ← B5               │
  │     stop = price * 0.98                                      │
  │     tp   = price * 1.04                                      │
  │                                                              │
  │   buy_order = alpaca.submit_order(market buy)                │
  │   if not buy_order: return False                             │
  │                                                              │
  │   stop_order = alpaca.submit_order(stop sell)                │
  │   if not stop_order:                                         │
  │     # B1 cascade: cancel → flatten → escalate                │
  │     for attempt in range(3):                                 │
  │       try cancel_order(buy_order.id); break                  │
  │     if cancelled:                                            │
  │       telegram.risk_alert("stop-loss FAILED — cancelled")    │
  │     else:                                                    │
  │       try flatten via market sell                            │
  │       telegram.risk_alert("NAKED POSITION — verify manually")│
  │     return False                                             │
  │                                                              │
  │   _current_stops[symbol]   = stop        ← B2 cache fill     │
  │   _stop_order_ids[symbol]  = stop_order.id                   │
  │                                                              │
  │   tp_order = alpaca.submit_order(limit sell)  # nice-to-have │
  │   if not tp_order: log warning  (stop is in place)           │
  │                                                              │
  │   open_positions[symbol] = Position(...)                     │
  │                                                              │
  │ except Exception as e:                                       │
  │   logger.error(...); return False                            │
  └──────────────────────────────────────────────────────────────┘

  ┌── Section 2: journal + notify (NEVER returns False) ────────┐
  │ try: state.append_trade({...})                               │
  │ except: logger.critical + telegram.risk_alert (both wrapped) │
  │                                                              │
  │ try: telegram.trade_alert(...)                               │
  │ except: logger.warning                                       │
  │                                                              │
  │ return True                                                  │
  └──────────────────────────────────────────────────────────────┘
```

The split is mandated by bug **B6**: the orders in Section 1 are already
live at the broker by the time we reach Section 2. If Section 2 raised and
we returned `False` from the catch, the caller would mark the trade as
"not executed" and potentially retry, doubling the position. Section 2 must
therefore log every failure but **always return `True`**.

The "cancel → flatten → escalate" cascade in Section 1 (bug **B1**) is the
safety net for partial fills:

| Situation | Action | Operator visibility |
|-----------|--------|---------------------|
| Buy placed, stop placed | Success path | Trade alert |
| Buy placed, stop failed, cancel succeeded | Position removed, no naked exposure | Risk alert: "stop-loss FAILED — buy cancelled" |
| Buy placed, stop failed, cancel failed, flatten succeeded | Position flattened with a market sell | Risk alert: "NAKED POSITION — verify manually" |
| Buy placed, stop failed, cancel failed, flatten failed | Position is open with no protection | Risk alert: "CRITICAL — MANUAL INTERVENTION IMMEDIATE" |

The last case is the only one where a human **must** be reachable. The
`SyncTelegram.risk_alert` fallback (see `main.py:81-102`) guarantees the
alert lands in `data/critical_alerts.jsonl` even if Telegram is down.

### Step 6 — Trailing stop loop (`main.py:317-355`)

```
for sym, pos in open_positions.items():
    try:
        quote ← alpaca.get_latest_trade(sym)
        current_price = float(quote.get('p', 0))
        if current_price <= 0: continue

        atr = _last_atr.get(sym)     ← may be missing
        if not atr: continue

        new_stop = risk_manager.update_trailing_stop(pos, current_price, atr)
        if new_stop is None: continue         ← not ratcheted

        new_stop = round(new_stop, 2)
        if pos.stop_loss is not None and new_stop == pos.stop_loss:
            continue                          ← no change after rounding

        # Cancel old, submit new — idempotent at the broker level
        old_id = _stop_order_ids.get(sym)
        if old_id:
            try: alpaca.cancel_order(old_id)
            except: logger.warning (but continue)

        new_order = alpaca.submit_order(stop sell, price=new_stop)
        if new_order:
            pos.stop_loss            = new_stop
            _current_stops[sym]      = new_stop
            _stop_order_ids[sym]     = new_order.get('id', '')
    except: logger.error (but continue with next symbol)
```

The important invariant is **cancel-before-submit**. If we submitted the
new stop first, the position would briefly have two sell stops — if the
price gapped down in that window, both could fill and create a short
position. Cancelling first means the worst case is a brief window with no
stop at all, which is recoverable: the next cycle (5 min later) will
retry. The broker's stop order is "submit to become fill-on-trigger"; a
5-minute gap without one is manageable for swing trades.

If the cancel fails, the new stop is submitted anyway and the warning is
logged — the cancel is best-effort. Alpaca will not error on
"cancel-already-filled", it will return a specific status that the client
surfaces as an exception, which we swallow at `main.py:338-342`.

## Startup: `_recover_state` (`main.py:604-652`)

Called from `__init__`, **after** `self.telegram` is set so that alerts can
fire. Never raises — a failed recovery must not prevent the bot from
starting.

```
_recover_state():
  try:
    prev_state = state.load_state()
    if not prev_state: return

    broker_positions = alpaca.get_positions() → {sym: {qty}}
    journal_positions = prev_state['open_positions'] → {sym: {qty}}

    discrepancies = state.reconcile_with_broker(
        journal_positions, broker_positions
    )

    if discrepancies:
      logger.warning + telegram.risk_alert (first 5 lines)
  except Exception as exc:
    logger.critical + telegram.risk_alert (best-effort)
```

The policy when discrepancies exist is **alert but continue**. We do not
stop the bot because:

- The broker is the source of truth. Once we've reconciled and surfaced
  the diff, the next `_execute_buy` will consult `get_position()` and
  skip any symbol the broker already holds.
- A crashloop here would block the user from ever restarting the bot
  after an operational issue. Starting with a warning is safer.

`reconcile_with_broker` reports three kinds of discrepancy — see
[`state-persistence.md`](state-persistence.md) §"Reconciliation".

## Shutdown: `_handle_shutdown` (`main.py:654-728`)

Triggered by `SIGTERM` or `SIGINT`. Never raises. The function is split
into five numbered sections, each wrapped in its own try/except:

1. **Snapshot positions** before anything else so the Telegram message at
   step 4 always has a meaningful count. (Original bug B8: `n_pos` was
   computed with `'positions_list' in dir()` which inspects module
   globals, not local scope — always returned 0.)
2. **Cancel pending orders one at a time.** A failing cancel must not
   abort the rest of the loop (bug B8, second half). Each cancel is
   wrapped individually; `failed_cancels` counts the ones that didn't
   succeed.
3. **Save final state.** Best-effort — logger.error on failure.
4. **Telegram notification** mentioning open positions and failed
   cancels. Best-effort.
5. **Close the HTTP session** last, so nothing above raises on a closed
   client.

Then `sys.exit(0)`.

The order matters: we cannot close the HTTP session before cancelling
pending orders (step 2 needs it), and we cannot skip step 1 because it
primes the data for step 4. Each step is allowed to fail; the worst case
is an incomplete shutdown, logged and alerted, which a human can clean up
from the Alpaca dashboard.

## Error propagation summary

| Source | Handling | Visibility |
|--------|----------|-----------|
| Scan thread raises | `except Exception` in `as_completed` loop, logged | Log only |
| Strategy `analyze` raises | Caught inside `_fetch_and_analyze`, logged | Log only |
| `risk_manager.check_trade` raises | Default-BLOCK + Telegram risk alert | Telegram + log |
| `get_position` before buy raises | Section-1 try/except returns False | Log only |
| Market buy fails | Section-1 returns False | Log only |
| Stop-loss submit fails | Cancel-or-flatten cascade + Telegram risk alert | Telegram + log |
| Take-profit submit fails | Log warning, continue (stop is enough) | Log only |
| Journal append raises | logger.critical + Telegram risk alert | Telegram + log |
| Telegram raise | Captured in `SyncTelegram`; `risk_alert` persists to `critical_alerts.jsonl` | Disk only |
| Trailing stop update raises | `except` inside loop, logged | Log only |
| State save raises | Logged, loop continues | Log only |
| Shutdown cancel raises | Per-order try, `failed_cancels++` | Final Telegram |
| Alpaca HTTP 429 / 503 / Timeout | Retry with exponential backoff + jitter (max 3) | Warning log |

The overarching rule: **every money-touching call has either a retry path
or a risk alert** (or both). The only silent failures allowed are
cosmetic (take-profit missing, Telegram `send()` failing for a
non-risk message).

## Invariants enforced by the loop

1. **No trade without a stop.** `_execute_buy` either places both orders
   or unwinds the buy via cancel/flatten, and always alerts on failure.
2. **No double-entry on the same symbol.** `_execute_buy` calls
   `get_position(symbol)` before submitting, and `_scan_all_symbols`
   breaks after the first triggered strategy.
3. **No position exceeds the risk limits mid-cycle.** Entries are
   sequential and `risk_manager.check_trade` is re-run between each.
4. **Trailing stops never move against the position.** Enforced by
   `risk_manager.update_trailing_stop` returning `None` when the new
   computed stop is not strictly better than the cached one.
5. **Crash recovery is alert-first, reconcile-second, continue-third.**
   A mismatch in journal vs broker is surfaced before any new order can
   be placed — but is not fatal.

All five are covered by unit tests in `tests/test_strategies.py` and
`tests/test_momentum_indicators.py` — see [`testing.md`](testing.md).
