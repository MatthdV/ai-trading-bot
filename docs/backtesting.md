# Backtesting and Validation

Reference for the custom backtesting engine (`backtesting/engine.py`),
the two-gate verification process, and the walk-forward scripts.

## Two engines, two surfaces

| Surface | Engine | Driven by | Output |
|---------|--------|-----------|--------|
| US equities | `backtesting.engine.Backtester` | `run_backtest.py` and `scripts/validate_change.py` | Terminal report + `_equity_curve` series |
| Crypto | Freqtrade's built-in `freqtrade backtesting` CLI | `scripts/validate_hyperopt.py` and `scripts/backtest_funding_arb.py` | Freqtrade CSV/JSON reports |

The two are independent. The custom engine here is specifically for
testing `strategies/mean_reversion.py` and `strategies/momentum.py`
against real yfinance data without involving Alpaca or a paper account.

## `backtesting.engine.Backtester`

Event-driven bar-by-bar simulator. The design prioritises "run against
the same strategy classes as live" over performance — this is a testing
tool, not a high-throughput optimiser.

### Constructor

```python
Backtester(
    strategies: Sequence[BaseStrategy],
    initial_capital: float = 10_000.0,
    commission_pct: float  = 0.001,      # 0.1 % per side
    slippage_pct: float    = 0.0005,     # 0.05 % per side
)
```

Commission and slippage are applied at both entry and exit. Commissions
default to 0.1 % per side (→ 0.2 % round-trip), matching a generous
retail broker model. Slippage is modelled as a worsening of the fill
price in the direction of the trade.

### Internal state (reset on each `run()`)

```python
_cash: float                           # Available USD
_positions: dict[str, Position]        # key = f"{symbol}::{strategy_name}"
_trades: list[TradeRecord]             # Completed round-trips
_equity_curve: list[dict]              # [{date, equity}, ...]
```

The position key is `f"{symbol}::{strategy_name}"` rather than just
`symbol`. This is important because it **allows two strategies to hold
positions on the same symbol in parallel** during a backtest. The live
bot does not permit this (see §"Known divergence" below), but the
backtester does so each strategy can be evaluated independently.

### `run(candles, start_date=None, end_date=None)`

Main entry point. Iterates over unique dates in the DataFrame, builds a
rolling lookback window per symbol, and calls each strategy's
`analyze` / `should_exit` / `should_enter` in order.

**Lookback window**. Computed as
`max(strategy.config.get('min_candles', 60) for strategy in strategies)`.
This means the backtester always gives each strategy at least as many
prior bars as its configured minimum — a strategy that needs 100 bars
of warmup gets 100 bars on every `analyze` call after the warmup phase.
Before `i >= lookback`, the main loop `continue`s without calling the
strategies.

**Multi-asset support**. If the input DataFrame has a `symbol` column,
the engine iterates over every unique symbol each bar. Otherwise it
treats the frame as a single-asset series.

**Simulated fills**. Both stop-loss and take-profit triggers fill at
the **current bar's close price** after applying slippage. This is a
simplification: in reality a stop trigger would fill at the next bar's
open, and a fast move could fill well below the stop price.

Key implication: the backtester's reported P&L is **optimistic** on
stop-outs, especially on gappy instruments. For risk sizing decisions
you should assume actual live P&L on stop events will be ~1 % worse
than the backtest suggests.

**End-of-backtest cleanup**. Any position still open at the last bar
is force-closed at the last available price with `exit_reason =
"end_of_backtest"`. These trades contribute to the stats — they are
not filtered out — because the alternative (mark-to-market only)
would under-count lossy positions still open at the end.

### `get_results()` metrics

```python
{
    "initial_capital":         float,
    "final_equity":            float,
    "total_return_pct":        float,
    "sharpe_ratio":            float,   # annualised, risk-free = 0
    "max_drawdown_pct":        float,
    "win_rate_pct":            float,
    "profit_factor":           float,   # total_wins / total_losses
    "num_trades":              int,
    "avg_trade_duration_hours": float,
    "total_commissions":       float,
    "equity_curve":            pd.Series,
}
```

Two things worth knowing:

1. **Sharpe ratio** uses daily returns, `std > 0` check, and
   `sqrt(252)` annualisation. It is **not** risk-adjusted for any
   risk-free rate — the numerator is raw mean daily return.
2. **Profit factor** returns `inf` when `total_losses == 0.0`. Any
   caller comparing Sharpe/PF across strategies must special-case this.

### `print_report()`

A formatted stdout summary. Used by `run_backtest.py` for interactive
runs. The report is human-reading only; automated comparisons should
read `get_results()` directly.

## `run_backtest.py`

Top-level driver that downloads historical OHLCV from yfinance, builds
a combined DataFrame, and runs the backtester for:

1. `MeanReversionStrategy` alone
2. `MomentumStrategy` alone
3. Both strategies combined

Three separate reports are printed. The combined run uses the same
`::` position-keying scheme, so both strategies can trade the same
symbol simultaneously in the combined report — this explicitly is not
how the live bot behaves.

Example:

```sh
python3 run_backtest.py \
    --symbols AAPL MSFT GOOGL TSLA SPY \
    --period 2y \
    --capital 10000
```

Output (truncated):

```
========================================================
  BACKTEST REPORT
========================================================
  Strategies      : MeanReversionStrategy, MomentumStrategy
  Initial Capital  : $    10,000.00
  Final Equity     : $    10,209.93
  Total Return     :         2.10 %
  Sharpe Ratio     :        0.693
  Max Drawdown     :         1.53 %
  Win Rate         :         47.8 %
  Profit Factor    :         1.26
  Num Trades       :           92
```

Those numbers are the current US-strategy baseline. They are not good
— the bot is primarily a learning / infrastructure exercise for the
equity side, with the real expected returns coming from the crypto
side (FundingRateArbitrage).

## Two-gate verification

From [`../CLAUDE.md`](../CLAUDE.md) rule 7: *"Tout changement de
stratégie doit passer la two-gate verification + walk-forward"*. The
two gates exist to catch both code bugs and overfitting before any
change reaches live trading.

### Gate 1: code review

Looking for, in order:

1. **Look-ahead bias**. Does an indicator consume the current bar as
   if it were already closed? Every `.shift(1)` on Donchian channels
   in `BreakoutTrendFollowing` is there for exactly this reason.
2. **Survivorship bias**. Does the watchlist only include stocks that
   still exist today? (For US: yes, but the watchlist is small enough
   that we accept the bias. For crypto: Freqtrade handles delistings
   natively.)
3. **Division by zero and NaN**. Every indicator helper in
   `strategies/*.py` uses `.replace(0, np.nan)` and the `analyze`
   methods guard against NaN on the last row.
4. **Edge cases**. Empty DataFrames, single-bar inputs, all-equal
   prices, all-gap data.

For non-trivial changes, the workflow is the parallel-agents review
— see [`testing.md`](testing.md) §"Money-risk PR review workflow".

### Gate 2: walk-forward backtest

For US strategies:

```sh
python3 scripts/validate_change.py --compare
```

`validate_change.py` is supposed to save a baseline `.json` before the
change and a candidate `.json` after, then compare them. The thresholds
encoded in the script:

- **Sharpe ratio** ≥ 1.0
- **Win rate** ≥ 50 %
- **Max drawdown** ≤ 15 %
- **Profit factor** ≥ 1.2

**Current state (2026-04-09)**: no baseline is stored in the repo.
Running `--compare` returns *"No baseline found. Run with
--save-baseline first."* — the comparison passes through without
regressing anything because there is nothing to regress against. This
is a known gap; running `--save-baseline` on the current `main` should
be a prerequisite for future PRs.

For crypto strategies:

```sh
freqtrade backtesting --strategy FundingRateArbitrage \
    --config freqtrade_config/config_futures.json \
    --timerange 20200101-
python3 scripts/validate_hyperopt.py \
    --strategy FundingRateArbitrage --train-sharpe 4.8 --oos-sharpe 4.7
```

`validate_hyperopt.py` checks that the OOS Sharpe drop versus the
training Sharpe is **below 30 %**. Anything beyond that is considered
overfit. This threshold is what killed `MomentumFreqtrade` (Sharpe
0.68 train, −0.52 OOS, 177 % drop) and `MeanReversionFreqtrade`
(Sharpe −1.33 train, −2.65 OOS).

## Walk-forward protocol

The actual walk-forward method used for the Phase 2b / 2b bis
strategy validation:

1. **Split the historical window** into a training and an
   out-of-sample (OOS) segment. For crypto that was typically
   `train = 2017-01 .. 2023-12`, `OOS = 2024-01 .. 2025-12`.
2. **Hyperopt on training** (Freqtrade's hyperopt with Bayesian
   search, 500 epochs).
3. **Lock the best params**.
4. **Re-run backtest on OOS** with the locked params, no further
   tuning.
5. **Compare Sharpe**. If OOS Sharpe dropped more than 30 %, reject
   the strategy.

The FundingRateArbitrage strategy was the only one to pass: 4.8 train
→ 4.7 OOS, 2 % drop. All other strategies failed this gate.

## Commission and slippage calibration

The backtester's defaults (`0.1 %` commission, `0.05 %` slippage) are
**optimistic** for a retail account. Reasonable stress-test numbers:

- **Alpaca paper** (US equities): 0 commissions, ~1-2 bps slippage on
  liquid names, 5-10 bps on small caps.
- **Alpaca live** (US equities): same.
- **Binance spot** (crypto): 0.075-0.1 % commission, 2-5 bps slippage.
- **Binance futures** (crypto): 0.02-0.04 % commission (maker), 0.04-
  0.06 % (taker), 1-3 bps slippage.

For paranoid runs, double the defaults. A strategy that still shows a
positive Sharpe with 0.2 % commission + 0.1 % slippage is much closer
to live-realistic than the defaults suggest.

## Known divergences between backtest and live

1. **Position keying**. Backtest allows two strategies on the same
   symbol simultaneously; live loop does not (`break` after first
   triggered strategy in `_scan_all_symbols`). Backtest P&L therefore
   slightly over-counts strategies that fire together.
2. **Risk manager not used in backtest**. The custom `Backtester` does
   not call `RiskManager.check_trade`. Position caps, sector limits,
   daily-loss halts, and drawdown halts are not simulated. In the live
   loop these would block many of the trades the backtest executes.
3. **Trailing stops simulated inside the strategy**, not inside the
   risk manager. `MomentumStrategy.should_exit` has its own trailing
   logic that is functionally similar but separately coded from
   `risk.manager.update_trailing_stop`. A refactor that deletes the
   per-strategy trailing in favour of the risk manager version would
   be welcome but has not been done.
4. **Fills at bar close** on stop/TP triggers — optimistic on gaps.
5. **No slippage model for limit orders** — they always fill at the
   limit price if the bar touches it, which is aggressive.

All five divergences make backtest results better than reality. A
realistic mental model is: **expect live Sharpe to be 30-50 % lower
than backtest Sharpe** on the US strategies, and **2-5 % lower** on
the delta-neutral FundingRateArbitrage.

## Extending the backtester

The engine is ~400 lines and deliberately kept simple. Features that
would be welcome but are not currently implemented:

- **Risk manager hookup**. Wire `Backtester._open_position` through
  `RiskManager.check_trade` so the backtest respects the live gates.
- **Bracket order simulation**. Currently stops are managed by
  `Position.stop_loss` + the bar-close check; there is no notion of a
  separate stop order that could fail.
- **Bar-open fills** on stop/TP triggers. Model gap risk properly.
- **Walk-forward driver** built into `Backtester` instead of relying on
  Freqtrade's CLI for crypto and a manual split for equities.
- **Portfolio-level metrics** beyond single-strategy and
  single-asset — for example, correlation between the two US
  strategies on the same equity curve.

None of these are blocking for Phase 3 dry-run. They are quality-of-
life improvements for ongoing research.
