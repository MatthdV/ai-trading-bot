# 🤖 AI Trading Bot

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Bot de trading automatisé avec analyse technique (RSI, MACD) et analyse des news (sentiment analysis).

## 🎯 Objectif

Générer 5-10% de rendement mensuel sur le marché des actions US avec une gestion des risques stricte.

## 📊 Stratégie

### Indicateurs Techniques
- **RSI(14)** : Mean reversion (oversold < 30, overbought > 70)
- **MACD(12,26,9)** : Momentum et tendance
- **EMA(20,50)** : Direction de la tendance

### Analyse des News
- Sentiment scoring sur headlines financières
- Détection événements économiques (FED, earnings)
- Ajustement confiance des signaux

### Gestion des Risques
- **Kelly Criterion** : Position sizing optimal (fractional 25%)
- **Stop-loss** : -2% par trade
- **Take-profit** : +4% par trade (ratio 2:1)
- **Max drawdown** : -15% portfolio

## 🏗️ Architecture

```
trading-bot/
├── core/
│   ├── alpaca_client.py      # API Alpaca Markets
│   ├── risk_manager.py        # Gestion des risques
│   └── position_sizer.py      # Kelly Criterion sizing
├── strategies/
│   └── rsi_macd_kelly.py      # Stratégie principale
├── analysis/
│   └── news_analyzer.py       # Analyse news & sentiment
└── main.py                    # Point d'entrée
```

## 📈 Performance

| Métrique | Valeur |
|----------|--------|
| Capital test | $100,000 (paper trading) |
| Win rate target | 55% |
| Sharpe ratio target | > 1.5 |
| Max drawdown | < 15% |

## 🚀 Utilisation

```bash
# Installation
git clone https://github.com/MatthdV/ai-trading-bot.git
cd ai-trading-bot

# Configuration
export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"

# Paper trading
python3 main.py --mode paper

# Live (après validation)
python3 main.py --mode live
```

## ⚠️ Disclaimer

Ce bot est à but éducatif. Le trading comporte des risques. Ne tradez pas avec de l'argent que vous ne pouvez pas perdre.

## 🛠️ Stack

- Python 3.11+
- Alpaca Markets API
- OpenAI API (sentiment analysis)
- Pure Python (pas de dépendances externes lourdes)

## 📅 Historique

- **2026-03-19** : v2.0 - Ajout module News Analysis
- **2026-03-19** : v1.0 - Lancement paper trading

## 👨‍💻 Auteur

**Matthieu de Villele** - Automation & AI Engineer
- Portfolio: [matthieudevillele.com](https://matthieudevillele.com)
- LinkedIn: [linkedin.com/in/matthieu-devillele](https://linkedin.com/in/matthieu-devillele)

---

*Projet développé avec Claude Code dans le cadre de l'automatisation IA.*
