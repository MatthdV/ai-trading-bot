#!/usr/bin/env bash
# Démarre les deux bots Freqtrade en dry-run dans des terminaux/processes séparés.
# Usage : bash scripts/start_dryrun.sh [futures|breakout|both]
#
# Prérequis : .env configuré avec BINANCE_TESTNET_KEY, BINANCE_TESTNET_SECRET,
#             TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
FREQTRADE="$ROOT_DIR/.venv/bin/freqtrade"
LOGS_DIR="$ROOT_DIR/freqtrade_config/logs"

# Charger les variables d'environnement
if [ -f "$ROOT_DIR/.env" ]; then
    set -a
    source "$ROOT_DIR/.env"
    set +a
else
    echo "❌ .env introuvable. Copie .env.example et remplis les clés."
    exit 1
fi

mkdir -p "$LOGS_DIR"

# Freqtrade ne substitue pas ${VAR} dans les JSON.
# On passe les secrets via le format FREQTRADE__<section>__<key> qui override le config.
export FREQTRADE__exchange__key="$BINANCE_TESTNET_KEY"
export FREQTRADE__exchange__secret="$BINANCE_TESTNET_SECRET"
export FREQTRADE__telegram__token="$TELEGRAM_BOT_TOKEN"
# chat_id doit être une string JSON (guillemets littéraux pour éviter la conversion en int)
export FREQTRADE__telegram__chat_id="\"$TELEGRAM_CHAT_ID\""

MODE="${1:-both}"

start_futures() {
    echo "▶ Démarrage FundingRateArbitrage (futures dry-run)..."
    "$FREQTRADE" trade \
        --strategy FundingRateArbitrage \
        --config "$ROOT_DIR/freqtrade_config/config_futures.json" \
        --db-url "sqlite:///$ROOT_DIR/freqtrade_config/tradesv3_futures_dryrun.sqlite" \
        --logfile "$LOGS_DIR/freqtrade_futures.log" \
        >> "$LOGS_DIR/freqtrade_futures_stdout.log" 2>&1 &
    FUTURES_PID=$!
    echo "  PID futures : $FUTURES_PID"
    echo "$FUTURES_PID" > "$ROOT_DIR/freqtrade_config/futures.pid"
}

start_breakout() {
    echo "▶ Démarrage BreakoutTrendFollowing (spot dry-run)..."
    "$FREQTRADE" trade \
        --strategy BreakoutTrendFollowing \
        --config "$ROOT_DIR/freqtrade_config/config_breakout.json" \
        --db-url "sqlite:///$ROOT_DIR/freqtrade_config/tradesv3.sqlite" \
        --logfile "$LOGS_DIR/freqtrade.log" \
        >> "$LOGS_DIR/freqtrade_breakout_stdout.log" 2>&1 &
    BREAKOUT_PID=$!
    echo "  PID breakout : $BREAKOUT_PID"
    echo "$BREAKOUT_PID" > "$ROOT_DIR/freqtrade_config/breakout.pid"
}

case "$MODE" in
    futures) start_futures ;;
    breakout) start_breakout ;;
    both)
        start_futures
        sleep 2
        start_breakout
        ;;
    *)
        echo "Usage: $0 [futures|breakout|both]"
        exit 1
        ;;
esac

echo ""
echo "✅ Bots démarrés."
echo ""
echo "Commandes utiles :"
echo "  Logs futures  : tail -f $LOGS_DIR/freqtrade_futures.log"
echo "  Logs breakout : tail -f $LOGS_DIR/freqtrade.log"
echo "  Monitoring    : python3 $SCRIPT_DIR/monitor_dryrun.py"
echo ""
echo "Arrêter les bots :"
echo "  kill \$(cat freqtrade_config/futures.pid)"
echo "  kill \$(cat freqtrade_config/breakout.pid)"
