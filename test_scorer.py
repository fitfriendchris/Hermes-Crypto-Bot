#!/usr/bin/env python3
"""Test wallet scorer with one seed wallet."""
import asyncio
from wallet_scorer import WalletScorer

async def main():
    scorer = WalletScorer()
    await scorer.initialize()

    # Test top wallet
    wallet = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
    print(f"Scoring: {wallet}")
    result = await scorer.score_wallet(wallet, days=30)
    print(f"Result: {result}")

    await scorer.close()

if __name__ == "__main__":
    asyncio.run(main())
