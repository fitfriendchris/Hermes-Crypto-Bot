#!/usr/bin/env python3
"""
LAUNCH SNIPER — Pump.fun & Raydium Micro-Cap Launcher
Monitors new token launches and snipes at optimal liquidity threshold.

Key Features:
- Sub-second execution (target: <1s from launch detection to buy)
- Bonding curve monitoring (Pump.fun)
- Liquidity threshold sniping ($10K+ LP)
- Anti-rug pre-checks before execution
- Auto-exit at 2R (100% gain) or 4 hours

Requirements:
- Dedicated Solana RPC (no throttling)
- Jito bundles for MEV protection
- Real-time mempool monitoring
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger('CryptoBot')

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── SNIPER CONFIG ──
SNIPER_CONFIG = {
    'liquidity_threshold_usd': 10000,    # Snipe when LP >= $10K
    'max_mc_at_entry': 500000,           # Max $500K market cap
    'min_holder_count': 10,              # At least 10 holders
    'max_slippage_pct': 0.05,            # 5% max slippage
    'position_size_pct': 0.08,           # 8% per snipe (was 5%) — launch pumps are highest alpha
    # Exit strategy: 50% at 2× (100%), trail remaining 50% with 20% stop
    # Pump.fun tokens can go 10×-50×; old 2R exit missed almost everything
    'exit1_at_x': 2.0,                   # sell 50% when price = 2× entry
    'trail_pct': 0.20,                   # 20% trail on remaining 50% (more room for pump volatility)
    'stop_pct': 0.25,                    # 25% hard stop (pump tokens are volatile at entry)
    'time_stop_minutes': 120,            # 2h max hold (was 4h) — pump resolves fast
    'max_daily_snipes': 5,               # Max 5 snipes/day
    'cooldown_between_snipes_min': 20,   # 20 min between snipes (was 30)
}

# ── LAUNCH TRACKING ──
monitored_launches: Dict[str, Dict] = {}
recent_snipes: List[Dict] = []

async def fetch_pumpfun_launches(session: aiohttp.ClientSession) -> List[Dict]:
    """Fetch latest Pump.fun launches."""
    try:
        # Pump.fun API endpoint
        url = "https://frontend-api.pump.fun/coins/for-you"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('coins', [])
    except Exception as e:
        logger.debug(f"Pump.fun fetch error: {e}")
    return []

async def fetch_raydium_new_pools(session: aiohttp.ClientSession) -> List[Dict]:
    """Fetch new Raydium liquidity pools."""
    try:
        url = "https://api.dexscreener.com/latest/dex/pairs/solana"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get('pairs', [])
                # Filter for new pools (<1 hour old)
                new_pairs = []
                for p in pairs:
                    created = p.get('pairCreatedAt', 0)
                    age_hours = (time.time() * 1000 - created) / 3600000 if created else 999
                    if age_hours < 1:
                        new_pairs.append(p)
                return new_pairs
    except Exception as e:
        logger.debug(f"Raydium fetch error: {e}")
    return []

async def check_liquidity(session: aiohttp.ClientSession, token_address: str) -> float:
    """Check liquidity USD for a token."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get('pairs', [])
                if pairs:
                    return float(pairs[0].get('liquidity', {}).get('usd', 0))
    except Exception:
        pass
    return 0.0

async def check_holder_count(session: aiohttp.ClientSession, token_address: str) -> int:
    """Check unique holder count."""
    try:
        # Solscan API
        url = f"https://public-api.solscan.io/token/holders?tokenAddress={token_address}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get('total', 0)
    except Exception:
        pass
    return 0

async def check_market_cap(session: aiohttp.ClientSession, token_address: str) -> float:
    """Check market cap USD."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get('pairs', [])
                if pairs:
                    return float(pairs[0].get('marketCap', 0))
    except Exception:
        pass
    return float('inf')

async def anti_rug_check(session: aiohttp.ClientSession, token_address: str) -> Dict:
    """
    Run anti-rug checks on token.
    Returns: {'safe': bool, 'flags': List[str]}
    """
    flags = []
    
    # 1. Check contract on TokenSniffer
    try:
        url = f"https://tokensniffer.com/api/v1/tokens/{token_address}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get('is_honeypot'):
                    flags.append('honeypot')
                if data.get('mint_function_enabled'):
                    flags.append('mint_enabled')
                if data.get('owner_can_blacklist'):
                    flags.append('blacklist_risk')
    except Exception:
        pass
    
    # 2. Check Bubblemaps for holder concentration
    try:
        url = f"https://api.bubblemaps.io/v1/solana/{token_address}"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                top_10_pct = data.get('top_10_holder_pct', 100)
                if top_10_pct > 50:
                    flags.append(f'top10_concentration_{top_10_pct:.0f}%')
    except Exception:
        pass
    
    # 3. Check if LP is burned/locked
    try:
        # Simplified: check if liquidity is growing (good) or shrinking (bad)
        liq = await check_liquidity(session, token_address)
        if liq < 5000:
            flags.append('low_liquidity')
    except Exception:
        pass
    
    return {
        'safe': len(flags) == 0,
        'flags': flags,
    }

async def evaluate_launch(token: Dict) -> Optional[Dict]:
    """
    Evaluate if a new launch is snipe-worthy.
    Returns position dict or None.
    """
    sym = token.get('symbol', 'UNKNOWN')
    addr = token.get('mint') or token.get('address') or token.get('tokenAddress')
    
    if not addr:
        return None
    
    # Skip if already monitored
    if addr in monitored_launches:
        return None
    
    logger.info(f"🔍 Evaluating launch: {sym}")
    
    async with aiohttp.ClientSession() as session:
        # 1. Check liquidity threshold
        liq = await check_liquidity(session, addr)
        if liq < SNIPER_CONFIG['liquidity_threshold_usd']:
            logger.debug(f"🚫 {sym} — Liquidity ${liq:.0f} < ${SNIPER_CONFIG['liquidity_threshold_usd']}")
            return None
        
        # 2. Check market cap
        mcap = await check_market_cap(session, addr)
        if mcap > SNIPER_CONFIG['max_mc_at_entry']:
            logger.debug(f"🚫 {sym} — Market cap ${mcap:.0f} > ${SNIPER_CONFIG['max_mc_at_entry']}")
            return None
        
        # 3. Check holder count
        holders = await check_holder_count(session, addr)
        if holders < SNIPER_CONFIG['min_holder_count']:
            logger.debug(f"🚫 {sym} — Holders {holders} < {SNIPER_CONFIG['min_holder_count']}")
            return None
        
        # 4. Anti-rug check
        rug_check = await anti_rug_check(session, addr)
        if not rug_check['safe']:
            logger.warning(f"🚫 {sym} — RUG FLAGS: {', '.join(rug_check['flags'])}")
            return None
        
        # 5. Build position
        price = float(token.get('price', 0) or token.get('priceUsd', 0))
        if price <= 0:
            return None
        
        # Success — mark as monitored
        monitored_launches[addr] = {
            'symbol': sym,
            'address': addr,
            'price_at_snipe': price,
            'liquidity': liq,
            'market_cap': mcap,
            'holders': holders,
            'sniped_at': datetime.now().isoformat(),
            'rug_flags': rug_check['flags'],
        }
        
        logger.info(f"✅ {sym} SNIPED: ${liq:.0f} LP | {holders} holders | ${mcap:.0f} MC")
        
        return {
            'token': sym.upper(),
            'address': addr,
            'chain': 'solana',
            'entry': price,
            'invested': 0,  # Will be set by caller based on balance
            'quantity': 0,
            'stop': price * 0.65,  # 35% stop for micro-cap launches
            'stop_type': 'snipe_stop',
            'risk_pct': 0.35,
            'opened_at': datetime.now().isoformat(),
            'tier_exits': {'1': False, '2': False, '3': False, '4': False},
            'highest_price': price,
            'last_price': price,
            'source': 'launch_sniper',
            'launch_data': monitored_launches[addr],
        }

async def sniper_loop(dex_connector=None):
    """
    Main sniper loop: monitor launches and snipe.
    
    Args:
        dex_connector: Optional DEX connector for execution
    """
    logger.info("🎯 Launch sniper initialized")
    
    snipes_today = 0
    last_snipe_time = None
    
    while True:
        try:
            # Check daily limit
            if snipes_today >= SNIPER_CONFIG['max_daily_snipes']:
                logger.info(f"Daily snipe limit reached ({snipes_today})")
                await asyncio.sleep(300)
                continue
            
            # Check cooldown
            if last_snipe_time:
                elapsed = (datetime.now() - last_snipe_time).total_seconds() / 60
                if elapsed < SNIPER_CONFIG['cooldown_between_snipes_min']:
                    await asyncio.sleep(30)
                    continue
            
            # Fetch launches
            async with aiohttp.ClientSession() as session:
                pump_launches = await fetch_pumpfun_launches(session)
                raydium_launches = await fetch_raydium_new_pools(session)
                
                all_launches = pump_launches + raydium_launches
                
                for launch in all_launches[:10]:  # Check top 10
                    result = await evaluate_launch(launch)
                    if result:
                        snipes_today += 1
                        last_snipe_time = datetime.now()
                        
                        # Return for execution by main bot
                        return result
                        
        except Exception as e:
            logger.error(f"Sniper error: {e}")
        
        await asyncio.sleep(10)  # Check every 10 seconds


# ── INIT ──
async def init_launch_sniper():
    """Initialize launch sniper module."""
    logger.info("🎯 Launch sniper initialized")
    logger.info(f"  Liquidity threshold: ${SNIPER_CONFIG['liquidity_threshold_usd']:,}")
    logger.info(f"  Max market cap: ${SNIPER_CONFIG['max_mc_at_entry']:,}")
    logger.info(f"  Exit 1 at: {SNIPER_CONFIG['exit1_at_x']}× (sell 50%)")
    logger.info(f"  Trail: {SNIPER_CONFIG['trail_pct']*100:.0f}% on remaining 50%")
    logger.info(f"  Time stop: {SNIPER_CONFIG['time_stop_minutes']} min")


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    test_token = {
        'symbol': 'TEST',
        'mint': 'So11111111111111111111111111111111111111112',
        'price': 0.001,
    }
    
    result = asyncio.run(evaluate_launch(test_token))
    if result:
        print(f"✅ SNIPED: {result['token']} @ ${result['entry']}")
    else:
        print("🚫 Not sniped")
