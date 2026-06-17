# Risk Management

Reference for `risk/manager.py` — the pre-trade safety kernel. Every order
the bot submits must first pass through `RiskManager.check_trade`. This
document describes the gates, the sizing model, the stop-loss maths, the
audit trail, and the rules you must respect if you extend the module.

## Design intent

`risk/manager.py` is the only module in the repository that is **not
allowed to import from `core/` or `main.py`**. It operates on the
dataclasses defined in `strategies/base.py` (`Signal`, `Position`,
`Direction`) and a few stdlib types. That purity is what lets
`tests/test_strategies.py::TestRiskManager` unit-test every gate in
isolation without mocking the broker.

The kernel has four responsibilities:

1. **Gate**: accept or reject a candidate trade based on portfolio state
2. **Size**: compute a dollar allocation (dormant in live path — see §Sizing)
3. **Stop**: compute initial SL/TP and update trailing stops
4. **Audit**: persist every decision to an in-memory log

None of these mutate the broker, the filesystem, or external services.
Effects live in `main.py`.

## `RiskConfig` — tunable parameters

Defined as a `@dataclass` at `risk/manager.py:28`. All defaults match the
Phase-2c non-negotiable rules in [`../CLAUDE.md`](../CLAUDE.md).

```python
@dataclass
class RiskConfig:
    # Position limits
    max_position_pct: float           = 0.05    # max 5% of portfolio per trade
    max_simultaneous_positions: int   = 5
    max_sector_positions: int         = 3

    # Daily / drawdown limits
    max_daily_loss_pct: float         = 0.02    # -2% halts the day
    max_portfolio_drawdown_pct: float = 0.10    # -10% halts permanently

    # Kelly Criterion
    kelly_fraction: float             = 0.5     # half-Kelly (dormant)

    # Stops
    atr_stop_multiplier: float        = 2.0     # initial SL = 2*ATR
    min_risk_reward: float            = 2.0     # TP/SL >= 2:1
    trailing_activation_pct: float    = 0.01    # activate after +1%
    trailing_atr_multiplier: float    = 1.0     # trail distance = 1*ATR
```

`SECTOR_MAP` (same file, line 51) is a dict mapping every symbol in the
watchlist to a sector bucket. Adding a symbol to `main.py::SYMBOLS`
without adding it to `SECTOR_MAP` classifies it as `"unknown"`, which
shares a sector with every other unknown symbol. That is usually not what
you want — add the sector at the same time.

## Internal state

`RiskManager` holds five pieces of mutable state:

| Field | Purpose | Reset |
|-------|---------|-------|
| `_peak_equity` | Running max of portfolio value (for drawdown) | Never — persists across days |
| `_day_start_equity` | Equity at today's first `check_trade` | New trading day |
| `_current_day` | `date.today()` at last reset | New trading day |
| `_daily_pnl` | Unused currently (reserved for reports) | New trading day |
| `_trading_halted` + `_halt_reason` | Sticky halt flag | New trading day (daily halts) — never for drawdown halts |
| `_positions` | Fallback positions dict if caller passes None | Never — `check_trade` prefers the `open_positions` arg |
| `_audit_log` | List of dict entries (`_log_check` appends) | Never |

Critical subtlety: `_maybe_reset_day` resets `_trading_halted` on a new
day. That means a **daily loss halt** is sticky for the day but cleared
at midnight. A **drawdown halt** is also cleared — but in practice the
drawdown will immediately re-trigger on the next `check_trade` unless the
portfolio has recovered significantly. If you need a truly permanent
halt, add a flag that survives `_maybe_reset_day`.

## `check_trade(signal, portfolio_value, open_positions=None)`

The single pre-trade gate. Returns `(allowed: bool, reason: str)`.

Order of checks (short-circuits on first reject):

```
check_trade:
  ├── _maybe_reset_day(portfolio_value)    # updates peak, may reset halt
  │
  ├── if _trading_halted:                   # sticky flag
  │     return (False, "Trading halted: ...")
  │
  ├── drawdown = (peak - value) / peak
  │   if drawdown >= max_portfolio_drawdown_pct:
  │     halt + audit + return (False, "Max drawdown breached: ...")
  │
  ├── daily_ret = (value - day_start) / day_start
  │   if daily_ret <= -max_daily_loss_pct:
  │     halt + audit + return (False, "Daily loss limit: ...")
  │
  ├── if len(positions) >= max_simultaneous_positions:
  │     return (False, "Max positions reached: ...")
  │
  ├── sector = SECTOR_MAP[signal.symbol]
  │   sector_count = positions where SECTOR_MAP[p.symbol] == sector
  │   if sector_count >= max_sector_positions:
  │     return (False, "Sector limit (tech): ...")
  │
  ├── if signal.symbol in {p.symbol for p in positions}:
  │     return (False, "Already holding SYM")
  │
  └── return (True, "OK")
```

Each rejection path also calls `_log_check(signal, allowed=False, reason)`
so the audit log captures a structured entry even for blocked trades.

### Ordering rationale

The order is intentional:

1. Halt check first — cheapest, prevents useless math
2. Drawdown before daily loss — drawdown is the harsher threshold and
   catches runaway losses that span multiple days
3. Position cap before sector cap — a full portfolio is a harder constraint
4. Sector cap before already-holding — a symbol you already hold
   contributes to the sector count, so rejecting early on the cap saves
   the set-construction for the dedup check
5. Already-holding last — it's the most permissive rejection and the
   cheapest message

If you add a new gate, think carefully about where it fits: gates that
mutate state (like the drawdown/daily halts) must come **before** gates
that only read state, otherwise a read-only gate can mask a halt.

## `calculate_size(portfolio_value, win_rate, avg_win, avg_loss)`

Half-Kelly sizing with a hard `max_position_pct` cap.

```
f*      = (b*p - q) / b          where b = avg_win / avg_loss
                                       p = win_rate
                                       q = 1 - p
f_adj   = f* * kelly_fraction
f_capped = max(0, min(f_adj, max_position_pct))
size    = portfolio_value * f_capped
```

Returns `0.0` if `avg_loss <= 0` or `win_rate <= 0` (insufficient stats).

**This method is currently dormant in the live path.** The bot calls
`strategy.calculate_position_size()` instead, which uses a fixed fraction
of portfolio × signal strength. The rationale is in
[`../CLAUDE.md`](../CLAUDE.md) §"Sizing" and reproduced here:

- Kelly requires stable win-rate, avg-win, avg-loss statistics *per
  strategy in live conditions*.
- The only stats we have are backtest-derived, which are biased by
  look-ahead, survivorship, and walk-forward optimisation overfitting.
- Applying Kelly to biased stats amplifies the overfitting — sizing
  becomes a mirror of backtest noise rather than a reflection of true
  edge.
- The `max_position_pct = 0.05` cap guarantees a worst-case exposure
  independent of the sizing model, so the cap alone is enough for the
  safety invariant.

Kelly sizing will be re-enabled once we have ≥100 live trades per
strategy over ≥3 months and can compute rolling live stats. Until then,
`calculate_size` remains tested (`test_kelly_sizing`) and callable but
unused by `main.py`.

## `calculate_stops(entry_price, direction, atr)`

ATR-based initial stop-loss and take-profit.

```
sl_dist = atr_stop_multiplier * atr          # default: 2 * ATR
tp_dist = sl_dist * min_risk_reward          # default: 4 * ATR (2:1 R/R)

if direction == LONG:
    stop_loss   = entry_price - sl_dist
    take_profit = entry_price + tp_dist
else:   # SHORT
    stop_loss   = entry_price + sl_dist
    take_profit = entry_price - tp_dist
```

Returns `(stop_loss, take_profit)`. No input validation — callers are
responsible for non-negative ATR. A zero ATR produces entry==stop==tp,
which the live bot avoids by branching on `atr > 0` in
`main.py::_execute_buy` (B5) and falling back to a fixed 2%/4% stop with a
logged warning and a Telegram alert.

The 2:1 risk-reward floor is hard-coded via `min_risk_reward=2.0`. A
strategy that needs a wider TP can pass its own stop/TP in the signal
metadata and bypass `calculate_stops`, but nothing in the current code
does this — it would require a separate code path in `_execute_buy`.

## `update_trailing_stop(position, current_price, atr)`

Called once per cycle per open position from the trailing-stop loop in
`main.py`. Returns:

- `None` if the trail is not activated (unrealised PnL < 1 %) or if the
  new stop would not ratchet in the favourable direction
- A new stop price otherwise

```
pnl_pct = position.unrealized_pnl_pct(current_price)
if pnl_pct < trailing_activation_pct:
    return None

trail_dist = trailing_atr_multiplier * atr          # default: 1 * ATR

if position.is_long:
    new_sl = current_price - trail_dist
    if position.stop_loss and new_sl <= position.stop_loss:
        return None           # don't move stop against the position
    return new_sl
else:   # SHORT
    new_sl = current_price + trail_dist
    if position.stop_loss and new_sl >= position.stop_loss:
        return None
    return new_sl
```

The ratchet logic is what keeps the caller's trailing loop idempotent: if
the price is drifting sideways, `update_trailing_stop` returns `None`
every cycle and the broker stop order is not re-submitted.

**Requires that `position.stop_loss` reflects the current broker stop.**
This is why `main.py` maintains the `_current_stops` cache — see
[`execution-flow.md`](execution-flow.md) §"Step 2".

## Audit log

`_log_check` appends a dict to `self._audit_log` on every call and logs
at INFO (allowed) or WARNING (blocked):

```python
{
    "timestamp": datetime.utcnow().isoformat(),
    "symbol": signal.symbol,
    "direction": signal.direction.value,
    "strength": signal.strength,
    "strategy": signal.strategy_name,
    "allowed": bool,
    "reason": str,
}
```

Accessible via `get_audit_log()`, used by `daily_report()` to count
"checks_today". The log is in-memory only — it is **not** persisted to
disk. Rationale: the risk kernel is pure, and persistence is the
responsibility of `state_persistence.py`. If you need the audit log to
survive a restart, add a snapshot of `get_audit_log()` to the state file
in `main.py::save_state` — do not reach into `risk/manager.py` to write
files.

## `daily_report(portfolio_value, open_positions=None)`

Returns a dict summary suitable for Telegram or dashboards:

```python
{
    "date":             iso timestamp,
    "portfolio_value":  float,
    "daily_pnl_pct":    float,
    "drawdown_pct":     float,
    "exposure_pct":     float,
    "open_positions":   int,
    "trading_halted":   bool,
    "halt_reason":      str,
    "positions":        list of {symbol, direction, entry_price, quantity, stop_loss, take_profit},
    "checks_today":     int,
}
```

Note: `exposure_pct` is the sum of `pos.notional_value` (entry price ×
quantity) divided by `portfolio_value`. This is **entry exposure**, not
mark-to-market exposure. If a position is up 50 % the function will
under-report the real exposure. The live daily-summary feature that
Matthieu has on the Phase-3 backlog will need to reconcile this with
`get_positions()` from Alpaca.

## What not to do

This module is the safety kernel. Contributors have the rule
"do not modify `risk/manager.py` without explicit approval" in
[`../CLAUDE.md`](../CLAUDE.md) rule 8. In practice, the things you should
not change without writing a matching test first:

1. **Don't loosen the check order.** Moving the already-holding check
   before the daily-loss halt would let a re-entry bypass the halt.
2. **Don't swallow exceptions inside `check_trade`.** The caller in
   `main.py` already wraps it in a try/except with a default-BLOCK
   policy; adding a try here would mask bugs.
3. **Don't add I/O.** No logging to disk, no HTTP, no file reads. The
   kernel must stay pure for testability.
4. **Don't add optional kwargs with defaults that change behaviour.** If
   you need a new knob, add it to `RiskConfig` so every instance has it
   explicitly.
5. **Don't mutate the passed `open_positions` dict.** It is owned by
   `main.py` and borrowed for the duration of `check_trade`.

Extensions that are welcome:

- New gates (e.g. "no entry during the first 15 minutes after market
  open") — add them in the correct position in the check order
- New stop models (e.g. Chandelier Exit) — add new methods alongside
  `calculate_stops`, do not replace
- A "pessimistic" flag on `calculate_size` to divide by a user-provided
  confidence factor

## Test coverage

`tests/test_strategies.py::TestRiskManager` (12 cases) and
`tests/test_bot.py::test_risk_manager` cover:

| Scenario | Test |
|----------|------|
| Valid trade allowed | `test_allows_valid_trade` |
| Max positions cap | `test_blocks_when_max_positions` |
| Sector concentration cap | `test_blocks_sector_concentration` |
| Duplicate symbol rejected | `test_blocks_duplicate_symbol` |
| Daily loss halt | `test_blocks_on_daily_loss_halt` |
| Drawdown halt | `test_blocks_on_drawdown_halt` |
| Kelly sizing returns positive and capped | `test_kelly_sizing` |
| LONG stops below entry, TP above | `test_atr_stops` |
| SHORT stops above entry, TP below | `test_short_stops_inverted` |
| Trailing stop ratchets up only | `test_trailing_stop_ratchets_up_only` |
| Trailing stop respects activation threshold | `test_trailing_stop_not_active_below_threshold` |
| Daily report structure | `test_daily_report` |

The five failure-path tests (daily halt, drawdown halt, SHORT stops,
trailing ratchet, activation threshold) were added in Phase 2c-bis
specifically because agent reviews flagged them as the most-likely-to-be-
broken in future refactors.
