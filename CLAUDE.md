# AI Trading Bot

Bot de trading automatisé hybride : **Actions US** (Alpaca, bot custom) + **Crypto** (Freqtrade + CCXT).
Stratégie principale crypto : **FundingRateArbitrage** (delta-neutre, Sharpe 4.7 OOS validé).
Stratégie secondaire crypto : **BreakoutTrendFollowing** (Turtle System 2, long-only, dry-run).
Actions US : MeanReversion + Momentum via Alpaca (micro-live).

## État actuel (2026-04-09)

⚠️ **Le bot US est DOWN** — erreur HTTP 401 Unauthorized sur paper-api.alpaca.markets. Clés API expirées/révoquées depuis ~2 avril. Matthieu doit régénérer les clés sur app.alpaca.markets → Paper Trading → API Keys et mettre à jour `.env`.

✅ **Phase 2c-bis terminée (2026-04-09)** — les 9 bloquants (B1-B9) et 8 hautes priorités (H1-H8) identifiés par le code review du 9 avril sont tous appliqués sur `feat/freqtrade-crypto-strategies`. 11 commits atomiques. Tests : 44 passed. Détails dans `CURRENT_TASK.md`. **Avant de lancer un dry-run**, il reste à : (1) régénérer les clés Alpaca et les mettre dans `.env`, (2) valider visuellement une première boucle sur paper trading, (3) installer yfinance proprement dans la venv du repo (broken symlinks sur l'ancien utilisateur).

## Architecture hybride

```
strategies/              → Stratégies custom pour actions US (héritent de BaseStrategy)
core/                    → Clients actions US + risk manager legacy ⚠️ NE PLUS UTILISER core/risk_manager.py
  ├── alpaca_client.py         → Actions US (Alpaca API) — manque retry/backoff
  ├── position_sizer.py        → Position sizing (Kelly) — NON UTILISÉ actuellement
  └── risk_manager.py          → ⛔ LEGACY — thresholds FAUX (10%, -5%, -15%), à remplacer
risk/                    → Risk manager avancé (Half-Kelly, ATR stops, daily reset, audit log) ✅
backtesting/             → Engine de backtest custom (actions US) ✅
backtest/                → ⛔ LEGACY — backtester.py à supprimer (utilise _legacy strategy)
freqtrade_config/        → TOUT Freqtrade ici (crypto)
  ├── config.json              → Config Binance spot (dry-run)
  ├── config_breakout.json     → Config sans ROI pour BreakoutTrendFollowing
  ├── config_futures.json      → Config Binance futures (funding rate arb)
  └── strategies/              → user_data_dir=freqtrade_config, strategy_path=strategies/
      ├── BreakoutTrendFollowing.py   → Turtle System 2 (spot, long-only)
      ├── FundingRateArbitrage.py     → Delta-neutre (futures, market-neutral)
      ├── MeanReversionFreqtrade.py   → DEPRECATED — Sharpe négatif en crypto
      └── MomentumFreqtrade.py        → DEPRECATED — ne passe pas l'OOS
scripts/
  ├── validate_change.py       → Gate 2 actions US
  ├── validate_hyperopt.py     → Walk-forward validation crypto
  └── backtest_funding_arb.py  → Backtest delta-neutre custom
notifications/           → Telegram alerts ✅ (implémenté mais PAS intégré dans main.py)
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
| Alpaca | Actions US, ETF | Custom (main.py) | ❌ DOWN (401) | 15h30-22h Paris |
| Binance | Crypto spot | Freqtrade (Breakout) | 🟡 Dry-run | 24/7 |
| Binance | Crypto futures | Freqtrade (Funding Arb) | 🚧 À déployer | 24/7 |

## Watchlist

Actions : AAPL, MSFT, GOOGL, AMZN, TSLA, NVDA, META, NFLX, AMD, CRM, BABA, UBER, COIN, PLTR, RKLB
Crypto spot : BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT
Crypto futures : BTC/USDT:USDT, ETH/USDT:USDT, SOL/USDT:USDT, BNB/USDT:USDT, XRP/USDT:USDT

## Règles NON-NÉGOCIABLES

1. **JAMAIS de trade sans stop-loss** (ATR-based via risk/manager.py, trailing Freqtrade, ou delta-neutral hedge)
2. **Position max = 5% du portfolio** (Half-Kelly), max 5 positions simultanées, max 3 par secteur
3. **Daily max-loss = -2%** → trading halt automatique (reset le lendemain)
4. **Max drawdown = -10%** → halt total + alerte
5. **Logger la raison de chaque trade AVANT exécution** (audit log via risk/manager.py)
6. **Mode paper/dry-run obligatoire** tant que Sharpe < 1.5 sur backtest 90 jours
7. **Tout changement de stratégie** doit passer la two-gate verification + walk-forward
8. **Ne JAMAIS modifier risk/manager.py** sans approbation explicite
9. **Ne JAMAIS hardcoder** de clés API — tout passe par .env (ou config.json Freqtrade)
10. **Le funding rate arb doit être delta-neutre** — toujours long spot + short perp (ou inverse)

## Two-Gate Verification

**Gate 1 — Code Review** : Absence de look-ahead bias, survivorship bias, division par zéro, edge cases.
**Gate 2 — Walk-forward** :
- Actions US : `python3 scripts/validate_change.py --compare` (Sharpe ≥ 1.0, WR ≥ 50%, DD ≤ 15%, PF ≥ 1.2)
- Crypto spot : `freqtrade backtesting` + `validate_hyperopt.py` (Sharpe OOS drop < 30%)
- Funding arb : `python3 scripts/backtest_funding_arb.py` (Sharpe ≥ 2.0, DD ≤ 5%)

## Décisions de design

### Sizing : pourquoi on n'utilise pas Half-Kelly en live (2026-04-09)

`risk/manager.py:calculate_size()` implémente bien Half-Kelly mais **n'est pas
appelé** dans la boucle d'exécution. Le bot utilise `strategy.calculate_position_size()`
(% fixe par stratégie) + le cap `max_position_pct = 0.05` du risk manager.

**Raisonnement :**
- Half-Kelly demande des stats stables *win_rate / avg_win / avg_loss* par stratégie.
- On ne dispose que de stats de backtest — biaisées par look-ahead + survivorship +
  optimisation walk-forward — pas de stats live roulantes.
- Appliquer Kelly avec des paramètres biaisés amplifie l'overfitting : le sizing
  devient le reflet du bruit historique, pas de l'edge réel.
- Le garde-fou `max_position_pct = 0.05` (règle non-négociable n°2) couvre le
  worst-case indépendamment du sizing choisi par la stratégie.

**Quand re-activer Kelly :** quand un pipeline de stats rollantes sur les trades
*live* (pas backtest) sera en place — typiquement après 100+ trades par stratégie
sur au moins 3 mois. Chantier à ouvrir à ce moment-là, pas avant.

**Ne PAS brancher Kelly en attendant.** C'est une décision, pas une omission.

---

## Chantiers post-audit (2026-04-06)

### ⛔ BLOQUANTS — À faire AVANT tout dry-run ou live

#### Chantier 1 : Brancher le risk manager avancé dans main.py

**Problème** : `main.py` et `main_v2.py` importent `core.risk_manager` (legacy) au lieu de `risk.manager`. Le legacy a des thresholds FAUX : position max 10% (devrait être 5%), daily loss -5% (devrait être -2%), drawdown -15% (devrait être -10%), pas de sector check, pas d'audit log.

**Ce qu'il faut faire** :
1. Dans `main.py` et `main_v2.py` : remplacer `from core.risk_manager import RiskManager` par `from risk.manager import RiskManager`
2. Adapter les appels de méthodes à l'API du risk manager avancé (check_trade, calculate_stops, update_trailing_stop)
3. Passer les positions actuelles à `check_trade()` à chaque cycle
4. Appeler `calculate_stops(entry_price, direction, atr)` au lieu de hardcoder `price * 0.98`
5. Appeler `update_trailing_stop()` dans la boucle de monitoring des positions ouvertes
6. S'assurer que le sector check est actif (la sector map est dans risk/manager.py lignes 51-56)

**Fichiers concernés** : `main.py`, `main_v2.py`
**Fichiers à lire** : `risk/manager.py` (comprendre l'API), `core/risk_manager.py` (comprendre les appels actuels)
**Ne PAS modifier** : `risk/manager.py`
**Tests** : `python3 -m pytest tests/ -v` — adapter les tests si nécessaire

#### Chantier 2 : Sécuriser les ordres partiels

**Problème** : `_execute_buy()` soumet 3 ordres séquentiels (market buy + stop-loss + take-profit). Si le buy réussit mais le stop-loss échoue → position ouverte SANS protection. Le code catch l'exception et continue.

**Ce qu'il faut faire** :
1. Après le market buy, si le stop-loss échoue → annuler le buy (via `cancel_order`)
2. Si le take-profit échoue mais le stop-loss a réussi → loguer un warning mais continuer (au moins la protection est en place)
3. Retourner un statut explicite (True/False) au lieu de fail silencieux
4. Envoyer une alerte Telegram si un ordre partiel échoue

**Fichiers concernés** : `main.py` (lignes ~230-288), `main_v2.py`

#### Chantier 3 : Ajouter retry + exponential backoff sur l'API Alpaca

**Problème** : Un seul timeout ou erreur réseau kill le bot. Pas de retry, pas de gestion HTTP 429.

**Ce qu'il faut faire** :
1. Dans `core/alpaca_client.py`, ajouter un décorateur ou wrapper retry avec backoff exponentiel + jitter
2. Retry sur : Timeout, ConnectionError, HTTP 429, HTTP 503
3. Max 3 retries, backoff : 1s, 2s, 4s + jitter random(0, 1)
4. Sur HTTP 429 : parser le header `Retry-After` si présent
5. Après 3 échecs : raise l'exception (ne pas boucler indéfiniment)
6. Loguer chaque retry avec le numéro de tentative

**Fichiers concernés** : `core/alpaca_client.py`

#### Chantier 4 : Ajouter persistence d'état

**Problème** : Si le bot crash, il perd tout l'état interne. Pas de journal de trades sur disque, pas de state file.

**Ce qu'il faut faire** :
1. Créer un fichier `trades_journal.jsonl` (append-only, une ligne JSON par trade)
2. À chaque ouverture/fermeture de position, écrire un enregistrement avec : timestamp, symbol, side, qty, price, strategy, stop_loss, take_profit, reason
3. Au démarrage du bot, lire le journal et reconstruire l'état des positions ouvertes
4. Comparer avec les positions Alpaca (source de vérité) et loguer les écarts
5. Sauvegarder un snapshot d'état (`bot_state.json`) toutes les 5 minutes : cash, positions, daily_pnl, peak_equity

**Fichiers concernés** : `main.py`, `main_v2.py`
**Nouveau fichier** : `core/state_persistence.py`

### 🔴 HAUTE PRIORITÉ — Avant Phase 3

#### Chantier 5 : Intégrer Telegram dans le bot principal

**Problème** : `notifications/telegram_bot.py` est complet et fonctionnel (send_message, send_trade_alert, send_daily_summary, send_risk_alert) mais JAMAIS importé dans main.py/main_v2.py. Les trades passent en silence.

**Ce qu'il faut faire** :
1. Le notifier est async, le bot est sync. Deux options :
   - Option A : Wrapper sync autour du notifier async (avec `asyncio.run()`)
   - Option B : Refactorer le bot en async (plus gros chantier)
   → Recommandation : Option A pour l'instant
2. Envoyer une alerte à chaque : trade ouvert, trade fermé, stop-loss touché, risk halt, erreur critique
3. Envoyer un résumé quotidien (daily_summary) à 22h30 Paris

**Fichiers concernés** : `main.py`, `main_v2.py`, `notifications/telegram_bot.py`

#### Chantier 6 : Fixer le bug timezone dans MomentumStrategy

**Problème** : `strategies/momentum.py` hardcode UTC-4 (EDT). En hiver (nov-mars), le marché US trade en EST (UTC-5). Le bot rate des trades ou trade hors marché.

**Ce qu'il faut faire** :
1. Remplacer le hardcode UTC-4 par `zoneinfo.ZoneInfo("America/New_York")`
2. Utiliser `datetime.now(tz=ZoneInfo("America/New_York"))` pour vérifier les heures de marché
3. Le marché est ouvert de 9:30 à 16:00 Eastern Time (peu importe EDT/EST)

**Fichiers concernés** : `strategies/momentum.py`

#### Chantier 7 : Fixer la race condition sur le comptage de positions

**Problème** : `ThreadPoolExecutor(max_workers=5)` scanne en parallèle. Tous les threads lisent `len(positions)` en même temps → peuvent tous passer le check et ouvrir plus de 5 positions.

**Ce qu'il faut faire** :
1. Séparer le scan (parallèle) de l'exécution (séquentiel)
2. Le scan collecte les signaux, le bot principal les trie par force et exécute séquentiellement en vérifiant le compteur à chaque fois
3. Alternative : utiliser un `threading.Lock` sur le dict de positions

**Fichiers concernés** : `main.py`, `main_v2.py`

#### Chantier 8 : Graceful shutdown

**Problème** : `bot.stop()` met juste un flag à False. Pas d'annulation des ordres pendants, pas de cleanup.

**Ce qu'il faut faire** :
1. Intercepter SIGTERM et SIGINT proprement
2. Annuler tous les ordres Alpaca en attente (`get_orders(status='open')` + `cancel_order`)
3. Loguer l'état final (positions ouvertes, P&L)
4. Envoyer une alerte Telegram "Bot arrêté — X positions ouvertes"
5. Fermer la session HTTP proprement

**Fichiers concernés** : `main.py`, `main_v2.py`, `core/alpaca_client.py`

### 🟡 NETTOYAGE — Quand les bloquants sont réglés

#### Chantier 9 : Supprimer le code legacy

**Ce qu'il faut faire** :
1. Supprimer `core/risk_manager.py` (remplacé par risk/manager.py)
2. Supprimer `backtest/backtester.py` (remplacé par backtesting/engine.py)
3. Supprimer `strategies/_legacy_rsi_macd_kelly.py` (remplacé par strategies/mean_reversion.py + momentum.py)
4. Supprimer les imports de ces fichiers partout dans le codebase
5. Vérifier que les tests passent toujours

#### Chantier 10 : Virer ou implémenter le news analyzer

**Problème** : `analysis/news_analyzer.py` retourne toujours `{'overall_sentiment': 0.0}`. C'est du code mort qui complique la lecture.

**Options** :
- Option A : Supprimer le news analyzer et simplifier main_v2.py → revenir à main.py comme seul entry point
- Option B : Implémenter un vrai flux news (Finnhub, NewsAPI, etc.)
→ Recommandation : Option A sauf si Matthieu veut le garder.

#### Chantier 11 : Améliorer la validation

**Ce qu'il faut faire** :
1. Diversifier les symboles de backtest : ajouter des financials (JPM, BAC), industrials (CAT, DE), small caps
2. Étendre la période de validation de 2 ans à 5 ans si les données sont disponibles
3. Ajouter un check sur le drawdown max dans validate_hyperopt.py (pas seulement le Sharpe)

#### Chantier 12 : Ajouter un pre-commit hook pour bloquer les .env

**Ce qu'il faut faire** :
1. Créer `.git/hooks/pre-commit` qui bloque tout commit contenant des fichiers `.env`
2. Ou utiliser `pre-commit` (pip package) avec un hook `detect-private-key`
3. Scanner le git history pour vérifier que les anciennes clés sont bien révoquées

---

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
- [x] Phase 2c : Chantiers post-audit (1-8) — code fixes appliqués en local (2026-04-09)
- [x] Phase 2c-bis : Code review + fixes (B1-B9, H1-H8) — 11 commits atomiques landés sur branch (2026-04-09)
- [ ] Phase 3 : Dry-run 30 jours — Funding Arb (futures) + Breakout (spot)
- [ ] Phase 3b : Configurer clés API Binance testnet + lancer Freqtrade dry-run
- [ ] Phase 4 : Live progressif — commencer avec 500 USDT sur funding arb
- [ ] Phase 5 : Dashboard unifié actions + crypto + monitoring Telegram
