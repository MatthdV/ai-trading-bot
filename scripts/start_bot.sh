#!/bin/bash
# Auto-start script for Trading Bot v2.0 with News Analysis

cd ~/.openclaw/workspace/skills/trading-bot
export $(cat .env | xargs)

# Kill existing bot if running
pkill -f "python3 main_v2.py" 2>/dev/null
sleep 1

# Start new instance
nohup python3 main_v2.py --mode paper > bot_v2.log 2>&1 &
echo $! > bot_v2.pid

echo "Trading Bot v2.0 démarré à $(date)"
echo "PID: $(cat bot_v2.pid)"
echo "Log: ~/.openclaw/workspace/skills/trading-bot/bot_v2.log"
