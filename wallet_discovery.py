#!/usr/bin/env python3
"""
WALLET DISCOVERY — Find high-performing Solana traders to copy
Uses multiple strategies:
1. Seed expansion: start with known good wallets, trace their counter-parties
2. Trending token analysis: find wallets that bought early and sold high
3. Whale detection: large profitable moves on-chain

Author: Hermes | May 2026
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

import aiohttp

logger = logging.getLogger('CryptoBot')

SOLSCAN_API = "https://public-api.solscan.io"
HELIUS_RPC = "https://mainnet.helius-rpc.com/?api-key=1b648949-7c0e-4167-aaf2-3f7ad6d90e15"

# Known profitable wallets as seeds (public, from Subglow 30d leaderboard)
# Verified 2026-05-27: All wallets show positive 30d realized PnL on kolscan.io
SEED_WALLETS = [
    "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",  # #1 | +4954 SOL | Centedcya
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",  # #2 | +3219 SOL | theo
    "525LueqAyZJueCoiisfWy6nyh4MTvmF4X9jSqi6efXJT",  # #3 | +1503 SOL | Joji
    "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",  # #4 | +1331 SOL | Jijo
    "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",  # #5 | +1253 SOL | decu
]

# File to persist discovered wallets
SCOREBOARD_PATH = os.path.join(os.path.dirname(__file__), "state", "wallet_scoreboard.json")


class WalletDiscovery:
    """Discover and track profitable Solana wallets."""

    def __init__(self, scorer=None):
        self.scorer = scorer
        self._session: Optional[aiohttp.ClientSession] = None
        self.scoreboard: Dict[str, Dict] = {}  # wallet -> score data

    async def initialize(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"Accept": "application/json"}
        )
        self._load_scoreboard()
        logger.info(f"Wallet discovery initialized | {len(self.scoreboard)} wallets in scoreboard")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _load_scoreboard(self):
        """Load existing wallet scores."""
        if os.path.exists(SCOREBOARD_PATH):
            try:
                with open(SCOREBOARD_PATH) as f:
                    self.scoreboard = json.load(f)
                logger.info(f"Loaded {len(self.scoreboard)} wallets from scoreboard")
            except Exception as e:
                logger.warning(f"Scoreboard load failed: {e}")

    def _save_scoreboard(self):
        """Persist wallet scores."""
        os.makedirs(os.path.dirname(SCOREBOARD_PATH), exist_ok=True)
        try:
            with open(SCOREBOARD_PATH, "w") as f:
                json.dump(self.scoreboard, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Scoreboard save failed: {e}")

    # ── STRATEGY 1: SCORE KNOWN SEEDS ──

    async def score_seeds(self, seeds: Optional[List[str]] = None) -> List[Dict]:
        """Score a list of wallet addresses. Returns scored wallets sorted by composite score."""
        if seeds is None:
            seeds = SEED_WALLETS

        if not seeds:
            logger.warning("No seed wallets provided")
            return []

        results = []
        for wallet in seeds:
            if not wallet or len(wallet) < 32:
                continue

            # Skip if recently scored (cache 24h)
            existing = self.scoreboard.get(wallet)
            if existing:
                last_scored = datetime.fromisoformat(existing.get("last_updated", "2000-01-01"))
                if datetime.now(timezone.utc) - last_scored < timedelta(hours=24):
                    results.append(existing)
                    continue

            # Score fresh
            if self.scorer:
                try:
                    score = await self.scorer.score_wallet(wallet)
                    self.scoreboard[wallet] = score
                    results.append(score)
                    await asyncio.sleep(0.5)  # Rate limit
                except Exception as e:
                    logger.warning(f"Score failed for {wallet[:20]}...: {e}")
            else:
                logger.warning("No scorer available")

        self._save_scoreboard()

        # Sort by composite score descending
        results.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
        return results

    # ── STRATEGY 2: EXPAND FROM WINNERS ──

    async def expand_from_winners(self, top_n: int = 5) -> List[str]:
        """Find wallets that traded with our top performers (counter-party expansion)."""
        new_wallets: Set[str] = set()

        # Get top wallets from scoreboard
        sorted_wallets = sorted(
            self.scoreboard.items(),
            key=lambda x: x[1].get("composite_score", 0),
            reverse=True
        )[:top_n]

        for wallet, _ in sorted_wallets:
            try:
                counter_parties = await self._get_counter_parties(wallet, limit=20)
                new_wallets.update(counter_parties)
            except Exception as e:
                logger.debug(f"Expansion failed for {wallet[:20]}...: {e}")

        # Remove known wallets
        new_wallets -= set(self.scoreboard.keys())

        logger.info(f"Discovered {len(new_wallets)} new wallets from counter-party expansion")
        return list(new_wallets)

    async def _get_counter_parties(self, wallet: str, limit: int = 20) -> Set[str]:
        """Find wallets that have traded with this wallet."""
        counter_parties: Set[str] = set()

        url = f"{SOLSCAN_API}/account/transactions?account={wallet}&limit={limit}"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return set()
                data = await resp.json()
                txs = data if isinstance(data, list) else []

                for tx in txs:
                    for acc in tx.get("tokenBalanes", []) or []:
                        owner = acc.get("owner")
                        if owner and owner != wallet:
                            counter_parties.add(owner)
        except Exception:
            pass

        return counter_parties

    # ── STRATEGY 3: DISCOVER FROM TRENDING TOKENS ──

    async def discover_from_trending(self, days: int = 7) -> List[str]:
        """Find wallets that bought trending tokens early and sold profitably."""
        # Get trending tokens from DexScreener
        trending = await self._get_trending_tokens()
        profitable_wallets: Set[str] = set()

        for token in trending[:10]:  # Check top 10
            token_address = token.get("tokenAddress", "")
            if not token_address:
                continue

            # Find early buyers who also sold
            early_buyers = await self._get_early_buyers(token_address, days=days)
            profitable_wallets.update(early_buyers)
            await asyncio.sleep(0.5)

        logger.info(f"Discovered {len(profitable_wallets)} wallets from trending tokens")
        return list(profitable_wallets)

    async def _get_trending_tokens(self) -> List[Dict]:
        """Fetch trending Solana tokens from DexScreener."""
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = []
                    for t in data:
                        if t and t.get("chainId") == "solana":
                            tokens.append(t)
                    return tokens
        except Exception as e:
            logger.warning(f"Trending fetch failed: {e}")
        return []

    async def _get_early_buyers(self, token_address: str, days: int = 7) -> Set[str]:
        """Find wallets that bought a token in the first 24h and sold at profit."""
        # This is a simplified version — real implementation needs transfer parsing
        url = f"{SOLSCAN_API}/token/holders?tokenAddress={token_address}&limit=50"
        buyers: Set[str] = set()

        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    holders = data if isinstance(data, list) else []
                    for h in holders:
                        addr = h.get("owner", "")
                        if addr:
                            buyers.add(addr)
        except Exception:
            pass

        return buyers

    # ── FULL DISCOVERY CYCLE ──

    async def run_discovery_cycle(self) -> List[Dict]:
        """Run full discovery: score seeds → expand → score new → rank → return top."""
        logger.info("🔍 Starting wallet discovery cycle...")

        # 1. Score existing seeds
        scored = await self.score_seeds()

        # 2. Expand from winners
        new_wallets = await self.expand_from_winners(top_n=3)
        if new_wallets:
            new_scores = await self.score_seeds(new_wallets)
            scored.extend(new_scores)

        # 3. Discover from trending
        trending_wallets = await self.discover_from_trending()
        if trending_wallets:
            # Only score wallets we haven't seen
            unseen = [w for w in trending_wallets if w not in self.scoreboard]
            if unseen:
                trending_scores = await self.score_seeds(unseen[:20])  # Limit to 20
                scored.extend(trending_scores)

        # 4. Deduplicate and sort
        seen = set()
        unique_scored = []
        for s in scored:
            w = s.get("wallet", "")
            if w and w not in seen:
                seen.add(w)
                unique_scored.append(s)

        unique_scored.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

        self._save_scoreboard()

        # Log summary
        mirror_count = sum(1 for s in unique_scored if s.get("recommendation") == "MIRROR")
        watch_count = sum(1 for s in unique_scored if s.get("recommendation") == "WATCH")
        logger.info(
            f"Discovery complete: {len(unique_scored)} wallets | "
            f"MIRROR: {mirror_count} | WATCH: {watch_count}"
        )

        return unique_scored

    # ── GET TOP MIRRORS ──

    def get_top_mirrors(self, n: int = 3) -> List[Dict]:
        """Return top N wallets recommended for mirroring."""
        mirrors = [
            s for s in self.scoreboard.values()
            if s.get("recommendation") == "MIRROR"
        ]
        mirrors.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
        return mirrors[:n]

    def get_all_scored(self) -> List[Dict]:
        """Return all scored wallets."""
        return sorted(
            self.scoreboard.values(),
            key=lambda x: x.get("composite_score", 0),
            reverse=True
        )


# ── BACKWARD COMPATIBLE INIT ──
async def init_wallet_discovery():
    logger.info("Wallet discovery module ready")


# ── TEST ──
async def test():
    from wallet_scorer import WalletScorer

    scorer = WalletScorer()
    await scorer.initialize()

    discovery = WalletDiscovery(scorer)
    await discovery.initialize()

    # Test with a seed wallet if you have one
    # results = await discovery.run_discovery_cycle()
    # print(f"Found {len(results)} wallets")

    await discovery.close()
    await scorer.close()


if __name__ == "__main__":
    asyncio.run(test())
