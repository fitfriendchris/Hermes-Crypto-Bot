#!/bin/bash
# Auto-push script for Hermes Crypto Bot
# Runs after every deploy to archive changes

cd "$(dirname "$0")"

# Check if there are changes
if git diff --quiet && git diff --cached --quiet; then
    echo "No changes to push"
    exit 0
fi

# Stage all changes
git add -A

# Commit with timestamp
COMMIT_MSG="Auto-deploy: $(date '+%Y-%m-%d %H:%M %Z')

Changes detected in:
$(git diff --name-only --cached | head -20)

Bot: Hermes Crypto Bot v2.0
Mode: HIGH_ATTENTION
Status: LIVE"

git commit -m "$COMMIT_MSG" > /dev/null 2>&1

# Push to GitHub
if git push origin main > /dev/null 2>&1; then
    echo "✅ Pushed to GitHub"
else
    echo "❌ Push failed"
    exit 1
fi
