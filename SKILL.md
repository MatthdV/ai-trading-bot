---
name: trading-bot
description: Automated trading bot for paper trading on Alpaca with AI-driven strategies (RSI, MACD, Kelly Criterion). Supports backtesting, risk management, and Telegram notifications. Use when trading stocks, crypto, or implementing automated investment strategies with proper risk management.
---

# 🤖 Trading Bot — AI-Powered Automated Trading

Bot de trading automatisé pour paper trading sur Alpaca avec stratégies IA.

## 🎯 Objectifs

- **Phase 1** : Paper trading (3 mois) — Valider stratégie
- **Phase 2** : Micro-live (€100) — Test exécution réelle  
- **Phase 3** : Scale (€1000) — Objectif 5-10% mensuel

## 📊 Stratégie Implémentée

### Indicateurs
- **RSI** (14 périodes) : >70 = overbought, <30 = oversold
- **MACD** (12, 26, 9) : Crossover bullish/bearish
- **Moving Averages** : EMA 20 & 50

### Gestion des Risques
- **Kelly Criterion** : Position sizing optimal
- **Stop-loss** : -2% par trade
- **Take-profit** : +4% par trade (ratio 2:1)
- **Max drawdown** : -15% portfolio

## 🛠️ Architecture

```
trading-bot/
├── config/
│   ├── settings.py        # Paramètres globaux
│   └── strategy_config.py # Config stratégie
├── strategies/
│   ├── base_strategy.py   # Classe de base
│   └── rsi_macd_kelly.py  # Stratégie principale
├── core/
│   ├── alpaca_client.py   # API Alpaca
│   ├── risk_manager.py    # Gestion risque
│   └── position_sizer.py  # Kelly Criterion
├── backtest/
│   └── backtester.py      # Tests historiques
├── notifications/
│   └── telegram_bot.py    # Alertes Telegram
└── main.py                # Point d'entrée
```

## 🚀 Utilisation

### Lancer le bot
```bash
python3 ~/.openclaw/workspace/skills/trading-bot/main.py --mode paper
```

### Backtest
```bash
python3 ~/.openclaw/workspace/skills/trading-bot/backtest/backtester.py --days 180
```

### Voir positions
```bash
python3 ~/.openclaw/workspace/skills/trading-bot/scripts/status.py
```

## 📈 Métriques Trackées

- Sharpe ratio
- Win rate
- Max drawdown
- Profit factor
- Kelly fraction utilisée

## ⚠️ Avertissements

- **JAMAIS** trader avec de l'argent que tu ne peux pas perdre
- Paper trading obligatoire avant live
- Surveillance quotidienne recommandée
- Marchés volatiles = risque élevé
