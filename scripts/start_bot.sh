#!/bin/bash
# Auto-start script for Trading Bot (main.py)

cd "$(dirname "$0")/.."
set -a; source .env; set +a

# Kill existing bot if running
pkill -f "python3 main.py" 2>/dev/null
sleep 1

# Start new instance
nohup python3 main.py --mode paper > bot.log 2>&1 &
echo $! > bot.pid

echo "Trading Bot démarré à $(date)"
echo "PID: $(cat bot.pid)"
echo "Log: $(pwd)/bot.log"
