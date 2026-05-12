#!/bin/bash
# HERMES CRYPTO BOT — STARTUP SCRIPT
# Usage: ./start_hermes.sh [paper|live]

MODE=${1:-paper}
LOG_DIR="$(dirname "$0")/logs"
STATE_DIR="$(dirname "$0")/state"

echo "🚀 Starting HERMES CRYPTO BOT v2.1"
echo "Mode: $MODE"
echo "Time: $(date)"

# Kill existing bot processes
pkill -f HERMES_CRYPTO_BOT.py 2>/dev/null
sleep 2

# Verify clean
BOT_COUNT=$(ps aux | grep HERMES_CRYPTO_BOT | grep -v grep | wc -l)
if [ "$BOT_COUNT" -gt 0 ]; then
    echo "⚠️  Warning: $BOT_COUNT bot processes still running"
    ps aux | grep HERMES_CRYPTO_BOT | grep -v grep
fi

# Start bot WITHOUT redirecting stdout (FileHandler already logs to file)
nohup python3 HERMES_CRYPTO_BOT.py > /dev/null 2>> "$LOG_DIR/HERMES_CRYPTO_BOT.log" &
echo "Bot PID: $!"
sleep 5

# Verify running
NEW_COUNT=$(ps aux | grep HERMES_CRYPTO_BOT | grep -v grep | wc -l)
if [ "$NEW_COUNT" -eq 1 ]; then
    echo "✅ Bot running cleanly (1 process)"
    tail -3 "$LOG_DIR/HERMES_CRYPTO_BOT.log"
elif [ "$NEW_COUNT" -gt 1 ]; then
    echo "⚠️  Multiple processes detected: $NEW_COUNT"
    ps aux | grep HERMES_CRYPTO_BOT | grep -v grep
else
    echo "❌ Bot failed to start"
    tail -10 "$LOG_DIR/HERMES_CRYPTO_BOT.log"
fi

# Start dashboard
echo ""
echo "📊 Starting Telegram Dashboard..."
nohup python3 HERMES_TELEGRAM_DASHBOARD.py >> "$LOG_DIR/dashboard.stderr.log" 2>&1 &
echo "Dashboard PID: $!"
sleep 3

# Verify dashboard
DASH_COUNT=$(ps aux | grep HERMES_TELEGRAM_DASHBOARD | grep -v grep | wc -l)
if [ "$DASH_COUNT" -eq 1 ]; then
    echo "✅ Dashboard running"
else
    echo "⚠️  Dashboard issues: $DASH_COUNT processes"
fi

echo ""
echo "📈 Bot Status:"
echo "  Mode: $MODE"
echo "  Log: $LOG_DIR/HERMES_CRYPTO_BOT.log"
echo "  State: $STATE_DIR/"
echo ""
echo "To monitor: tail -f $LOG_DIR/HERMES_CRYPTO_BOT.log"
echo "To stop: pkill -f HERMES_CRYPTO_BOT.py"
