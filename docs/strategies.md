# Strategies

Reference for the strategy layer. The repository has two families of
strategies that never interact at runtime:

- **US equity strategies** (`strategies/`) — pure Python, consumed by
  the custom main loop in `main.py` and by the custom
  `backtesting.engine.Backtester`.
- **Crypto strategies** (`freqtrade_config/strategies/`) — subclasses of
  Freqtrade's `IStrategy`, consumed by the Freqtrade process running in
  Docker.

The two families do not share a base class, a type, or a test harness —
only the concept "a strategy takes OHLCV bars and produces trade
decisions".

## US equity strategies

### `BaseStrategy` contract (`strategies/base.py`)

Every US strategy is a subclass of `BaseStrategy`. The class defines an
abstract interface that the main loop and the backtester both consume
identically. A concrete subclass must implement three methods:

```python
class BaseStrategy(ABC):
    def analyze(self, symbol: str, candles: pd.DataFrame) -> Signal: ...
    def should_enter(self, signal: Signal) -> bool: ...
    def should_exit(self, signal: Signal, position: Position) -> bool: ...
```

And may override one:

```python
def calculate_position_size(self, signal: Signal, portfolio_value: float) -> float:
    """Default: portfolio_value * max_allocation_pct * signal.strength"""
```

### `Signal` dataclass

```python
@dataclass
class Signal:
    symbol: str
    direction: Direction           # LONG | SHORT | NEUTRAL
    strength: float                # clamped to [0.0, 1.0] in __post_init__
    strategy_name: str
    timestamp: datetime            # default: datetime.utcnow()
    metadata: dict[str, Any]       # indicator values, reasons, etc.
```

Two properties worth knowing:

- `is_actionable` — True when `direction != NEUTRAL` and `strength > 0`.
  Used as the first gate in every `should_enter` implementation.
- `strength` is automatically clamped to `[0.0, 1.0]` via
  `__post_init__`. You can pass `strength=1.5` from a strategy and it
  will be stored as `1.0`.

The `metadata` dict is a free-form channel for the strategy to pass
data downstream. `main.py` specifically reads:

- `metadata['price']` — current close, for sizing and display
- `metadata['atr']` — for `calculate_stops` (falls back to 2%/4% if absent)
- `metadata['rsi']`, `metadata['zscore']`, `metadata['adx']`,
  `metadata['macd']`, `metadata['relative_volume']` — for the
  `_format_metadata` display helper

A strategy that produces `LONG` signals without populating `atr` will
trigger bug B5's fallback (logged warning + Telegram risk alert). This
is intentional — the metadata contract is "populate ATR or accept the
2%/4% degraded stop".

### `Position` dataclass

```python
@dataclass
class Position:
    symbol: str
    direction: Direction
    entry_price: float
    quantity: float
    entry_time: datetime           # default: datetime.utcnow()
    stop_loss: Optional[float]     # default: None
    take_profit: Optional[float]   # default: None
```

Computed helpers:

- `is_long` — bool
- `notional_value` — `entry_price * quantity` (entry exposure)
- `unrealized_pnl(current_price)` — respects direction
- `unrealized_pnl_pct(current_price)` — respects direction
- `holding_duration(now=None)` — hours since `entry_time`, with naive/
  aware tz auto-alignment

The backtester builds `Position` objects directly; the live bot
constructs them from Alpaca's dicts. Both paths share the `should_exit`
consumer, which is what lets strategies be backtested against the same
logic they trade with.

### `MeanReversionStrategy` (`strategies/mean_reversion.py`)

Three-indicator mean reversion combining RSI, Bollinger Bands, and
price Z-score. All three must agree for an entry.

**Indicators**

- **RSI(14)** using Wilder's smoothing (EWMA α=1/period). Entry on
  oversold (`rsi < 35`) or overbought (`rsi > 65`).
- **Bollinger(20, 1.5)** — 1.5 std instead of the classical 2.0 for more
  entries. Entry when the price is outside the band.
- **Z-score(20)** — `(price - rolling_mean) / rolling_std`. Entry
  threshold at `|zscore| >= 1.5`.

**Entry logic**

```
LONG:  rsi < 35 AND price < bb_lower AND zscore < -1.5
SHORT: rsi > 65 AND price > bb_upper AND zscore > +1.5
```

Strength is a weighted blend:

```
strength = 0.40 * rsi_component
         + 0.30 * bb_component
         + 0.30 * zscore_component
```

Each component is normalised to `[0, 1]`. `should_enter` requires
`strength >= 0.3`.

**Exit logic** (3 conditions, any one triggers exit):

1. **Take-profit**: z-score reverts into `[-0.2, 0.2]` (mean-reversion
   completed)
2. **Stop-loss**: unrealized P&L ≤ -5 %
3. **Timeout**: holding duration ≥ 168 h (7 days)

**Why the parameters diverge from the classics**: walk-forward
optimisation on Phase-2 found that the tighter exit band (`[-0.2, 0.2]`
instead of `[-0.5, 0.5]`) and the 1.5-std Bollinger bands improved
out-of-sample Sharpe on the MR side. These defaults are committed to
`CLASS_DEFAULTS` and should only be changed after a new walk-forward
run.

**Known limitation**: in the Phase-2 backtests, MR scored Sharpe 0.67
with a 44.6 % win rate and a 0.74 profit factor — not great. The
strategy is deliberately kept running because it offers low-correlation
returns alongside Momentum, but it's not a standalone strategy.

### `MomentumStrategy` (`strategies/momentum.py`)

Trend-following strategy combining MACD crossovers, EMA alignment, ADX,
and relative volume. All four must agree for an entry.

**Indicators**

- **MACD(12, 26, 9)** — entry on crossover (bull or bear)
- **EMA(9)** vs **EMA(21)** — trend alignment
- **ADX(14)** with Wilder's smoothing — trend strength
- **Relative volume(20)** — current volume / 20-bar average
- **ATR(14)** — used for sizing and the trailing stop

**Entry logic**

```
LONG:
    macd_cross_up AND ema_fast > ema_slow
    AND adx > 18 AND relative_volume > 1.0

SHORT:
    macd_cross_down AND ema_fast < ema_slow
    AND adx > 18 AND relative_volume > 1.0
```

`macd_cross_up` is a *new* crossover this bar (`prev_macd <= prev_signal
AND cur_macd > cur_signal`). A persistent "macd above signal" without
an actual crossover does **not** trigger — this is explicitly to avoid
late entries chasing an established trend.

Strength is `0.40 * macd_str + 0.35 * adx_str + 0.25 * vol_str`, with
each component normalised to `[0, 1]` relative to its threshold. MACD
amplitude is normalised by ATR so very volatile stocks don't dominate.

**Exit logic** (any triggers):

1. **Opposite MACD cross** — `macd_cross_down` for longs,
   `macd_cross_up` for shorts
2. **ADX collapse** — `adx < 14`, meaning the trend is losing force
3. **ATR-based trailing stop** — 2.5 × ATR(14), activated after +1 %
   unrealized gain. The trailing is computed inline in `should_exit`
   using the price and position — it is **separate** from the
   `RiskManager.update_trailing_stop` path used in the live loop.
4. **Hard stop** at -3 % (wider than MR because trends need room)

**Market hours filter**. `MomentumStrategy` is instantiated in
`main.py` with `{"filter_market_hours": True}`. When true, `analyze`
short-circuits to a NEUTRAL signal outside US equity hours (09:30–16:00
ET). The timezone is `zoneinfo.ZoneInfo("America/New_York")`, which
handles the EDT/EST transition automatically (this was bug 6 in the
Phase 2c audit — the pre-fix code hardcoded UTC-4).

**Why MR and Momentum coexist**. Momentum tends to win in bull markets,
MR tends to win in choppy ranges. Both run on the same cycle and
`_scan_all_symbols` grants the first-triggered strategy a veto ("break"
after the first entry on a symbol). The two strategies are **not**
allowed to both open a position on the same symbol in the same cycle.

### Adding a new US strategy

1. Create `strategies/<name>.py` with a class subclassing
   `BaseStrategy`.
2. Define `CLASS_DEFAULTS` with every tunable parameter.
3. Merge defaults with user config in `__init__`.
4. Implement `analyze`, `should_enter`, `should_exit`.
5. Populate `metadata['price']` and `metadata['atr']` in every `Signal`
   so the live bot can compute stops correctly.
6. Add tests in `tests/test_strategies.py` following the
   `TestMeanReversionStrategy` / `TestMomentumStrategy` patterns:
   insufficient-data guard, neutral on flat data, directional signal
   on contrived input, should_enter / should_exit behaviour.
7. Add an entry to `main.py::TradingBot.__init__` if the strategy is
   meant to run live.
8. Run the two-gate verification — see
   [`backtesting.md`](backtesting.md) §"Two-gate verification".

## Crypto strategies (Freqtrade)

The Freqtrade side uses the standard `IStrategy` interface. Only the
two strategies currently committed are described here.

### `FundingRateArbitrage` (`freqtrade_config/strategies/FundingRateArbitrage.py`)

**Status**: ✅ validated walk-forward (Sharpe 4.8 train, 4.7 OOS, 2 %
drop). This is the primary crypto strategy and the one Phase 3 will
deploy to real money.

**Thesis**. Binance perpetual futures pay a funding rate every 8 hours
(00:00, 08:00, 16:00 UTC). When the rate is positive, longs pay shorts;
when it is sustained and high, shorting the perp earns the carry. To
eliminate directional risk, the live bot pairs the short perp with a
long spot position of equal notional (delta-neutral). The backtest
does not model the spot leg — it trades the futures side naked and
includes a -5 % stop-loss as a directional safety net.

**Implementation details**

- `INTERFACE_VERSION = 3`, `can_short = True` (futures mode required)
- `timeframe = "1h"` — funding rate payments are 8h, so we aggregate
  several 1h bars before entering
- `startup_candle_count = 100` — for Bollinger(20) + buffer
- `minimal_roi = {"0": 100}` — ROI-based exits disabled; exits happen
  through the strategy signal only
- `stoploss = -0.05` — hard directional stop (neutralised by the spot
  leg in live)
- `funding_entry_annual` — tunable via Freqtrade hyperopt
  (`DecimalParameter(0.05, 0.50, default=0.15)`)
- `funding_exit_annual` — `DecimalParameter(0.01, 0.10, default=0.03)`
- `FUNDING_WINDOW = 9` — hardcoded; 9 × 8h = 3 days rolling mean to
  smooth funding rate noise without excessive lag

**Data pipeline**. Freqtrade stores funding rates as synthetic OHLCV
candles where `open == funding_rate`. The strategy fetches them via
`dp.get_pair_dataframe(pair, timeframe, CandleType.FUNDING_RATE)`,
merges them asof the main 1h candle, and computes the rolling mean
annualised by `3 * 365` (periods per year).

**Entry**:

```python
funding_annual > funding_entry_annual  AND  close > bb_lower
```

The BB-lower filter avoids shorting into a crash (which would
overwhelm the funding carry).

**Exit**:

```python
funding_annual < funding_exit_annual
```

Exit the carry when it is no longer profitable after fees.

**Why it works**. The funding rate is paid by longs to shorts during
periods of extreme bullish sentiment. The strategy captures that carry
with ~0 % directional exposure (thanks to the spot hedge in live) for
as long as sentiment stays extreme. Historical drawdowns are driven by
sudden funding rate reversals, not price moves — hence the delta-
neutral construction.

### `BreakoutTrendFollowing` (`freqtrade_config/strategies/BreakoutTrendFollowing.py`)

**Status**: 🟡 dry-run only. Walk-forward showed Sharpe 0.16 train,
−0.17 OOS — it does not pass the two-gate verification, but is kept
running in dry-run as a baseline / sanity check.

**Thesis**. Turtle Trading System 2 on 4h timeframe: enter on Donchian
channel breakouts, exit on reverse breakouts, filter out bear markets
with EMA(200). Classical momentum / trend-following, no parameter
fitting.

**Implementation details**

- `INTERFACE_VERSION = 3`, `can_short = False` (long-only spot)
- `timeframe = '4h'`
- `startup_candle_count = 700` — EMA(200) + Donchian(480) + ATR(180)
  warmup
- `minimal_roi = {"0": 100}` — no ROI exits
- `stoploss = -0.10` — -10 % hard stop
- `trailing_stop = False`
- Parameters marked `optimize=False` — deliberately pinned to the
  classical Turtle defaults, no hyperopt

**Indicators**

- **Donchian entry(480 bars)** = 80 days of 4h bars
- **Donchian exit(240 bars)** = 40 days of 4h bars
- **ADX(14)** — trend filter
- **EMA(200)** — macro bear filter

Both Donchian channels use `.shift(1)` to avoid look-ahead bias (the
bar in progress cannot be used to trigger its own entry).

**Entry**: `close > ema200 AND close > dc_high_480 AND adx > 25`

**Exit**: `close < dc_low_240`

The strategy is intentionally not optimised. Matthieu is letting it
run to accumulate live data and to sanity-check that the generic
Freqtrade + Binance plumbing works. It is expected to lose money
slowly and will probably be retired once FundingRateArbitrage is fully
deployed.

## Testing the strategies

US strategies are tested in `tests/test_strategies.py`:

- `TestMeanReversionStrategy` — insufficient data, flat data, oversold
  entry, should_enter threshold, should_exit (SL, TP, timeout)
- `TestMomentumStrategy` — insufficient data, neutral on flat data,
  trending data, should_enter threshold, should_exit (ADX collapse,
  opposite cross)
- `TestBacktestIntegration` — end-to-end with synthetic and real data

Indicator arithmetic (EMA, MACD, ATR, ADX) is covered in a dedicated
file, `tests/test_momentum_indicators.py`, with 11 cases.

Freqtrade strategies are validated via Freqtrade's own backtesting
subsystem, plus walk-forward using `scripts/validate_hyperopt.py` and
`scripts/backtest_funding_arb.py` for the funding arbitrage. Their
results are documented in [`../CLAUDE.md`](../CLAUDE.md) §"Résultats
walk-forward validés".
