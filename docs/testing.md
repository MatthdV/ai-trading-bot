# Testing

Reference for the test suite layout, what each file covers, how to run
it, and the review workflow for money-risk changes.

## Test suite layout

```
tests/
├── test_bot.py                    # Legacy smoke tests (print-style)
├── test_strategies.py             # pytest: strategies + backtester + risk manager
└── test_momentum_indicators.py    # pytest: EMA, MACD, ATR, ADX
```

All three are discoverable by `pytest`. As of Phase 2c-bis the suite
runs in under one second and reports:

```
================= 45 passed, 1 skipped, 348 warnings in 0.72s ==================
```

The 348 warnings are all deprecation notices for `datetime.utcnow()`
(removed in Python 3.14). They do not affect correctness; fixing them
is backlog.

## Running the tests

```sh
python3 -m pytest tests/ -v
```

Or a specific file / class / test:

```sh
python3 -m pytest tests/test_strategies.py::TestRiskManager -v
python3 -m pytest tests/test_momentum_indicators.py::TestATR -v
python3 -m pytest tests/test_strategies.py::TestRiskManager::test_blocks_on_daily_loss_halt -v
```

Pre-commit hooks run independently:

```sh
pre-commit run --all-files
```

This covers gitleaks, `detect-private-key`, `check-added-large-files`,
whitespace, JSON/YAML validity, and the custom `no-env-files` hook.
Nothing overlaps with pytest — they are orthogonal safety nets.

## What each file covers

### `tests/test_strategies.py` (33 tests across 5 classes)

Pure pytest, no external dependencies, no network.

**`TestBacktester`** — synthetic-data integration test that feeds a
DataFrame into `Backtester.run` and verifies it terminates with at
least one trade and a non-NaN equity curve.

**`TestMeanReversionStrategy`** (7 tests)

| Case | Verifies |
|------|----------|
| `test_insufficient_data` | `analyze` returns NEUTRAL when `len(candles) < min_candles` |
| `test_normal_data_low_strength` | Ranging data produces no high-strength signal |
| `test_long_on_oversold` | Synthetic oversold RSI+BB+Z combo triggers LONG |
| `test_should_enter_threshold` | `should_enter` respects the `strength >= 0.3` gate |
| `test_should_exit_stop_loss` | -5 % loss triggers `should_exit` |
| `test_should_exit_take_profit` | Z-score in `[-0.2, 0.2]` triggers `should_exit` |
| `test_should_exit_timeout` | Holding past 168 h triggers `should_exit` |

**`TestMomentumStrategy`** (6 tests)

| Case | Verifies |
|------|----------|
| `test_insufficient_data` | Guard on short input |
| `test_neutral_on_flat_data` | Flat prices produce no actionable signal |
| `test_trending_data` | Synthetic uptrend (with MACD crossover + ADX > 25) triggers LONG |
| `test_should_enter_threshold` | Strength gate |
| `test_should_exit_adx_collapse` | ADX < 14 triggers exit |
| `test_should_exit_opposite_cross` | Bearish MACD cross on a long triggers exit |

**`TestBacktestIntegration`** (2 tests)

| Case | Verifies |
|------|----------|
| `test_backtest_with_synthetic_data` | End-to-end with a generated OHLCV frame |
| `test_backtest_with_real_data` | End-to-end using yfinance (marked skippable if offline) |

**`TestRiskManager`** (12 tests) — the risk kernel safety net

| Case | Verifies |
|------|----------|
| `test_allows_valid_trade` | Empty portfolio → trade allowed |
| `test_blocks_when_max_positions` | 5 positions held → 6th rejected with "Max positions" |
| `test_blocks_sector_concentration` | Sector cap enforced via `SECTOR_MAP` |
| `test_blocks_duplicate_symbol` | Already-holding rejected with "Already holding" |
| `test_blocks_on_daily_loss_halt` | Second call with 97 % equity triggers -3 % daily loss halt |
| `test_blocks_on_drawdown_halt` | Drop to 88 % with relaxed daily-loss triggers drawdown halt first |
| `test_kelly_sizing` | `calculate_size` returns positive and respects `max_position_pct` cap |
| `test_atr_stops` | LONG: SL below entry, TP above, R/R ≥ min_risk_reward |
| `test_short_stops_inverted` | SHORT: SL above entry, TP below, R/R intact |
| `test_trailing_stop_ratchets_up_only` | New stop > old; retreating price → returns None |
| `test_trailing_stop_not_active_below_threshold` | Below +1 % → returns None |
| `test_daily_report` | Dict keys present |

The five "failure path" tests (daily halt, drawdown halt, SHORT stops,
trailing ratchet, activation threshold) were added in Phase 2c-bis
specifically to cover the branches most likely to regress in future
refactors of `risk/manager.py`.

**`TestBaseClasses`** (5 tests) — `Signal`, `Position` helpers

| Case | Verifies |
|------|----------|
| `test_signal_clamps_strength` | `Signal.__post_init__` clamps to `[0, 1]` |
| `test_position_pnl_long` | `unrealized_pnl` and `unrealized_pnl_pct` for LONG |
| `test_position_pnl_short` | Same for SHORT |
| `test_position_holding_duration` | Hours since entry, handles naive/aware tz mixing |

### `tests/test_momentum_indicators.py` (11 tests, 4 classes)

Dedicated indicator-math tests added in Phase 2c-bis. Ported from a
deleted `tests/test_indicators.py` that targeted the now-removed
`_legacy_rsi_macd_kelly` module.

**`TestEMA`** (3 tests)

| Case | Verifies |
|------|----------|
| `test_ema_differs_from_sma_on_trending_data` | EMA > SMA on an accelerating uptrend |
| `test_ema_on_constant_prices_equals_price` | EMA(100) of 100s is 100 |
| `test_ema_incorporates_history_not_just_last_period` | Seed from 50s pulls the EMA below 100 |

**`TestMACD`** (3 tests)

| Case | Verifies |
|------|----------|
| `test_signal_line_is_ema_of_macd_not_proportional` | Signal line is not `0.9 * macd` (regression guard for an old bug) |
| `test_histogram_is_macd_minus_signal` | `histogram == macd_line - signal_line` exactly |
| `test_macd_positive_on_sustained_uptrend` | Fast EMA > Slow EMA → positive MACD |

**`TestATR`** (3 tests)

| Case | Verifies |
|------|----------|
| `test_atr_is_positive_for_non_flat_data` | Non-trivial ATR |
| `test_atr_is_zero_for_flat_prices` | Identical H/L/C → ATR == 0 |
| `test_atr_grows_with_volatility` | Wider bars → larger ATR |

**`TestADX`** (2 tests)

| Case | Verifies |
|------|----------|
| `test_adx_high_on_strong_trend` | Linear uptrend → ADX > 20 |
| `test_adx_low_on_choppy_range` | Random walk ADX < linear trend ADX |

### `tests/test_bot.py` (3 tests, print-style)

Legacy integration-style tests. Run as `python3 tests/test_bot.py`
(not pytest-compatible for the runner) and exercise:

- `test_kelly_sizer` — the dormant `KellyPositionSizer` helper
- `test_risk_manager` — smoke test of `RiskManager` via its live API
- `test_alpaca_connection` — real Alpaca API round-trip using `.env`
  credentials

The Alpaca connection test is the only **integration** test in the
suite. It needs valid paper-trading credentials. Pytest will skip it
with a warning if Alpaca is down.

The print-style format is a historical artefact from the original
codebase. It was kept (rather than converted to pytest) because it is
the quickest way to manually verify the `.env` before a dry-run
session. Phase 2c-bis fixed an `await test_kelly_sizer()` bug — the
function is sync, so the await raised a TypeError on `python3 tests/
test_bot.py`.

## Coverage map

| Module | Coverage | Notes |
|--------|----------|-------|
| `strategies/base.py` | Dataclass helpers tested in `TestBaseClasses` | |
| `strategies/mean_reversion.py` | Well-covered in `TestMeanReversionStrategy` | |
| `strategies/momentum.py` | Well-covered in `TestMomentumStrategy` + indicators | |
| `risk/manager.py` | Fully covered in `TestRiskManager` | Every gate has a test |
| `backtesting/engine.py` | Integration-only via `TestBacktestIntegration` | No unit tests for internal helpers |
| `core/alpaca_client.py` | Only the live integration test in `test_bot.py` | No unit test with mock Session |
| `core/state_persistence.py` | **No tests** | Only manual verification during Phase 2c |
| `main.py::TradingBot` | **No tests** | Orchestrator — too stateful for reasonable unit tests |
| `main.py::SyncTelegram` | **No tests** | Depends on live Telegram bot |
| `notifications/telegram_bot.py` | **No tests** | Same reason |
| `freqtrade_config/strategies/*` | Tested via Freqtrade's own backtester | Walk-forward is the gate |

### Coverage gaps to address

1. **`state_persistence.py`** — needs a pytest suite with a
   `tmp_path` fixture verifying append atomicity, corruption recovery
   (truncated tail), rename-atomic save, and permissions (0o600 /
   0o700). Manual verification exists in
   [`state-persistence.md`](state-persistence.md) §"Testing".
2. **`alpaca_client.py::_request`** — needs a mock-Session suite
   covering every branch of the retry/backoff loop: 429 with int
   header, 429 with HTTP-date header, 503 no header, Timeout-then-
   success, ConnectionError-exhaustion, 404 returns None, 500 raises
   immediately.
3. **`main.py::TradingBot`** — the orchestrator is stateful and
   I/O-heavy but several of its methods (`_scan_all_symbols`,
   `_execute_buy` Section 1) could be tested with a fake
   `AlpacaClient`. The B1 cascade (cancel → flatten → escalate) is
   the critical path that deserves a dedicated test.

## Money-risk PR review workflow

From [`../CLAUDE.md`](../CLAUDE.md) §"Code review" and the memory file
`feedback_code_review_approach.md`: any change touching `main.py`,
`core/alpaca_client.py`, `risk/manager.py`, `core/state_persistence.py`,
the Freqtrade strategies, or anything that could affect order execution
/ state persistence must be reviewed via **option C**:

1. **Dispatch three specialised review agents in parallel** from the
   Claude Code superpowers toolkit:
   - `pr-review-toolkit:silent-failure-hunter` — finds swallowed
     exceptions, unprotected positions, orphan orders
   - `pr-review-toolkit:code-reviewer` — finds convention violations,
     CLAUDE.md rule breaches, test gaps
   - `general-purpose` with a security-specific brief — finds secret
     leaks, file permission issues, deploy script regressions

2. **Do a manual verification pass afterwards**, with `git diff` and
   `grep`. The Phase 2c review proved this is necessary: agents
   missed that three methods (`update_trailing_stop`, `load_state`,
   `reconcile_with_broker`) were defined but never called. Agents
   search **in** the code; they don't always notice absences.

Single-pass review is fine for cosmetic PRs (whitespace, docs,
comment fixes). Anything that changes behaviour on money-touching
paths should go through the three-agent pass.

## Running the walk-forward gate

Before committing a strategy change for a US strategy:

```sh
python3 scripts/validate_change.py --save-baseline      # before the change
# ... make the change ...
python3 scripts/validate_change.py --compare            # after the change
```

Thresholds: Sharpe ≥ 1.0, win rate ≥ 50 %, max DD ≤ 15 %, profit
factor ≥ 1.2.

For a Freqtrade crypto strategy:

```sh
freqtrade backtesting \
    --strategy YourStrategy \
    --config freqtrade_config/config_futures.json \
    --timerange 20200101-20240101     # training window
# (note the train Sharpe)

freqtrade backtesting \
    --strategy YourStrategy \
    --config freqtrade_config/config_futures.json \
    --timerange 20240101-20260101     # OOS window
# (note the OOS Sharpe)

python3 scripts/validate_hyperopt.py \
    --strategy YourStrategy \
    --train-sharpe 4.8 --oos-sharpe 4.7
```

The script rejects any strategy whose OOS Sharpe dropped more than
30 % from training.

## Test environment

The tests run against:

- **Python 3.11** in the repo venv (`.venv/`, but the symlinks are
  currently broken — they point to `/Users/c_mdevillele/...`, a user
  that doesn't exist on this host)
- **Python 3.14** on the system (`/Library/Frameworks/Python.framework/
  Versions/3.14/`). This is what `python3 -m pytest tests/` actually
  runs.

The venv rebuild is a known infra debt item. Once fixed, the tests
should run via `.venv/bin/python -m pytest tests/`.

`yfinance` is required for `test_backtest_with_real_data` and
`run_backtest.py`. It is currently installed in the system Python, not
the venv. This is also known infra debt.

## What the tests explicitly do not check

These are **not** regression-tested today:

1. **Thread safety of `_BarCache`**. Relied upon to be non-critical.
2. **`ThreadPoolExecutor` + `_last_atr` dict writes from multiple
   threads.** Relies on CPython GIL atomicity for single-key writes.
3. **Signal handler ordering** (`SIGTERM` vs `SIGINT`).
4. **The live Telegram notifier's HTTP retries** (uses aiohttp inside
   an async context).
5. **The deploy script's SSH path**. Manual verification only.
6. **Docker Compose file schema.** Neither pytest nor pre-commit runs
   `docker compose config` (Docker not installed locally).

If any of the above becomes a concern, add a test rather than manual
re-verification. The test suite is cheap to extend and runs fast.
