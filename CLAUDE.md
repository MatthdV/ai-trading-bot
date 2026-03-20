# AI Trading Bot

Bot de trading automatisé hybride : **Actions US** (Alpaca, bot custom) + **Crypto** (Freqtrade + CCXT).
Stratégie principale crypto : **FundingRateArbitrage** (delta-neutre, Sharpe 4.7 OOS validé).
Stratégie secondaire crypto : **BreakoutTrendFollowing** (Turtle System 2, long-only, dry-run).
Actions US : MeanReversion + Momentum via Alpaca (micro-live).

## Architecture hybride

```
strategies/              → Stratégies custom pour actions US (héritent de BaseStrategy)
core/                    → Clients actions US + risk manager legacy
  ├── alpaca_client.py         → Actions US (Alpaca API)
  ├── position_sizer.py        → Position sizing (Kelly)
  └── risk_manager.py          → Risk manager simplifié (legacy)
risk/                    → Risk manager avancé (Half-Kelly, ATR stops, daily reset, audit log)
analysis/                → News analyzer, sentiment
backtesting/             → Engine de backtest custom (actions US)
freqtrade_config/        → TOUT Freqtrade ici (crypto)
  ├── config.json              → Config Binance spot (dry-run)
  ├── config_breakout.json     → Config sans ROI pour BreakoutTrendFollowing
  ├── config_futures.json      → Config Binance futures (funding rate arb)
  └── user_data/strategies/
      ├── BreakoutTrendFollowing.py   → Turtle System 2 (spot, long-only)
      ├── FundingRateArbitrage.py     → Delta-neutre (futures, market-neutral)
      ├── MeanReversionFreqtrade.py   → DEPRECATED — Sharpe négatif en crypto
      └── MomentumFreqtrade.py        → DEPRECATED — ne passe pas l'OOS
scripts/
  ├── validate_change.py       → Gate 2 actions US
  ├── validate_hyperopt.py     → Walk-forward validation crypto
  └── backtest_funding_arb.py  → Backtest delta-neutre custom
notifications/           → Telegram alerts
```

## Résultats walk-forward validés

| Stratégie | Sharpe train | Sharpe OOS | Drop | Statut |
|-----------|-------------|-----------|------|--------|
| FundingRateArbitrage | 4.8 | 4.7 | 2% | ✅ VALIDÉ |
| BreakoutTrendFollowing | 0.16 | -0.17 | ❌ | 🟡 Dry-run |
| MomentumFreqtrade | 0.68 | -0.52 | 177% | ❌ Abandonné |
| MeanReversionFreqtrade | -1.33 | -2.65 | N/A | ❌ Abandonné |

## Exchanges supportés

| Exchange | Marchés | Bot | Statut | Heures |
|----------|---------|-----|--------|--------|
| Alpaca | Actions US, ETF | Custom (main.py) | ✅ Micro-live | 15h30-22h Paris |
| Binance | Crypto spot | Freqtrade (Breakout) | 🟡 Dry-run | 24/7 |
| Binance | Crypto futures | Freqtrade (Funding Arb) | 🚧 À déployer | 24/7 |

## Watchlist

Actions : AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA, META, NFLX, AMD, CRM, BABA, UBER, COIN, PLTR, RKLB
Crypto spot : BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT
Crypto futures : BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT, BNB/USDT:USDT, XRP/USDT:USDT

## Règles NON-NÉGOCIABLES

1. **JAMAIS de trade sans stop-loss** (ATR-based, trailing Freqtrade, ou delta-neutral hedge)
2. **Position max = 5% du portfolio** (Half-Kelly), max 5 positions simultanées, max 3 par secteur
3. **Daily max-loss = -2%** → trading halt automatique (reset le lendemain)
4. **Max drawdown = -10%** → halt total + alerte
5. **Logger la raison de chaque trade AVANT exécution** (audit log)
6. **Mode paper/dry-run obligatoire** tant que Sharpe < 1.5 sur backtest 90 jours
7. **Tout changement de stratégie** doit passer la two-gate verification + walk-forward
8. **Ne JAMAIS modifier risk/manager.py ou core/risk_manager.py** sans approbation explicite
9. **Ne JAMAIS hardcoder** de clés API — tout passe par .env (ou config.json Freqtrade)
10. **Le funding rate arb doit être delta-neutre** — toujours long spot + short perp (ou inverse)

## Two-Gate Verification

**Gate 1 — Code Review** : Absence de look-ahead bias, survivorship bias, division par zéro, edge cases.
**Gate 2 — Walk-forward** :
- Actions US : `python3 scripts/validate_change.py --compare` (Sharpe ≥ 1.0, WR ≥ 50%, DD ≤ 15%, PF ≥ 1.2)
- Crypto spot : `freqtrade backtesting` + `validate_hyperopt.py` (Sharpe OOS drop < 30%)
- Funding arb : `python3 scripts/backtest_funding_arb.py` (Sharpe ≥ 2.0, DD ≤ 5%)

## Contexte de session

Lire `CURRENT_TASK.md` pour l'objectif en cours. Ne charger que les fichiers listés.

## Agents

Un agent par session (voir `.claude/agents/`) :
- `quant.md` — Stratégies et optimisation
- `risk-auditor.md` — Audit risk management
- `engineer.md` — Infra, API, tests
- `reviewer.md` — Code review + validation backtest

## Tests

```bash
# Actions US
python3 -m pytest tests/ -v
python3 run_backtest.py --symbols AAPL MSFT GOOGL TSLA SPY --period 2y --capital 10000
python3 scripts/validate_change.py --compare

# Crypto — Breakout (spot)
freqtrade backtesting --strategy BreakoutTrendFollowing --config freqtrade_config/config_breakout.json --timerange 20170101-

# Crypto — Funding Rate Arbitrage (futures)
python3 scripts/backtest_funding_arb.py

# Walk-forward validation
python3 scripts/validate_hyperopt.py --strategy <Name> --train-sharpe X --oos-sharpe Y
```

## Roadmap

- [x] Phase 1 : Aligner live bot sur stratégies backtestées (MeanReversion + Momentum)
- [x] Phase 1b : Installer Freqtrade + porter stratégies
- [x] Phase 2 : Hyperopt — tester MeanReversion, Momentum, Breakout (résultat : échec OOS)
- [x] Phase 2b : FundingRateArbitrage — stratégie delta-neutre validée (Sharpe 4.7 OOS)
- [ ] Phase 3 : Dry-run 30 jours — Funding Arb (futures) + Breakout (spot) ← PROCHAINE
- [ ] Phase 3b : Configurer clés API Binance testnet + lancer Freqtrade dry-run
- [ ] Phase 4 : Live progressif — commencer avec 500 USDT sur funding arb
- [ ] Phase 5 : Dashboard unifié actions + crypto + monitoring Telegram
