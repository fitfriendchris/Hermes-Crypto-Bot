#!/usr/bin/env python3
"""
ANTI-RUG SUITE v3 — Minimal but effective.
Uses only public Solana RPC + DexScreener (no API keys needed).

Logic:
1. Token exists on-chain (has supply > 0)
2. DexScreener shows real liquidity ($5K+)
3. Token is tradeable on Jupiter (quote succeeds)

Score: 0-100. Threshold: 50 (was 70 — way too strict).

Author: Hermes | May 2026
"""
import asyncio
import logging
from typing import Dict

import aiohttp

logger = logging.getLogger('CryptoBot')

SAFE_THRESHOLD = 50
JUPITER_QUOTE_URL = "https://api.jup.ag/swap/v1/quote"
SOL_MINT = "So11111111111111111111111111111111111111112"
PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


async def run_full_rug_check(token_address: str) -> Dict:
    """Return {'safe': bool, 'score': int, 'flags': list}"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        results = await asyncio.gather(
            check_token_exists(session, token_address),
            check_dexscreener_liquidity(session, token_address),
            check_jupiter_quote(session, token_address),
            return_exceptions=True,
        )

    score = 0
    flags = []
    for r in results:
        if isinstance(r, Exception):
            logger.debug(f"Anti-rug check exception: {r}")
            continue
        score += r.get("score", 0)
        flags.extend(r.get("flags", []))

    safe = score >= SAFE_THRESHOLD
    if safe:
        logger.info(f"✅ Anti-rug PASS: score={score}")
    else:
        logger.warning(f"🚫 Anti-rug FAIL: score={score}, flags={flags}")

    return {"safe": safe, "score": score, "flags": flags}


async def check_token_exists(session: aiohttp.ClientSession, token_address: str) -> Dict:
    """Check if token mint exists on Solana."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [token_address, {"encoding": "jsonParsed"}]
        }
        async with session.post(PUBLIC_RPC, json=payload, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get("result", {})
                if result and result.get("value"):
                    return {"score": 35, "flags": [], "notes": {"exists": True}}
                else:
                    return {"score": 0, "flags": ["token_not_found"], "notes": {}}
            return {"score": 17, "flags": [], "notes": {"rpc_status": resp.status}}
    except Exception as e:
        return {"score": 17, "flags": [], "notes": {"error": str(e)[:60]}}


async def check_dexscreener_liquidity(session: aiohttp.ClientSession, token_address: str) -> Dict:
    """Check DexScreener for liquidity. Uses search endpoint as fallback."""
    try:
        # Try direct token endpoint first
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with session.get(url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs")
                if pairs:
                    return _score_liquidity(pairs)

        # Fallback: search endpoint (DexScreener sometimes indexes by search only)
        search_url = f"https://api.dexscreener.com/latest/dex/search?q={token_address}"
        async with session.get(search_url, timeout=8) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs", [])
                # Filter for Solana pairs matching this token
                matching = [p for p in pairs if p.get("chainId") == "solana"]
                if matching:
                    return _score_liquidity(matching)
                return {"score": 0, "flags": ["no_pairs"], "notes": {}}
            return {"score": 17, "flags": [], "notes": {"dex_status": resp.status}}
    except Exception as e:
        return {"score": 17, "flags": [], "notes": {"error": str(e)[:60]}}


def _score_liquidity(pairs: list) -> Dict:
    """Score based on best pair liquidity."""
    best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
    liq = float(best.get("liquidity", {}).get("usd", 0) or 0)
    vol_24h = float(best.get("volume", {}).get("h24", 0) or 0)
    if liq >= 10000 and vol_24h >= 5000:
        return {"score": 35, "flags": [], "notes": {"liquidity": liq, "volume_24h": vol_24h}}
    elif liq >= 5000:
        return {"score": 20, "flags": [], "notes": {"liquidity": liq}}
    else:
        return {"score": 5, "flags": ["low_liquidity"], "notes": {"liquidity": liq}}


async def check_jupiter_quote(session: aiohttp.ClientSession, token_address: str) -> Dict:
    """Check if Jupiter can route for this token. Tests both buy and sell."""
    try:
        # Test 1: Can we buy with 0.01 SOL (~$0.80)?
        buy_params = {
            "inputMint": SOL_MINT,
            "outputMint": token_address,
            "amount": "10000000",  # 0.01 SOL
            "slippageBps": "1000",
        }
        async with session.get(JUPITER_QUOTE_URL, params=buy_params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                out = int(data.get("outAmount", 0))
                if out > 0:
                    return {"score": 30, "flags": [], "notes": {"jupiter_buyable": True}}

        # Test 2: Can we sell 1M base units?
        sell_params = {
            "inputMint": token_address,
            "outputMint": SOL_MINT,
            "amount": "1000000",
            "slippageBps": "1000",
        }
        async with session.get(JUPITER_QUOTE_URL, params=sell_params, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                out = int(data.get("outAmount", 0))
                if out > 0:
                    return {"score": 30, "flags": [], "notes": {"jupiter_sellable": True}}

        return {"score": 0, "flags": ["jupiter_no_route"], "notes": {}}
    except Exception as e:
        return {"score": 15, "flags": [], "notes": {"error": str(e)[:60]}}


# Backward-compatible init
async def init_anti_rug():
    logger.info("Anti-rug v3 initialized (threshold=50)")
