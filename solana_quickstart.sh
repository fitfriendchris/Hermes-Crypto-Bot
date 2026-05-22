#!/bin/bash
# SOLANA MOMENTUM BOT — QUICKSTART (LIVE MODE)

set -e

echo "🚀 SOLANA MOMENTUM BOT"
echo "======================"
echo ""

cd ~/Hermes-Crypto-Bot

# Check .env exists
if [ ! -f .env ]; then
    echo "❌ No .env file found"
    echo "Create one with your wallet keys"
    exit 1
fi

# Check for private key
echo "Checking wallet keys..."
if grep -q "PHANTOM_PRIVATE_KEY=\|EXODUS_PRIVATE_KEY=\|SOLANA_PRIVATE_KEY=" .env; then
    echo "✅ Wallet key found in .env"
else
    echo "❌ No private key in .env"
    echo "Add: PHANTOM_PRIVATE_KEY='your_base58_key'"
    exit 1
fi

# Check live mode
if grep -q "LIVE_MODE=true" .env; then
    echo "✅ LIVE_MODE enabled"
else
    echo "⚠️  LIVE_MODE not set in .env"
    echo "Set: LIVE_MODE=true for real trades"
    echo "Set: LIVE_MODE=false for paper/simulated"
fi

# Install deps
echo ""
echo "Installing dependencies..."
pip install -q aiohttp requests base58 solders solana 2>/dev/null || pip install aiohttp requests base58 solders solana

# Check wallet balance via RPC (best effort)
echo ""
echo "💰 Checking wallet balance..."
python3 -c "
import os, json
from solders.keypair import Keypair
import base58

priv = os.getenv('PHANTOM_PRIVATE_KEY') or os.getenv('EXODUS_PRIVATE_KEY') or os.getenv('SOLANA_PRIVATE_KEY','')
if priv:
    try:
        kb = base58.b58decode(priv)
        kp = Keypair.from_bytes(kb)
        print(f'Wallet: {kp.pubkey()}')
    except:
        print('Could not decode key')
else:
    print('No key found')
" 2>/dev/null || true

echo ""
echo "Starting bot in 3 seconds..."
echo "⚠️  THIS WILL EXECUTE REAL TRADES"
echo "   Press Ctrl+C NOW to abort"
sleep 3

echo ""
python3 solana_executor.py
