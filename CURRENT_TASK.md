# Tâche actuelle : Phase 3 — Dry-run Funding Rate Arbitrage

## Objectif

Lancer la stratégie FundingRateArbitrage en dry-run (paper trading) sur Binance Futures testnet pendant 30 jours. La stratégie a été validée walk-forward (Sharpe 4.8 train / 4.7 OOS, drop 2%). L'objectif est de confirmer que l'exécution live (latence, fills, slippage) ne dégrade pas les résultats avant de passer en argent réel.

## Rôle

engineer

## Contexte

### Ce qui a été fait
- FundingRateArbitrage implémentée dans Freqtrade (futures mode)
- backtest_funding_arb.py valide le walk-forward (Sharpe 4.7 OOS)
- Données futures téléchargées : 5 paires × OHLCV + mark + funding_rate (depuis 2020)
- BreakoutTrendFollowing en backup (Turtle System 2, spot, long-only)

### Logique de la stratégie (rappel)
- **Delta-neutre** : long spot + short perp quand funding rate > seuil (les longs payent les shorts)
- **Market-neutral** : le profit vient du funding rate, pas de la direction du prix
- **Paires** : BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT
- **Risque** : quasi nul si le hedge est maintenu — le seul risque est le basis risk (écart spot/perp)

## Plan d'implémentation

### Étape 1 — Configurer Binance Futures testnet (15 min)

Créer un compte testnet sur https://testnet.binancefuture.com et récupérer les clés API.

Mettre à jour `freqtrade_config/config_futures.json` :
```json
{
    "trading_mode": "futures",
    "margin_mode": "isolated",
    "exchange": {
        "name": "binance",
        "key": "${BINANCE_TESTNET_KEY}",
        "secret": "${BINANCE_TESTNET_SECRET}",
        "urls": {
            "api": "https://testnet.binancefuture.com"
        },
        "pair_whitelist": [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"
        ]
    },
    "dry_run": true,
    "dry_run_wallet": 10000,
    "stake_currency": "USDT",
    "stake_amount": "unlimited"
}
```

**Important** : Les clés API vont dans `.env`, PAS dans config.json. Utiliser les variables d'environnement.

### Étape 2 — Ajouter monitoring Telegram (15 min)

Configurer le bot Telegram dans config_futures.json :
```json
{
    "telegram": {
        "enabled": true,
        "token": "${TELEGRAM_BOT_TOKEN}",
        "chat_id": "${TELEGRAM_CHAT_ID}",
        "notification_settings": {
            "status": "on",
            "entry": "on",
            "exit": "on"
        }
    }
}
```

Commandes utiles une fois le bot lancé :
- `/status` — positions ouvertes
- `/profit` — P&L total
- `/daily` — P&L journalier
- `/forceexit <trade_id>` — exit d'urgence

### Étape 3 — Lancer Freqtrade en dry-run (10 min)

```bash
freqtrade trade \
    --strategy FundingRateArbitrage \
    --config freqtrade_config/config_futures.json \
    --db-url sqlite:///freqtrade_config/tradesv3_dryrun.sqlite
```

Vérifier que :
1. Le bot se connecte à Binance Futures testnet
2. Le bot détecte les funding rates
3. Le bot ouvre une position quand le funding rate dépasse le seuil
4. Le bot maintient le hedge delta-neutre
5. Les notifications Telegram arrivent

### Étape 4 — Script de monitoring (20 min)

Créer `scripts/monitor_dryrun.py` qui :
1. Lit la DB SQLite de Freqtrade (`tradesv3_dryrun.sqlite`)
2. Calcule le Sharpe rolling sur 7 jours
3. Calcule le P&L cumulé
4. Vérifie que le delta est bien neutre (position spot ≈ -position perp)
5. Alerte si Sharpe rolling < 1.0 ou si le delta dépasse 5%
6. S'exécute en cron toutes les heures

### Étape 5 — Lancer BreakoutTrendFollowing en parallèle (10 min)

En plus du funding arb, lancer le Breakout en dry-run spot :
```bash
freqtrade trade \
    --strategy BreakoutTrendFollowing \
    --config freqtrade_config/config_breakout.json \
    --db-url sqlite:///freqtrade_config/tradesv3_breakout_dryrun.sqlite
```

Ça tourne en parallèle. On collecte des données pour évaluer si le Breakout mérite d'être gardé.

### Étape 6 — Vérification finale

```bash
# Vérifier que les deux bots tournent
freqtrade show-config --config freqtrade_config/config_futures.json
freqtrade show-config --config freqtrade_config/config_breakout.json

# Vérifier les positions
sqlite3 freqtrade_config/tradesv3_dryrun.sqlite "SELECT * FROM trades ORDER BY open_date DESC LIMIT 5;"

# Vérifier le monitoring
python3 scripts/monitor_dryrun.py
```

## Fichiers concernés

### À modifier
- `freqtrade_config/config_futures.json` — Ajouter clés testnet + Telegram
- `.env` — Ajouter BINANCE_TESTNET_KEY, BINANCE_TESTNET_SECRET, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

### À créer
- `scripts/monitor_dryrun.py` — Monitoring automatique
- `scripts/start_dryrun.sh` — Script pour lancer les deux bots en parallèle

### Intouchables
- `FundingRateArbitrage.py` — Ne PAS modifier la stratégie validée
- `BreakoutTrendFollowing.py` — Ne PAS modifier les params Turtle
- `risk/manager.py` — Ne PAS modifier
- `main.py` / `main_v2.py` — Ne PAS modifier (bot actions US séparé)

## Critères de succès (après 30 jours de dry-run)

1. FundingRateArbitrage : Sharpe rolling 7j > 2.0 en dry-run
2. FundingRateArbitrage : Max drawdown < 5%
3. FundingRateArbitrage : Delta toujours < 5% (hedge maintenu)
4. Telegram notifications fonctionnelles
5. Monitoring automatique en place
6. Aucun crash du bot sur 30 jours

## Ce qui est hors scope

- Ne PAS passer en argent réel — dry-run uniquement
- Ne PAS modifier les stratégies — elles sont validées
- Ne PAS lancer d'Hyperopt supplémentaire
- Ne PAS ajouter de nouvelles paires
- Ne PAS toucher au bot actions US (Alpaca)

## Prochaines tâches (après le dry-run)

1. **Analyse dry-run** : Comparer Sharpe dry-run vs backtest. Si dry-run Sharpe > 2.0 → GO live
2. **Live progressif** : Commencer avec 500 USDT sur funding arb uniquement
3. **Scale up** : Si profitable 30j → monter à 2000 USDT → 5000 USDT
4. **Dashboard** : Vue unifiée actions US + crypto + alertes
