# 🤖 AI Trading Bot

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Automated trading bot with technical analysis (RSI, MACD) and news sentiment analysis for US equities.

## 🎯 Goal

Target 5–10% monthly return on US equities with strict risk management.

## 📊 Strategy

### Technical Indicators
- **RSI(14)** — mean reversion (oversold < 30, overbought > 70)
- **MACD(12,26,9)** — momentum and trend
- **EMA(20,50)** — trend direction

### News Analysis
- Sentiment scoring on financial headlines
- Economic event detection (FED decisions, earnings)
- Signal confidence adjustment based on news context

### Risk Management
- **Kelly Criterion** — optimal position sizing (fractional 25%)
- **Stop-loss** — −2% per trade
- **Take-profit** — +4% per trade (2:1 ratio)
- **Max drawdown** — −15% portfolio hard limit

## 🏗️ Architecture

```
trading-bot/
├── core/
│   ├── alpaca_client.py      # Alpaca Markets API
│   ├── risk_manager.py       # Risk management
│   └── position_sizer.py     # Kelly Criterion sizing
├── strategies/
│   └── rsi_macd_kelly.py     # Main strategy
├── analysis/
│   └── news_analyzer.py      # News & sentiment analysis
└── main.py                   # Entry point
```

## 📈 Performance targets

| Metric | Target |
|--------|--------|
| Test capital | $100,000 (paper trading) |
| Win rate | 55% |
| Sharpe ratio | > 1.5 |
| Max drawdown | < 15% |

## 🚀 Usage

```bash
git clone https://github.com/MatthdV/ai-trading-bot.git
cd ai-trading-bot

export ALPACA_API_KEY="your_key"
export ALPACA_SECRET_KEY="your_secret"

# Paper trading
python3 main.py --mode paper

# Live (after validation)
python3 main.py --mode live
```

## ⚠️ Disclaimer

Educational project. Trading involves risk. Never trade money you cannot afford to lose.

## 🛠️ Stack

Python 3.11+ · Alpaca Markets API · OpenAI API (sentiment analysis)

## 👨‍💻 Author

**Matthieu de Villele** — Automation & AI Engineer  
[LinkedIn](https://linkedin.com/in/matthieudevillele) · [GitHub](https://github.com/MatthdV)
