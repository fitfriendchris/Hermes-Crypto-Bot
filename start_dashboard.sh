#!/bin/bash
# Crypto Dashboard Launcher — starts HERMES_TELEGRAM_DASHBOARD
# Managed by LaunchAgent com.hermes.crypto-dashboard

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BOT_DIR" || exit 1

# Activate venv if present
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
elif [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

export PYTHONPATH="$BOT_DIR:${PYTHONPATH:-}"

exec python3 HERMES_TELEGRAM_DASHBOARD.py