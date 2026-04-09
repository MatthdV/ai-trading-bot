#!/usr/bin/env bash
# Deploy Freqtrade bots to VPS Hostinger
# Usage: bash scripts/deploy_vps.sh

set -euo pipefail

VPS_HOST="187.124.26.75"
VPS_USER="matthieu"
VPS_DIR="/home/matthieu/ai-trading-bot"
COMPOSE_FILE="docker-compose.freqtrade.yml"
SSH_KEY="$HOME/.ssh/id_ed25519_vps"
# Host key pinning: the fingerprint must be pre-populated once via
#   ssh-keyscan -H 187.124.26.75 >> ~/.ssh/known_hosts_vps
# Then we refuse any unknown host (StrictHostKeyChecking=yes). This blocks
# MITM on first-contact, which `accept-new` silently allows.
KNOWN_HOSTS_FILE="$HOME/.ssh/known_hosts_vps"
SSH_OPTS="-i $SSH_KEY -o UserKnownHostsFile=$KNOWN_HOSTS_FILE -o StrictHostKeyChecking=yes"

# ─── Couleurs ──────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ─── Vérifications locales ─────────────────────────────────────────────────
cd "$(dirname "$0")/.."

[[ -f "$COMPOSE_FILE" ]]                                      || error "Fichier $COMPOSE_FILE introuvable (lance depuis la racine du projet)"
[[ -f ".env" ]]                                               || error "Fichier .env manquant — crée-le avec les clés Binance production"
[[ -f "freqtrade_config/config_futures.json" ]]               || error "config_futures.json manquant"
[[ -f "freqtrade_config/config_breakout.json" ]]              || error "config_breakout.json manquant"
[[ -f "freqtrade_config/strategies/FundingRateArbitrage.py" ]] || error "Stratégie FundingRateArbitrage.py manquante"
[[ -f "freqtrade_config/strategies/BreakoutTrendFollowing.py" ]] || error "Stratégie BreakoutTrendFollowing.py manquante"
[[ -f "$KNOWN_HOSTS_FILE" ]] || error "known_hosts pinné introuvable : $KNOWN_HOSTS_FILE
Pour initialiser (à faire UNE fois, après vérification out-of-band du fingerprint) :
  ssh-keyscan -H $VPS_HOST >> $KNOWN_HOSTS_FILE"

# Vérifie que le .env contient les bonnes clés (pas les clés testnet)
if grep -q "BINANCE_TESTNET_KEY" .env && ! grep -q "BINANCE_API_KEY" .env; then
    warn ".env contient BINANCE_TESTNET_KEY mais pas BINANCE_API_KEY"
    warn "Le docker-compose attend BINANCE_API_KEY et BINANCE_API_SECRET"
    warn "Mets à jour le .env avant de déployer en production"
    read -rp "Continuer quand même ? (y/N) " confirm
    [[ "$confirm" =~ ^[yY]$ ]] || exit 0
fi

info "Déploiement vers $VPS_USER@$VPS_HOST:$VPS_DIR"

# ─── Création des répertoires sur le VPS ───────────────────────────────────
info "Création des répertoires distants..."
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "
    mkdir -p $VPS_DIR/freqtrade_config/strategies
    mkdir -p $VPS_DIR/freqtrade_config/logs
    mkdir -p $VPS_DIR/freqtrade_config/data
    mkdir -p $VPS_DIR/freqtrade_config/user_data/hyperopts
"

# ─── Copie des fichiers ────────────────────────────────────────────────────
info "Copie du docker-compose..."
scp $SSH_OPTS "$COMPOSE_FILE" "$VPS_USER@$VPS_HOST:$VPS_DIR/"

info "Copie du .env (atomique, créé en 0600 — zéro fenêtre world-readable)..."
# scp crée le fichier en umask du shell distant (typiquement 0644), puis on
# aurait dû chmod après → race. Utiliser un ssh pipe + umask 077 garantit que
# le fichier EST CRÉÉ directement en 0600.
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" \
    "umask 077 && mkdir -p $VPS_DIR && cat > $VPS_DIR/.env" < .env

info "Copie des configs Freqtrade..."
scp $SSH_OPTS freqtrade_config/config_futures.json  "$VPS_USER@$VPS_HOST:$VPS_DIR/freqtrade_config/"
scp $SSH_OPTS freqtrade_config/config_breakout.json "$VPS_USER@$VPS_HOST:$VPS_DIR/freqtrade_config/"

info "Copie des stratégies..."
scp $SSH_OPTS freqtrade_config/strategies/FundingRateArbitrage.py   "$VPS_USER@$VPS_HOST:$VPS_DIR/freqtrade_config/strategies/"
scp $SSH_OPTS freqtrade_config/strategies/BreakoutTrendFollowing.py "$VPS_USER@$VPS_HOST:$VPS_DIR/freqtrade_config/strategies/"

# ─── Lancement des containers ──────────────────────────────────────────────
info "Pull des images Docker et démarrage des containers..."
ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "
    set -e
    cd $VPS_DIR

    # Le .env est déjà en 0600 (créé via umask 077 ci-dessus) — pas de chmod.

    # Pull les dernières images
    docker compose -f $COMPOSE_FILE pull

    # Arrête les anciens containers si ils tournent
    docker compose -f $COMPOSE_FILE down --remove-orphans 2>/dev/null || true

    # Lance en mode détaché
    docker compose -f $COMPOSE_FILE up -d
"

# ─── Vérification ──────────────────────────────────────────────────────────
info "Vérification des containers..."
sleep 5

ssh $SSH_OPTS "$VPS_USER@$VPS_HOST" "
    cd $VPS_DIR
    echo ''
    echo '=== STATUS ==='
    docker compose -f $COMPOSE_FILE ps

    echo ''
    echo '=== LOGS freqtrade-funding (30 dernières lignes) ==='
    docker compose -f $COMPOSE_FILE logs --tail=30 freqtrade-funding

    echo ''
    echo '=== LOGS freqtrade-breakout (30 dernières lignes) ==='
    docker compose -f $COMPOSE_FILE logs --tail=30 freqtrade-breakout
"

info "Déploiement terminé."
info "Pour suivre les logs en temps réel :"
echo "  ssh $VPS_USER@$VPS_HOST 'docker compose -f $VPS_DIR/$COMPOSE_FILE logs -f'"
