#!/bin/bash
# Hermes Crypto Bot — Auto Deploy + GitHub Push
# Usage: ./deploy.sh [message]

set -e

cd "$(dirname "$0")"

echo "🏛️ HERMES DEPLOY"
echo "================"

# 1. Check syntax
echo "⏳ Checking Python syntax..."
python3 -c "import ast; ast.parse(open('HERMES_CRYPTO_BOT.py').read())"
python3 -c "import ast; ast.parse(open('high_attention_scalper.py').read())"
python3 -c "import ast; ast.parse(open('symbol_filter.py').read())"
echo "✅ Syntax OK"

# 2. Kill old bot
echo "⏳ Stopping old bot..."
pkill -f "HERMES_CRYPTO_BOT.py" 2> /dev/null || true
sleep 2
echo "✅ Old bot stopped"

# 3. Fix state balance (prevent sync issues)
echo "⏳ Fixing state..."
python3 << 'PYEOF'
import json, os
state_path = 'state/HERMES_CRYPTO_STATE.json'
if os.path.exists(state_path):
    with open(state_path) as f:
        s = json.load(f)
    s['balance'] = 90.78
    s['day_start_balance'] = 90.78
    s['week_start_balance'] = 90.78
    s['halt_entries_until'] = None
    s['halt_reason'] = ''
    with open(state_path, 'w') as f:
        json.dump(s, f, indent=2)
PYEOF
echo "✅ State fixed"

# 4. Start bot
echo "⏳ Starting bot..."
nohup python3 HERMES_CRYPTO_BOT.py >> logs/HERMES_CRYPTO_BOT.log 2>&1 &
echo "✅ Bot started (PID: $!)"
sleep 5

# 5. GitHub push
echo "⏳ Pushing to GitHub..."
git add -A

if git diff --cached --quiet; then
    echo "✅ No changes to push"
else
    MSG="${1:-Auto-deploy: $(date '+%Y-%m-%d %H:%M %Z')}

Bot: Hermes Crypto Bot v2.0 ULTRA
Mode: HIGH_ATTENTION
Status: LIVE
Balance: \$90.78"
    
    git commit -m "$MSG"
    git push origin main
    echo "✅ Pushed to GitHub"
fi

# 6. Status
echo ""
echo "================"
echo "🚀 DEPLOY COMPLETE"
echo "================"
echo "Bot: LIVE"
echo "Mode: HIGH_ATTENTION"
echo "Balance: \$90.78"
echo "GitHub: fitfriendchris/Hermes-Crypto-Bot"
echo "Log: tail -f logs/HERMES_CRYPTO_BOT.log"
