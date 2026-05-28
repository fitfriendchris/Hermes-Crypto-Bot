# SEED WALLETS — Profitable Solana Wallets to Track

The copy trading system needs "seed wallets" — known profitable wallets to bootstrap discovery.

## How to Find Good Wallets

1. Go to DexScreener: https://dexscreener.com/solana
2. Find a token that pumped recently
3. Click the token → "Holders" tab
4. Look for wallets that:
   - Bought early (first 10-20 holders)
   - Have 10+ trades in last 30 days
   - Show green PnL on Solscan

5. Copy the wallet address and add to `wallet_discovery.py` SEED_WALLETS list

## Or use public sources:
- https://birdeye.so (leaderboard)
- https://dexcheck.ai (smart money)
- https://ape.pro (top traders)
- https://subglow.io (copy trading leaderboard)

## Current Status

**No seeds configured.** The bot is running but won't find wallets until you add at least 1-3 seed addresses.

Add them to `wallet_discovery.py` line 20:
```python
SEED_WALLETS = [
    "YOUR_WALLET_ADDRESS_HERE",
]
```

Then redeploy.
