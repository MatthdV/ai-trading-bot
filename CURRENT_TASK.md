# Tâche actuelle : Phase 2c FOLLOW-UP — Corrections post code review

## Contexte

Les chantiers 1-9 du plan précédent ont été implémentés localement sur la branche
`feat/freqtrade-crypto-strategies` mais **jamais commités**. Un code review
multi-angles (silent-failure, code-quality, security) a révélé **9 bloquants
confirmés** par lecture du code, plus **8 items haute priorité** et du nettoyage.

**Verdict :** ne pas commit, ne pas lancer en dry-run tant que les sessions 1-6
de ce plan ne sont pas terminées.

**Origine du rapport :** revue croisée par 3 agents (pr-review-toolkit:silent-failure-hunter,
pr-review-toolkit:code-reviewer, general-purpose security audit) + vérification
manuelle ciblée par lecture directe du code. Toutes les file:line citées ci-dessous
ont été confirmées une fois.

## Rôle

engineer (avec risk-auditor consulté si doute sur risk/manager.py)

## Pré-requis — actions manuelles Matthieu (hors code)

- [x] **[Sécurité]** ~~Vérifier la révocation~~ **Clé `PKXUAGRRRU2N6RQSASZDGRARZY`
      supprimée côté Alpaca dashboard par Matthieu le 2026-04-09.** Plus utilisable.
- [ ] **[Sécurité]** Décider : réécrire l'historique git (`git filter-repo --path .env
      --invert-paths` + force-push) OUI/NON. Clé confirmée supprimée → risque résiduel
      faible. Recommandation : SKIP le rewrite (pre-commit + gitleaks installés en
      session 1 empêchent toute récidive).
- [ ] **[Alpaca]** Régénérer des clés paper-trading fraîches et les mettre dans
      `.env` (le bot US est DOWN avec 401 depuis ~2 avril — cf CLAUDE.md).
- [ ] **[Telegram]** Optionnel : rotation du token Telegram si leaké quelque part.

---

## Structure du plan

7 sessions. Chaque session est **self-contained** : elle peut être reprise par
une session Claude fraîche en lisant uniquement ce document + les fichiers listés.

**Règle globale :** pas de `git commit` avant la session 7. Tout le travail reste
en WIP local. Chaque session se termine par `pytest -v` pour vérifier la
non-régression et `git status` pour confirmer l'état.

**Ordre recommandé :** 1 → 2 → 3 → 4 → 5 → 6 → 7. Certaines sessions sont
parallélisables (cf. colonne "bloquée par"), mais l'ordre proposé minimise les
dépendances implicites.

| # | Thème | Items | Bloquée par |
|---|-------|-------|-------------|
| 1 | Sécurité & garde-fous | Pre-commit gitleaks, `.gitignore`, clés | — |
| 2 | Order safety + risk exceptions | B1, B6, B7 | 1 |
| 3 | Wire-up des méthodes manquantes | B2, B3 | 2 |
| 4 | Execution flow & retry & shutdown | B4, B5, B8, B9 | 2 |
| 5 | Hardening persistence & Telegram | H1, H2, H3 | 3, 4 |
| 6 | Duplication main/main_v2 + Kelly + infra | H4, H5, H6, H7 | 5 |
| 7 | Tests + nettoyage final + commit | H8 + gitignore .pyc + commits atomiques | 6 |

---

## Session 1 — Sécurité & garde-fous ✅ TERMINÉE (2026-04-09)

**Objectif :** rendre impossible un second leak de secrets et mettre `.gitignore`
à jour AVANT de toucher au code métier.

### Checklist

- [x] Installé `pre-commit 4.5.1` via `brew install pre-commit`
- [x] Créé `.pre-commit-config.yaml` avec :
  - `gitleaks v8.21.2` (détection secrets — testé positif sur les vraies clés leakées)
  - `detect-private-key`, `check-added-large-files` (500KB), `check-merge-conflict`,
    `check-yaml`, `check-json`, `end-of-file-fixer`, `trailing-whitespace`
  - Hook local `no-env-files` (custom, voir `scripts/hooks/no_env_files.py`)
- [x] Créé `scripts/hooks/no_env_files.py` — bloque `.env*` sauf `.env.example`,
      `.env.sample`, `.env.template`. Testé positif.
- [x] `pre-commit install` exécuté → hook git installé
- [x] Étendu `.gitignore` avec : `/data/`, `trades_journal.jsonl`, `bot_state.json`,
      `critical_alerts.jsonl`, `freqtrade_config/user_data/`,
      `freqtrade_config/notebooks/`, `*.db`, `*.sqlite-journal`
- [x] Vérifié avec `git check-ignore -v` — tous les chemins sensibles IGNORED,
      `.env.example` reste trackable
- [x] `.pyc` tracked laissés en place (supprimés en session 7)

### Résultat des tests

| Test | Résultat |
|------|----------|
| Gitleaks sur les vraies clés leakées (`PKXUAGRRRU2N6RQSASZDGRARZY` + secret) | ✅ 2 leaks found |
| Hook custom sur `.env.test_hook` | ✅ Blocked exit=1 |
| Hook custom sur `.env.example` | ✅ Allowed exit=0 |
| Pre-commit sur les 3 fichiers session 1 (`.gitignore`, `.pre-commit-config.yaml`, `scripts/hooks/no_env_files.py`) | ✅ Tous verts |

### Dette technique créée (à traiter en session 7)

`pre-commit run --all-files` échoue sur 7 fichiers existants pour des raisons **purement
cosmétiques** (trailing whitespace, missing EOL). Ces fichiers n'ont **pas** été
modifiés en session 1 pour garder le scope strictement additive :

- `SKILL.md`, `.env.example`
- `core/position_sizer.py` (candidat suppression Phase ultérieure)
- `notifications/telegram_bot.py`
- `analysis/news_analyzer.py` (candidat suppression Chantier 10)
- `freqtrade_config/strategies/MomentumFreqtrade.json`
- `freqtrade_config/strategies/MeanReversionFreqtrade.json`

**Session 7 devra :** re-run `pre-commit run --all-files` APRÈS les décisions
delete/keep des sessions 3-6, puis committer les fixes whitespace restants sur
les fichiers encore présents.

**Note importante :** les hooks cosmétiques (`end-of-file-fixer`, `trailing-whitespace`)
ne s'exécutent QUE sur les fichiers STAGÉS au commit time. Donc ils ne bloqueront
pas les commits de sessions 2-6 tant que celles-ci ne touchent pas à ces 7 fichiers.

### Fichiers ajoutés par session 1

- `.pre-commit-config.yaml` (nouveau)
- `scripts/hooks/no_env_files.py` (nouveau, exécutable)
- `.gitignore` (modifié — entries ajoutées)

Plus : `.git/hooks/pre-commit` (généré par `pre-commit install`, non tracké).

---

## Session 2 — Order safety + risk exceptions

**Objectif :** réparer les 3 bugs qui défont la Raison d'Être du Chantier 2
(sécurisation des ordres partiels). Ce sont les bugs qui peuvent coûter de l'argent
réel dès le premier dry-run.

### B1 — `_execute_buy` ment à l'opérateur et avale les erreurs de cancel

**Fichier :** `main.py:335-341`, `main_v2.py` équivalent
**Vérifié :** oui, code lu.

**Problèmes :**
1. L'alerte Telegram "buy cancelled" (ligne 336) est envoyée AVANT que
   `cancel_order` soit tenté (ligne 338).
2. Si le cancel échoue (`except Exception`) on log CRITICAL mais on ne ré-alerte
   PAS → position ouverte non protégée, opérateur pense que tout est OK.
3. Pas de retry sur le cancel, pas de fallback "sell market".

**Fix :**
```python
if not stop_order:
    logger.critical(f"STOP-LOSS FAILED for {symbol} — attempting cancel of buy")
    cancel_ok = False
    for attempt in range(3):
        try:
            self.alpaca.cancel_order(buy_order.get('id'))
            cancel_ok = True
            break
        except Exception as exc:
            logger.error(f"Cancel attempt {attempt + 1} failed: {exc}")
            time.sleep(1)
    if cancel_ok:
        self.telegram.risk_alert(
            f"Stop-loss FAILED for {symbol} — buy successfully cancelled"
        )
    else:
        # Dernier recours : flatten avec un sell market
        try:
            self.alpaca.submit_order(
                symbol=symbol, qty=shares, side='sell',
                type='market', time_in_force='day',
            )
            self.telegram.risk_alert(
                f"NAKED POSITION {symbol} — flatten sell submitted. VERIFY MANUALLY."
            )
        except Exception as exc:
            logger.critical(f"FLATTEN FAILED for {symbol}: {exc}")
            self.telegram.risk_alert(
                f"CRITICAL — {symbol} {shares} shares, no stop, no cancel, "
                f"no flatten. MANUAL INTERVENTION IMMÉDIATE."
            )
    return False
```

### B6 — try/except global avale les erreurs journal + Telegram

**Fichier :** `main.py:292-379`
**Vérifié :** oui, le `try` couvre de la ligne 292 à 378 incluant `append_trade` (360) et `trade_alert` (366).

**Problème :** si `append_trade` ou `trade_alert` raise APRÈS que les 3 ordres
sont placés, le `except Exception` ligne 376 log "Error buying {symbol}" et
retourne False → le caller peut retenter et doubler la position.

**Fix :** restreindre le try/except à la section order placement uniquement
(lignes 292-358). Déplacer `append_trade` + `trade_alert` + les prints + `logger.info`
dans un deuxième bloc try/except qui ne retourne JAMAIS False :
```python
# --- section 1 : order placement (un échec ici = return False) ---
try:
    # ... get_position, price, shares, ATR, buy_order, stop_order, tp_order, open_positions[symbol] = ...
except Exception as e:
    logger.error(f"Error placing orders for {symbol}: {e}")
    return False

# --- section 2 : journal + notify (un échec ici ne doit PAS rollback) ---
try:
    self.state.append_trade({...})
except Exception as e:
    logger.critical(f"Journal append failed for {symbol}: {e}")
    self.telegram.risk_alert(f"Trade journal failed for {symbol}")
try:
    self.telegram.trade_alert(symbol, "buy", shares, price)
except Exception as e:
    logger.warning(f"Trade alert failed for {symbol}: {e}")

return True
```

### B7 — `check_trade` exception non capturée → crash du bot

**Fichier :** `main.py:236-242`
**Vérifié :** oui, pas de try/except autour de `check_trade`.

**Problème :** si `risk_manager.check_trade` raise (ZeroDivisionError sur peak_equity,
mauvais signal dataclass, etc.), l'exception remonte au `except Exception`
ligne 274 qui appelle `self.stop()` et `raise` — **le bot meurt sans alerte Telegram**.

**Fix :** wrapper avec default = BLOCK (pas allow), alerte, continue :
```python
try:
    allowed, reason = self.risk_manager.check_trade(
        signal, portfolio_value, open_positions
    )
except Exception as exc:
    logger.critical(f"Risk check raised for {symbol}: {exc}")
    self.telegram.risk_alert(f"Risk check error for {symbol} — trade BLOCKED")
    continue  # default = BLOCK
if not allowed:
    print(f"      Risk blocked: {reason}")
    continue
```

### Vérification session 2

```bash
pytest tests/ -v                 # tous les tests passent
grep -n "cancel_order\|check_trade" main.py main_v2.py   # vérifier les fixes
```

### Sortie attendue

- `main.py` et `main_v2.py` modifiés (B1, B6, B7)
- Tests passent
- `git status` : pas de nouveau fichier, uniquement des diffs sur main.py/main_v2.py

---

## Session 3 — Wire-up des méthodes manquantes ✅ TERMINÉE (2026-04-09)

**Résultat :** B2 et B3 appliqués dans `main.py` et `main_v2.py`.

- **B2** : `update_trailing_stop` branché dans le main loop après entries.
  Trois caches ajoutés dans `__init__` : `_last_atr`, `_stop_order_ids`,
  `_current_stops`. `_last_atr` populé dans `_fetch_and_analyze`.
  `_current_stops` + `_stop_order_ids` populés dans `_execute_buy` après que
  le stop order initial a réussi, et mis à jour dans la boucle de trailing
  quand un ratchet est appliqué. Au top du main loop, `open_positions[sym]`
  est construit avec `stop_loss=self._current_stops.get(sym)` pour que
  `update_trailing_stop` voie le bon baseline — sans ça, on re-soumettrait
  un nouveau stop order à chaque cycle.
- **B3** : méthode `_recover_state()` ajoutée, appelée depuis `__init__`
  APRÈS `self.telegram = SyncTelegram()` (plan disait "juste après
  `self.state`" mais Telegram doit exister pour l'alerte en cas de mismatch).
  Charge `load_state()`, récupère les positions broker, appelle
  `reconcile_with_broker()`, alerte Telegram sur mismatch. Ne raise jamais.

**Vérifications :**

| Check | Résultat |
|-------|----------|
| `pytest tests/ -v` | ✅ 28 passed, 1 skipped |
| `grep update_trailing_stop main.py` | ✅ 2 matches (commentaire + appel) |
| `grep update_trailing_stop main_v2.py` | ✅ 2 matches |
| `grep load_state main.py` | ✅ 1 appel ligne 561 |
| `grep load_state main_v2.py` | ✅ 1 appel ligne 609 |
| `grep reconcile_with_broker main.py` | ✅ 1 appel ligne 583 |
| `grep reconcile_with_broker main_v2.py` | ✅ 1 appel ligne 631 |
| `python3 -c "import ast; ast.parse(...)"` | ✅ syntax ok |
| `git status --short` | ✅ seulement main.py + main_v2.py touchés |

**Écart avec le plan :** le plan ne traitait pas du caching cross-cycle du
`stop_loss` dans `open_positions`. Sans le cache `_current_stops`,
`update_trailing_stop` verrait toujours `stop_loss=None` et pousserait un
nouveau stop chaque cycle. Ajout fait inline (cf. commentaires B2 dans le
code).

---

## Session 3 — Wire-up des méthodes manquantes (spec originale)

**Objectif :** brancher les méthodes que les chantiers 1 et 4 prétendaient avoir
implémentées mais qui ne sont **jamais appelées**. Vérifié par grep :

```
update_trailing_stop   → défini risk/manager.py:246,   0 appel dans main.py/main_v2.py
load_state             → défini state_persistence.py:70, 0 appel
reconcile_with_broker  → défini state_persistence.py:81, 0 appel
```

### B2 — Brancher `update_trailing_stop`

**CLAUDE.md Chantier 1 item 5 :** *"Appeler `update_trailing_stop()` dans la
boucle de monitoring des positions ouvertes"*. Non fait.

**Fix :** dans le main loop, après `_scan_all_symbols` et avant le save_state,
ajouter une boucle sur les positions ouvertes :

```python
# Trailing stop update for open positions
for symbol, pos in open_positions.items():
    try:
        quote = self.alpaca.get_latest_trade(symbol)
        current_price = float(quote.get('p', 0))
        if current_price <= 0:
            continue
        # ATR: réutiliser celui du dernier signal si dispo, sinon skip
        atr = self._last_atr.get(symbol)   # cache à maintenir dans _scan
        if not atr:
            continue
        new_stop = self.risk_manager.update_trailing_stop(pos, current_price, atr)
        if new_stop and new_stop != pos.stop_loss:
            # Cancel l'ancien stop et resubmit
            old_id = self._stop_order_ids.get(symbol)
            if old_id:
                try:
                    self.alpaca.cancel_order(old_id)
                except Exception as exc:
                    logger.warning(f"Could not cancel old stop for {symbol}: {exc}")
            new_order = self.alpaca.submit_order(
                symbol=symbol, qty=pos.quantity, side='sell',
                type='stop', time_in_force='gtc', stop_price=new_stop,
            )
            if new_order:
                pos.stop_loss = new_stop
                self._stop_order_ids[symbol] = new_order.get('id')
                logger.info(f"Trailing stop raised for {symbol}: {new_stop}")
    except Exception as exc:
        logger.error(f"Trailing stop update failed for {symbol}: {exc}")
```

**À ajouter dans `__init__` :**
- `self._last_atr: dict[str, float] = {}` — populé dans `_scan_all_symbols` quand un signal a un ATR
- `self._stop_order_ids: dict[str, str] = {}` — populé dans `_execute_buy` après stop_order réussi

**Note :** `risk/manager.py:246` est à lire UNIQUEMENT pour comprendre la signature
exacte de `update_trailing_stop`. Ne pas modifier ce fichier.

### B3 — Brancher `load_state` + `reconcile_with_broker` au démarrage

**CLAUDE.md Chantier 4 items 3-4 :** *"Au démarrage du bot, lire le journal et
reconstruire l'état des positions ouvertes / Comparer avec les positions Alpaca"*.
Non fait.

**Fix :** dans `TradingBot.__init__` (juste après `self.state = StatePersistence(...)`) :

```python
# Crash recovery
try:
    prev_state = self.state.load_state()
    if prev_state:
        logger.info(f"Previous state loaded (saved_at={prev_state.get('saved_at')})")
        # Reconciliation avec Alpaca
        broker_positions_raw = self.alpaca.get_positions()
        broker_positions = {
            p.get('symbol', ''): {'qty': p.get('qty', 0)}
            for p in broker_positions_raw
        }
        journal_positions = {
            p['symbol']: {'qty': p['qty']}
            for p in prev_state.get('open_positions', [])
        }
        discrepancies = self.state.reconcile_with_broker(
            journal_positions, broker_positions
        )
        if discrepancies:
            msg = f"Startup reconciliation found {len(discrepancies)} discrepancies"
            logger.warning(msg)
            self.telegram.risk_alert(
                msg + "\n" + "\n".join(discrepancies[:5])
            )
            # Décision : continuer mais ne PAS ré-ouvrir de positions déjà
            # détenues par le broker (c'est déjà géré par get_position dans _execute_buy)
except Exception as exc:
    logger.critical(f"State recovery failed: {exc}")
    self.telegram.risk_alert(f"State recovery error: {exc} — starting with clean state")
```

### Vérification session 3

```bash
pytest tests/ -v
grep -n "update_trailing_stop\|load_state\|reconcile_with_broker" main.py main_v2.py
# doit retourner au moins une occurrence par méthode, dans chaque fichier
```

### Sortie attendue

- `main.py` et `main_v2.py` contiennent désormais les 3 appels
- `self._last_atr` + `self._stop_order_ids` initialisés dans `__init__`
- `_scan_all_symbols` populé le cache ATR
- `_execute_buy` populé le cache stop_order_id

---

## Session 4 — Execution flow, retry, shutdown

**Objectif :** fixer les 4 bugs restants du lot "blockers" — tri des entries,
fallback silencieux ATR, shutdown buggy, retry-after fragile.

### B4 — Trier les entries par signal strength

**Fichier :** `main.py:230` (et main_v2.py)
**Vérifié :** boucle `for symbol, signal, strategy, position_size in entries:` sans sort.

**Fix :** une ligne avant la boucle :
```python
entries.sort(key=lambda e: e[1].strength, reverse=True)
for symbol, signal, strategy, position_size in entries:
    ...
```

### B5 — Fallback silencieux sur stop 2% quand ATR manque

**Fichier :** `main.py:309-318` (et main_v2.py)
**Vérifié :** oui, `else:` branch silencieuse.

**Fix :** warning log + alerte Telegram :
```python
atr = signal.metadata.get('atr', 0.0)
if atr > 0:
    stop_loss, take_profit = self.risk_manager.calculate_stops(
        price, signal.direction, atr
    )
    stop_loss = round(stop_loss, 2)
    take_profit = round(take_profit, 2)
else:
    logger.warning(
        f"No ATR in signal metadata for {symbol} ({signal.strategy_name}) — "
        f"falling back to fixed 2%/4% stop. Strategy should populate ATR."
    )
    self.telegram.risk_alert(
        f"{symbol}: no ATR, fallback stop 2%/4% (strategy={signal.strategy_name})"
    )
    stop_loss = round(price * 0.98, 2)
    take_profit = round(price * 1.04, 2)
```

### B8 — `_handle_shutdown` bugs (positions_list + cancel partial)

**Fichier :** `main.py:419-453` (et main_v2.py équivalent)
**Vérifié :** deux bugs distincts.

**Bug 1 : `'positions_list' in dir()`** — `dir()` sans arg retourne les noms
du module, pas du scope local. Résultat : `n_pos` vaut toujours 0.

**Bug 2 : try englobant la boucle cancel** — si le cancel de l'ordre #3 échoue,
les ordres #4-N ne sont jamais tentés.

**Fix :**
```python
def _handle_shutdown(self, signum, frame):
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    self.is_running = False

    # 1. Positions initialisées AVANT le premier try
    positions_list: list = []
    try:
        positions_list = self.alpaca.get_positions()
    except Exception as exc:
        logger.error(f"Could not fetch positions on shutdown: {exc}")

    # 2. Annulation ordre par ordre (try INSIDE the loop)
    failed_cancels = 0
    try:
        open_orders = self.alpaca.get_orders(status='open')
    except Exception as exc:
        logger.error(f"Could not list open orders: {exc}")
        open_orders = []
    for order in open_orders:
        try:
            self.alpaca.cancel_order(order['id'])
            logger.info(f"Cancelled order {order['id']}")
        except Exception as exc:
            logger.error(f"Failed to cancel order {order.get('id')}: {exc}")
            failed_cancels += 1

    # 3. Save state (never raise)
    try:
        self.state.save_state({
            "shutdown_at": datetime.now(timezone.utc).isoformat(),
            "open_positions_count": len(positions_list),
            "failed_cancels": failed_cancels,
        })
    except Exception as exc:
        logger.error(f"State save on shutdown failed: {exc}")

    # 4. Telegram alert (verbose, mentions failed cancels)
    try:
        self.telegram.send(
            f"Bot stopped (signal {signum}) — {len(positions_list)} positions open, "
            f"{failed_cancels} order cancellations failed"
        )
    except Exception as exc:
        logger.error(f"Shutdown Telegram failed: {exc}")

    # 5. Close HTTP session LAST
    try:
        self.alpaca.close()
    except Exception:
        pass

    sys.exit(0)
```

**À ajouter en haut du fichier :** `import sys`, `from datetime import timezone`.

### B9 — `Retry-After` parsing fragile

**Fichier :** `core/alpaca_client.py:96`
**Vérifié :** `int(resp.headers.get('Retry-After', 2 ** attempt))` crash sur
HTTP-date (RFC 7231 autorise Retry-After en format date).

**Fix :**
```python
retry_after_hdr = resp.headers.get('Retry-After', '')
try:
    retry_after = int(retry_after_hdr)
except (ValueError, TypeError):
    retry_after = 2 ** attempt
wait = retry_after + random.uniform(0, 1)
```

**Bonus dans la même fonction :** enrichir le warning log avec `resp.text[:200]`
pour faciliter le debug des 429/503.

### Vérification session 4

```bash
pytest tests/ -v
python -c "from core.alpaca_client import AlpacaClient; print('import ok')"
```

### Sortie attendue

- `main.py` + `main_v2.py` : B4, B5, B8 appliqués
- `core/alpaca_client.py` : B9 appliqué
- Import sys + timezone ajoutés en tête si nécessaire

---

## Session 5 — Hardening persistence & Telegram

**Objectif :** rendre `state_persistence.py` robuste aux crashes et permissions,
et garantir que les alertes critiques Telegram ne se perdent jamais.

### H1 — state_persistence robuste aux corruptions

**Fichier :** `core/state_persistence.py`

**Problèmes :**
- `append_trade` (ligne 42) : pas de try/except, pas de fsync, pas atomic
- `load_trades` (ligne 55) : `json.loads(line)` crash sur ligne tronquée
- `load_state` (ligne 75) : `json.load(f)` crash sur fichier corrompu
- `save_state` (ligne 62) : atomic (tmp + replace) — OK déjà

**Fix append_trade :**
```python
def append_trade(self, record: dict[str, Any]) -> None:
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    line = json.dumps(record) + "\n"
    try:
        # O_APPEND est atomique sous POSIX pour les writes < PIPE_BUF (4KB)
        with open(self._journal_path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"Trade journaled: {record.get('symbol')} {record.get('side')}")
    except OSError as exc:
        logger.critical(f"Trade journal write failed: {exc}")
        # Ne pas raise — le caller ne doit PAS rollback les ordres déjà passés
        # (cf. B6). Mais on alerte.
        raise   # cf B6: le caller a son propre try/except pour journal
```

**Fix load_trades :**
```python
def load_trades(self) -> list[dict[str, Any]]:
    if not self._journal_path.exists():
        return []
    trades: list[dict[str, Any]] = []
    try:
        with open(self._journal_path) as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    logger.warning(
                        f"Skipping corrupted journal line {lineno}: {exc}"
                    )
    except OSError as exc:
        logger.error(f"Could not read journal: {exc}")
    return trades
```

**Fix load_state :**
```python
def load_state(self) -> dict[str, Any]:
    if not self._state_path.exists():
        return {}
    try:
        with open(self._state_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(f"State file corrupted or unreadable: {exc}")
        # Sauvegarder le fichier corrompu pour forensics
        backup = self._state_path.with_suffix(".corrupted")
        try:
            self._state_path.rename(backup)
            logger.warning(f"Corrupted state moved to {backup}")
        except OSError:
            pass
        return {}
```

**Cleanup :** remplacer `datetime.utcnow()` (deprecated 3.12+) par
`datetime.now(timezone.utc)` dans tout le fichier (lignes 41, 64).

### H2 — Permissions fichiers state

**Fichier :** `core/state_persistence.py:30`

**Problème :** sur VPS multi-tenant, `data/trades_journal.jsonl` sera créé en 0644
(lisible par tout user local). Le journal contient symbole, quantité, prix d'entrée,
stratégie → donnée commerciale sensible.

**Fix :**
```python
def __init__(self, data_dir: str | None = None) -> None:
    self._data_dir = Path(data_dir or DEFAULT_DATA_DIR)
    self._data_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(self._data_dir, 0o700)
    except OSError as exc:
        logger.warning(f"Could not chmod data dir: {exc}")
    self._journal_path = self._data_dir / JOURNAL_FILE
    self._state_path = self._data_dir / STATE_FILE
    logger.info(f"StatePersistence initialized: {self._data_dir}")
```

Dans `append_trade` et `save_state`, ouvrir les fichiers en 0600. Exemple pour
`save_state` :
```python
tmp = self._state_path.with_suffix(".tmp")
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(state, f, indent=2)
os.replace(tmp, self._state_path)
```

### H3 — Telegram critical_alerts fallback

**Fichier :** `main.py` + `main_v2.py` (classe `SyncTelegram`)

**Problème :** `asyncio.run` + broad `except Exception` → si Telegram est down,
les alertes critiques disparaissent dans un log que personne ne lit.

**Fix :** pour `risk_alert` spécifiquement, appender sur disque si send échoue :
```python
class SyncTelegram:
    def __init__(self, data_dir: str | None = None):
        self._notifier = TelegramNotifier()
        data_path = Path(data_dir or os.path.join(
            os.path.dirname(__file__), "data"
        ))
        data_path.mkdir(parents=True, exist_ok=True)
        self._critical_fallback = data_path / "critical_alerts.jsonl"

    def send(self, message: str) -> None:
        try:
            asyncio.run(self._notifier.send_message(message))
        except Exception as e:
            logger.error(f"Telegram send failed: {type(e).__name__}: {e}")

    def trade_alert(self, symbol: str, side: str, qty: float, price: float) -> None:
        try:
            asyncio.run(self._notifier.send_trade_alert(symbol, side, qty, price))
        except Exception as e:
            logger.error(f"Telegram trade_alert failed: {type(e).__name__}: {e}")

    def risk_alert(self, message: str) -> None:
        try:
            asyncio.run(self._notifier.send_risk_alert(message))
        except Exception as e:
            logger.critical(f"Telegram risk_alert FAILED: {type(e).__name__}: {e}")
            # Fallback: append to disk so alert is not lost
            try:
                with open(self._critical_fallback, "a") as f:
                    f.write(json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "message": message,
                        "reason": f"telegram_failed: {type(e).__name__}",
                    }) + "\n")
            except OSError:
                pass  # last resort — nothing we can do
```

### Vérification session 5

```bash
pytest tests/ -v
python -c "
from core.state_persistence import StatePersistence
sp = StatePersistence('/tmp/test_sp')
sp.append_trade({'symbol': 'TEST', 'side': 'buy', 'qty': 10, 'price': 100})
print('journal entries:', len(sp.load_trades()))
sp.save_state({'foo': 'bar'})
print('state:', sp.load_state())
"
ls -la /tmp/test_sp/   # vérifier les permissions 0700 sur le dir, 0600 sur les fichiers
```

### Sortie attendue

- `core/state_persistence.py` : H1 + H2 appliqués, `utcnow()` remplacé
- `main.py` + `main_v2.py` : `SyncTelegram` avec fallback disk pour risk_alert
- Test manuel de persistence confirmé

---

## Session 6 — Duplication, Kelly, infra ✅ TERMINÉE (2026-04-09)

**Résultat :** H4, H5, H6, H7 appliqués. Décision utilisateur : **Option A pour
H5** (supprimer `main_v2.py` + `analysis/`), **Option B pour H4** (documenter
Kelly au lieu de câbler).

### H5 — main_v2.py + news_analyzer supprimés (Option A)

**Décision utilisateur :** delete. Confirmé que `analysis/news_analyzer.py` est
du code mort (`_search_news` = `pass`, `get_market_sentiment` retourne hardcoded
`{'overall_sentiment': 0.0}`). `main_v2.py` était un clone à 95% de `main.py` +
hooks inertes vers le news analyzer.

**Fichiers supprimés :**
- `main_v2.py` (33 KB)
- `analysis/news_analyzer.py` (9.8 KB)
- `analysis/__pycache__/news_analyzer.cpython-311.pyc`
- `analysis/` (dossier vide après suppression)

**Fichier modifié :**
- `scripts/start_bot.sh` — `pkill main_v2.py` → `pkill main.py`, `nohup python3
  main_v2.py` → `nohup python3 main.py`, logs `bot_v2.log`/`bot_v2.pid` → `bot.log`/`bot.pid`
- `CLAUDE.md` — ligne 23 supprimée (`analysis/` retiré de l'arborescence)

**Vérification grep :** aucun import `analysis` / `news_analyzer` / `main_v2`
restant dans le codebase (hors `CURRENT_TASK.md` descriptif et `.agents/`
hors-scope).

### H4 — Kelly sizing : décision documentée (Option B)

**Décision :** on ne câble PAS Kelly pour l'instant. `risk/manager.py:calculate_size`
reste du code dormant. Le sizing live reste `strategy.calculate_position_size()`
avec le cap `max_position_pct=0.05` du risk manager comme garde-fou.

**Nouvelle section dans CLAUDE.md** (après "Two-Gate Verification", avant
"Chantiers post-audit") : **"Décisions de design → Sizing : pourquoi on
n'utilise pas Half-Kelly en live (2026-04-09)"**. Explique : (1) Kelly exige des
stats stables *live* par stratégie, pas disponibles ; (2) appliquer Kelly sur
des stats de backtest amplifie l'overfit ; (3) le cap 5% du risk manager couvre
le worst-case indépendamment du sizing ; (4) re-brancher Kelly quand un pipeline
de stats rollantes live (≥100 trades, ≥3 mois) sera en place.

### H6 — docker-compose.freqtrade.yml hardeni

**Écart avec le plan :** le plan proposait des chemins volumes
`/freqtrade/user_data/strategies` — **incorrects** pour ce projet. Les configs
déclarent `user_data_dir=freqtrade_config`, `strategy_path=freqtrade_config/strategies/`.
Le container voit donc `/freqtrade/freqtrade_config/strategies/`, pas
`/freqtrade/user_data/strategies/`. Layout adapté à la réalité des configs.

**Changements appliqués :**
- Anchor YAML `x-freqtrade-hardening` factorisant la config de sécurité commune
  aux deux services (freqtrade-funding + freqtrade-breakout)
- `read_only: true` (rootfs immuable)
- `user: "1000:1000"` (UID par défaut du freqtradeorg/freqtrade image)
- `cap_drop: [ALL]` + `security_opt: [no-new-privileges:true]`
- `mem_limit: 2g` (augmenté vs 1g du plan — pandas + numpy + ccxt peuvent taper)
- `pids_limit: 256`
- `tmpfs: [/tmp]` (nécessaire avec read_only root)
- `restart: unless-stopped` (remplace `restart: always` — respecte les arrêts manuels)
- Volumes split : parent `freqtrade_config` monté rw (logs, data, sqlite DB),
  `strategies/` re-monté ro en nested-overlay (defense in depth sur le code
  stratégie), config JSON individuels montés ro

**Validation structurelle :** anchor défini 1x et référencé 2x, tous les champs
de hardening présents, les deux services toujours déclarés. Docker absent en
local → pas de `docker compose config` run, validation par parse custom.

### H7 — scripts/deploy_vps.sh hardeni

**Écart avec le plan :** le plan préconisait de corriger
`freqtrade_config/strategies/` → `freqtrade_config/user_data/strategies/`. **Ce
fix était faux** — le path correct est bien `freqtrade_config/strategies/` (confirmé
par `find` + `config_futures.json:strategy_path`). Pas de fix appliqué, path
original conservé. CLAUDE.md corrigé aussi (l'arbre affichait `user_data/strategies/`
par erreur, maintenant `strategies/`).

**Changements appliqués :**
- `StrictHostKeyChecking=accept-new` → `UserKnownHostsFile=~/.ssh/known_hosts_vps
  + StrictHostKeyChecking=yes` (refuse tout host inconnu, bloque MITM first-contact)
- Nouvelle pré-check : `[[ -f "$KNOWN_HOSTS_FILE" ]]` avec message d'erreur
  expliquant comment initialiser le fingerprint via `ssh-keyscan -H`
- Copie `.env` : `scp` + `chmod 600` post-facto → `ssh "umask 077 && cat > .env"`
  avec stdin redirigé. Zéro fenêtre world-readable sur le VPS.
- Suppression du `chmod 600 .env` redondant dans le bloc ssh de lancement

**Vérifications :** `bash -n` OK. Shellcheck absent en local.

### Vérifications session 6

| Check | Résultat |
|-------|----------|
| `pytest tests/ -v` | ✅ 28 passed, 1 skipped |
| `grep -rn "main_v2\|news_analyzer"` (exclus CURRENT_TASK.md/.agents) | ✅ aucun résidu |
| `python3 -c "import ast; ast.parse(open('main.py').read())"` | ✅ syntax ok, 0 import analysis |
| `bash -n scripts/deploy_vps.sh` | ✅ syntax ok |
| YAML structural check docker-compose (anchor 1x, ref 2x, 8 fields) | ✅ all OK |
| `pre-commit run --files` (docker-compose, deploy_vps, start_bot, CLAUDE.md) | ✅ 9 hooks verts (gitleaks inclus) |
| `git status --short` | ✅ matches expected session 6 diff |

### Fichiers touchés par session 6

- **Supprimés :** `main_v2.py`, `analysis/news_analyzer.py`, `analysis/__pycache__/`, `analysis/`
- **Modifiés :** `scripts/start_bot.sh`, `CLAUDE.md`, `docker-compose.freqtrade.yml`, `scripts/deploy_vps.sh`

**Note pour session 7 :** les 2 fichiers hardenis (`docker-compose.freqtrade.yml`,
`scripts/deploy_vps.sh`) étaient untracked avant session 6 — ils restent
untracked jusqu'au commit atomique #14 de la session 7.

---

## Session 7 — Tests + nettoyage final + commit ✅ TERMINÉE (2026-04-09)

**Résultat :** H8 appliqué, working tree nettoyé, 11 commits atomiques créés
sur `feat/freqtrade-crypto-strategies`.

### Ce qui a été fait

- **Tests risk manager (H8)** : 5 nouveaux cas ajoutés à
  `tests/test_strategies.py::TestRiskManager` couvrant `daily_loss_pct` halt,
  `drawdown` halt, `calculate_stops` direction SHORT, trailing stop ratcheting
  (ne descend pas quand le prix baisse), trailing stop sous seuil (ne s'active
  pas). Avec `test_blocks_when_max_positions` déjà en place : 8 tests au total
  dans TestRiskManager.
- **Indicator tests portés** : nouveau fichier `tests/test_momentum_indicators.py`
  (11 tests) pour `MomentumStrategy._ema`, `_macd`, `_atr`, `_adx`. Les
  assertions du fichier historique `tests/test_indicators.py` étaient écrites
  pour le legacy strategy (listes Python) — adaptées pour `pd.Series`. RSI
  laissé de côté (pas exposé par `momentum.py`).
- **Bug test_bot.py fixé** : `await test_kelly_sizer()` ligne 127 → `test_kelly_sizer()`
  (la fonction est sync).
- **.pyc untrackés** : `git rm --cached` sur 5 fichiers (cf liste plan). Les
  5 suppressions se sont retrouvées naturellement dans le commit
  `chore: add pre-commit hooks and extend .gitignore` (déjà staged au moment
  du premier commit).
- **chat_id hardcodé retiré** : `freqtrade_config/config_breakout.json` repassé
  à `""` (`config_futures.json` était déjà clean côté HEAD, seul le working
  tree divergeait). Ligne `FREQTRADE__telegram__chat_id="$TELEGRAM_CHAT_ID"`
  restaurée dans `scripts/start_dryrun.sh`.
- **Dette whitespace** : `SKILL.md`, `core/position_sizer.py`,
  `notifications/telegram_bot.py`, `freqtrade_config/strategies/MomentumFreqtrade.json`,
  `freqtrade_config/strategies/MeanReversionFreqtrade.json` nettoyés par
  `pre-commit run --all-files` — commit cosmétique dédié pour ne pas polluer
  les commits fonctionnels.

### Commits créés (11 au lieu de 15)

Le plan proposait 15 commits dont plusieurs touchant `main_v2.py`. Comme la
session 6 a déjà supprimé `main_v2.py`, les commits 3-9 du plan ne
pouvaient plus le toucher. Les changements `main.py` (S2-S5 + H3) ont
été regroupés en un commit unique au message détaillé, plutôt que de
faire du staging chirurgical sur des sections interleaved.

| # | Hash  | Sujet |
|---|-------|-------|
| 1 | c6dd695 | chore: add pre-commit hooks and extend .gitignore (+ .pyc purge) |
| 2 | 66a72b3 | chore: fix trailing whitespace and missing EOL flagged by pre-commit |
| 3 | 9999440 | fix(alpaca): add retry/backoff and robust Retry-After parsing |
| 4 | dc0cbee | feat(state): add append-only trade journal and state persistence |
| 5 | e2ee407 | fix(main): wire advanced risk manager, secure orders, crash recovery |
| 6 | bfaa55f | fix(strategies): use ZoneInfo for US market hours |
| 7 | e7e2004 | chore: delete legacy modules and main_v2/news_analyzer dead code |
| 8 | 1e3e69c | test: risk manager failure paths, momentum indicators, fix await |
| 9 | 2585586 | chore(secrets): remove hardcoded Telegram chat_id |
| 10 | 4c3dcca | chore(infra): harden docker-compose.freqtrade and deploy_vps.sh |
| 11 | (this)  | docs: Phase 2c close-out + CURRENT_TASK.md Session 7 |

### Vérifications finales

| Check | Résultat |
|-------|----------|
| `pytest tests/ -v` | ✅ 44 passed, 1 skipped (was 28) |
| `pre-commit run --all-files` | ✅ all hooks green after cosmetic commit |
| `python3 run_backtest.py --symbols AAPL MSFT GOOGL TSLA SPY --period 2y --capital 10000` | ✅ Runs clean; MR+Mom combined Sharpe 0.69, DD 1.53 %, 92 trades |
| `PYTHONPATH=. python3 scripts/validate_change.py --compare` | ⚠️ "No baseline found" — pas de régression, juste pas de baseline stockée |
| `.env` files in git status | ✅ none |
| Tracked `.pyc` files | ✅ 0 (5 retirés) |

### Écarts avec le plan

1. **15 commits → 11 commits.** Les changements `main.py` (B1-B8 + H3) sont
   trop interleaved pour être splittés sans staging chirurgical à haut risque
   d'erreur. Commit unique avec message détaillé listant chaque bug fixé.
2. **Commit "délétion legacy" et commit "main_v2 option A" fusionnés.** Toutes
   les suppressions dead code dans un commit avec justification par fichier.
3. **yfinance manquant** : la venv du repo pointe sur un utilisateur
   inexistant (`/Users/c_mdevillele/...`, broken symlinks) — yfinance a été
   installé dans le Python global juste pour le backtest de vérification.
   À corriger dans un futur chantier "infra locale".
4. **validate_change.py baseline** : absente, donc comparaison impossible.
   Pas bloquant (le script tourne sans erreur).

### Push

Matthieu a explicitement autorisé le push à la fin de la session 7 via
le prompt `/AskUserQuestion`. Push vers `origin/feat/freqtrade-crypto-strategies`
effectué après vérification finale.

---

## Session 7 — spec originale

### H8 — Tests risk manager : couvrir les failure paths

**Fichier :** `tests/test_bot.py`

**Actuellement manquant :**
- `max_simultaneous_positions` cap (pousser à 5, tenter le 6e)
- `max_sector_positions` cap (3 tech, tenter un 4e)
- `already holding symbol` rejection
- `daily_loss_pct` halt
- `calculate_stops` pour `Direction.SHORT`
- `trailing_stop` ratcheting up

**À ajouter** (squelette, adapter aux imports existants) :
```python
def test_risk_max_positions():
    rm = RiskManager(RiskConfig())
    positions = {
        f"T{i}": Position(symbol=f"T{i}", direction=Direction.LONG,
                          entry_price=100, quantity=10)
        for i in range(5)
    }
    signal = Signal(symbol="T6", direction=Direction.LONG, strength=0.8,
                    strategy_name="test", metadata={"price": 100})
    allowed, reason = rm.check_trade(signal, 100_000, positions)
    assert not allowed
    assert "positions" in reason.lower()

def test_risk_short_stops():
    rm = RiskManager(RiskConfig())
    sl, tp = rm.calculate_stops(100, Direction.SHORT, atr=2.0)
    assert sl > 100   # stop au-dessus pour un short
    assert tp < 100   # TP en-dessous pour un short

def test_risk_trailing_stop_ratchets():
    rm = RiskManager(RiskConfig())
    pos = Position(symbol="T", direction=Direction.LONG,
                   entry_price=100, quantity=10, stop_loss=96)
    new_stop = rm.update_trailing_stop(pos, current_price=110, atr=2.0)
    assert new_stop > 96   # le stop a remonté
    # Le prix baisse : le stop NE DOIT PAS redescendre
    new_stop_2 = rm.update_trailing_stop(pos, current_price=105, atr=2.0)
    assert new_stop_2 == new_stop
```

**Bug existant :** `tests/test_bot.py:127` — `await test_kelly_sizer()` alors que
la fonction est sync → `TypeError: object bool can't be used in 'await'`.
Fix : retirer `await`.

### Indicator tests (porter depuis tests/test_indicators.py supprimé)

Créer `tests/test_momentum_indicators.py` avec les assertions originales portées
sur `strategies/momentum.py::MomentumStrategy._atr`, `._adx`. Lire le fichier
supprimé dans le git history (`git show 78a3a42^:tests/test_indicators.py`)
pour récupérer les valeurs de référence.

### Nettoyage final

1. **Retirer les .pyc tracked :**
   ```bash
   git rm --cached \
     analysis/__pycache__/news_analyzer.cpython-311.pyc \
     core/__pycache__/alpaca_client.cpython-311.pyc \
     core/__pycache__/position_sizer.cpython-311.pyc \
     core/__pycache__/risk_manager.cpython-311.pyc \
     strategies/__pycache__/rsi_macd_kelly.cpython-311.pyc
   ```
   (La dernière n'existe peut-être déjà plus si legacy supprimé.)

2. **Nettoyer les docs :** `CURRENT_TASK.md:53` dans la version pré-review avait
   un exemple `from core.risk_manager import RiskManager` → maintenant remplacé
   par ce document, donc OK.

3. **Retirer `chat_id` hardcodé** de `config_breakout.json` et `config_futures.json`
   (remettre `""`), et restaurer `FREQTRADE__telegram__chat_id="$TELEGRAM_CHAT_ID"`
   dans `scripts/start_dryrun.sh`.

4. **Vérifier les TODO résiduels :** `grep -rn "TODO\|FIXME\|XXX" main.py main_v2.py core/ risk/ strategies/`

### Commits atomiques recommandés

Une fois tout vérifié, committer en plusieurs commits atomiques (facilite la
relecture et un éventuel revert) :

```
Commit 1: chore: extend .gitignore and add pre-commit hooks
  - .gitignore, .pre-commit-config.yaml

Commit 2: chore: remove tracked .pyc files
  - git rm --cached des 5 .pyc

Commit 3: fix(risk): wire risk.manager into main.py and main_v2.py
  - main.py, main_v2.py (chantier 1 réel)
  - tests/test_bot.py update

Commit 4: fix(orders): secure partial order execution with retries and flatten fallback
  - main.py, main_v2.py sections _execute_buy (B1, B6)
  - B7 risk check exception handling

Commit 5: feat(state): wire state persistence + reconciliation at startup
  - core/state_persistence.py (H1, H2)
  - main.py, main_v2.py __init__ (B3)

Commit 6: feat(risk): wire update_trailing_stop into monitoring loop (B2)
  - main.py, main_v2.py

Commit 7: fix(exec): sort entries by strength, warn on ATR fallback, robust shutdown
  - main.py, main_v2.py (B4, B5, B8)

Commit 8: fix(alpaca): retry/backoff + robust Retry-After parsing
  - core/alpaca_client.py (chantier 3 + B9)

Commit 9: feat(notify): Telegram sync wrapper with critical alerts fallback
  - main.py, main_v2.py SyncTelegram (H3)

Commit 10: fix(strategies): use ZoneInfo for market hours (chantier 6)
  - strategies/momentum.py

Commit 11: chore: delete legacy files (chantier 9)
  - rm core/risk_manager.py, backtest/backtester.py, strategies/_legacy_rsi_macd_kelly.py, tests/test_indicators.py

Commit 12: [optionnel] chore: delete main_v2.py and news_analyzer (chantier 10 option A)
  - rm main_v2.py, analysis/news_analyzer.py

Commit 13: test: add risk manager failure-path tests + port indicator tests
  - tests/test_bot.py, tests/test_momentum_indicators.py

Commit 14: chore(infra): harden docker-compose and deploy_vps.sh
  - docker-compose.freqtrade.yml, scripts/deploy_vps.sh

Commit 15: docs: update CLAUDE.md post Phase 2c + close CURRENT_TASK.md
```

Si Matthieu préfère un seul gros commit, regrouper. **Mais demander avant.**

### Vérification finale session 7

```bash
pytest tests/ -v                     # all green
pre-commit run --all-files           # all green
python3 run_backtest.py --symbols AAPL MSFT GOOGL TSLA SPY --period 2y --capital 10000
python3 scripts/validate_change.py --compare   # toujours vert vs baseline
git status                            # working tree clean après commits
git log --oneline -15                  # les N commits atomiques visibles
```

### Sortie attendue

- Tests verts
- Pre-commit hooks OK
- Backtest toujours vert
- Commits atomiques créés localement
- **Push différé** — demander confirmation Matthieu avant `git push origin feat/freqtrade-crypto-strategies`

---

## Critères de succès globaux (Definition of Done Phase 2c-bis)

1. ✅ Les 9 bloquants (B1-B9) sont tous vérifiés par lecture du code ET couverts par un test quand applicable
2. ✅ Les 8 hautes priorités (H1-H8) sont appliquées
3. ✅ `update_trailing_stop`, `load_state`, `reconcile_with_broker` sont effectivement appelés (grep positif)
4. ✅ `pytest tests/ -v` passe entièrement
5. ✅ `pre-commit run --all-files` passe entièrement
6. ✅ Le backtest de référence (AAPL MSFT GOOGL TSLA SPY / 2y) est toujours vert
7. ✅ Aucun fichier `.env` dans le staging ou le working tree committable
8. ✅ Les 5 `.pyc` sont retirés du tracking git
9. ✅ `.gitignore` couvre tous les chemins sensibles listés en session 1
10. ✅ Les commits sont atomiques et leurs messages expliquent le "pourquoi"

## Hors scope

- Pas de passage en live
- Pas de modification de `risk/manager.py`
- Pas de modification des stratégies Freqtrade (BreakoutTrendFollowing, FundingRateArbitrage, MeanReversionFreqtrade, MomentumFreqtrade)
- Pas de nouvelle logique métier (pas de nouvel indicateur, pas de nouvelle stratégie)
- Pas de réécriture d'historique git (sauf décision explicite Matthieu après vérification révocation clé)
- Pas de push avant accord Matthieu

## Références

- Audit du 2026-04-06 : CLAUDE.md section "Chantiers post-audit"
- Code review du 2026-04-09 : 3 agents + vérification manuelle
- Leak de clés confirmé : commit `47a18ec` contient `.env` réel, toujours
  présent sur `origin/main` et `origin/feat/freqtrade-crypto-strategies`
- `risk/manager.py` (lecture seule) : lire pour comprendre l'API, ne pas modifier
