# Technical Documentation

Reference documentation for the AI Trading Bot codebase. Intended audience:
developers extending the bot, operators running it in production, or reviewers
auditing its safety properties.

For high-level orientation (strategies, roadmap, non-negotiable rules), see
[`../CLAUDE.md`](../CLAUDE.md). For the active work item and session logs,
see [`../CURRENT_TASK.md`](../CURRENT_TASK.md). These docs stay focused on
what the code **does** and why.

## Scope

The bot is hybrid:

- **US equities** (`main.py`, `core/`, `risk/`, `strategies/`, `backtesting/`)
  — a custom synchronous loop built on top of the Alpaca REST API, with its
  own risk manager, state persistence, and backtesting engine.
- **Crypto** (`freqtrade_config/`) — two strategies delegated to Freqtrade,
  deployed as hardened Docker containers on a VPS.

The two worlds share only the repository and a common deployment script —
there is no runtime coupling.

## Reading order

If you are new to the codebase, read in this order:

1. [`architecture.md`](architecture.md) — module graph, data flow, boundaries
2. [`execution-flow.md`](execution-flow.md) — the main loop in `main.py`
3. [`risk-management.md`](risk-management.md) — pre-trade gates and stops
4. [`state-persistence.md`](state-persistence.md) — crash recovery
5. [`strategies.md`](strategies.md) — both the Python and Freqtrade strategies

The rest can be read on demand:

- [`alpaca-client.md`](alpaca-client.md) — HTTP retry and error semantics
- [`backtesting.md`](backtesting.md) — backtesting engine + two-gate validation
- [`testing.md`](testing.md) — test layout and what each test covers
- [`deployment.md`](deployment.md) — Docker hardening and VPS deploy flow

## Invariants at a glance

These are enforced in code and must hold across refactors. Changes that
break them require explicit approval.

| # | Rule | Enforced in |
|---|------|-------------|
| 1 | Never trade without a protective stop-loss | `main.py::_execute_buy`, `risk/manager.py::calculate_stops` |
| 2 | Max 5 % of portfolio per position | `risk/manager.py::RiskConfig.max_position_pct` |
| 3 | Max 5 simultaneous positions | `risk/manager.py::check_trade` |
| 4 | Max 3 positions per sector | `risk/manager.py::check_trade` + `SECTOR_MAP` |
| 5 | Daily loss ≥ 2 % halts trading for the day | `risk/manager.py::check_trade` |
| 6 | Portfolio drawdown ≥ 10 % halts trading permanently | `risk/manager.py::check_trade` |
| 7 | Every pre-trade decision is audit-logged | `risk/manager.py::_log_check` |
| 8 | Partial order failures never leave a naked position | `main.py::_execute_buy` (cancel-or-flatten cascade) |
| 9 | Critical alerts are persisted on disk if Telegram is down | `main.py::SyncTelegram.risk_alert` |
| 10 | State is journaled and reconciled with the broker at startup | `core/state_persistence.py`, `main.py::_recover_state` |

Violations of 1, 2, 8, or 9 are root causes for losing real money. Treat any
PR touching `main.py`, `core/alpaca_client.py`, `risk/manager.py`, or
`core/state_persistence.py` as money-risk code and review accordingly — see
[`testing.md`](testing.md) for the review workflow.

## Dual code surface

Two distinct execution engines run simultaneously:

```
┌─────────────────────────────┐       ┌─────────────────────────────┐
│  US equity bot (this repo)  │       │  Freqtrade crypto bots      │
│  main.py + custom modules   │       │  (Docker containers)        │
│  ─ Alpaca REST              │       │  ─ Binance spot + futures   │
│  ─ Synchronous main loop    │       │  ─ Freqtrade event loop     │
│  ─ Custom RiskManager       │       │  ─ Freqtrade protections    │
│  ─ Custom backtester        │       │  ─ freqtrade backtesting    │
└─────────────────────────────┘       └─────────────────────────────┘
         ▲                                        ▲
         └──────── shared repo, shared ───────────┘
                  .env, shared deploy_vps.sh
```

The two surfaces never touch at runtime. They share:

- Source control
- `.env` for API keys and Telegram credentials
- `scripts/deploy_vps.sh` to push the Freqtrade side to production
- Telegram notifications (independent channels, same bot token)

Any new feature must state explicitly which surface it belongs to. A feature
that crosses the boundary (for example, a shared dashboard reading both data
sources) should live in its own module, not inside `main.py` or the Freqtrade
strategies.
