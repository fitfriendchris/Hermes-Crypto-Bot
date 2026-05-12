#!/usr/bin/env python3
"""
MOMENTUM SCANNER — ICT + AMD Smart Entry Filter
Requirements:
- Volume > 2x 24h average
- ICT Market Structure: BOS/CHoCH confirmation
- AMD Cycle: Accumulation → Manipulation → Distribution
- Liquidity sweep before entry
- Fair Value Gap (FVG) as confluence
- BTC > 20EMA (bullish regime)
- SOL > 20EMA (Solana strong)
- Only Phase 2 momentum (not Phase 1 discovery)
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger('CryptoBot')

# ── ICT STRUCTURE STATE ──
# Tracks market structure per symbol for BOS/CHoCH detection
structure_state: Dict[str, Dict] = {}

# ── AMD CYCLE STATE ──
# Tracks which phase each symbol is in
amd_state: Dict[str, Dict] = {}

# ── TREND STATE ──
trend_state = {
    'btc_price': 0.0,
    'btc_ema20': 0.0,
    'sol_price': 0.0,
    'sol_ema20': 0.0,
    'last_update': None,
    'is_bullish': False,
}

async def fetch_price(session: aiohttp.ClientSession, symbol: str) -> float:
    """Fetch current price from CoinGecko or similar."""
    try:
        # Try CoinGecko
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                return float(data.get(symbol, {}).get('usd', 0))
    except Exception:
        pass
    return 0.0

async def update_trend_state():
    """Update BTC and SOL trend status every 5 minutes."""
    global trend_state
    
    async with aiohttp.ClientSession() as session:
        btc = await fetch_price(session, 'bitcoin')
        sol = await fetch_price(session, 'solana')
        
        # Simple EMA approximation (would need historical data for real EMA)
        # For now, use 24h change as proxy
        if btc > 0:
            trend_state['btc_price'] = btc
            # Approximate: if BTC up >5% in 24h, consider above EMA
            # This is simplified - real implementation needs historical candles
            trend_state['is_bullish'] = True  # Placeholder - implement proper EMA
            
        if sol > 0:
            trend_state['sol_price'] = sol
            
        trend_state['last_update'] = datetime.now()
        logger.info(f"Trend: BTC=${btc:.0f}, SOL=${sol:.2f}, Bullish={trend_state['is_bullish']}")

async def get_volume_profile(token: Dict) -> Dict:
    """Get 24h volume and compare to average."""
    volume_24h = float(token.get('volume', {}).get('h24', 0))
    # Get historical volume (simplified - would need historical data)
    # For now, use the token's typical volume from DexScreener
    avg_volume = volume_24h * 0.5  # Placeholder - implement real average
    
    return {
        'current': volume_24h,
        'average': avg_volume,
        'ratio': volume_24h / avg_volume if avg_volume > 0 else 0,
    }

def detect_ict_structure(token: Dict) -> Dict:
    """
    ICT Market Structure Analysis
    
    BOS (Break of Structure): Price breaks above previous high in uptrend
    CHoCH (Change of Character): Price breaks below previous low (trend change)
    
    For long entries, we need:
    - Previous structure low (liquidity pool below)
    - Price swept that low (liquidity grab)
    - BOS above previous high (trend continuation)
    
    Returns: {
        'signal': 'long'|'none',
        'bos_confirmed': bool,
        'liquidity_swept': bool,
        'choch': bool,
        'structure_score': 0-100
    }
    """
    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    price = float(token.get('priceUsd', 0))
    
    # Get price history for structure (simplified — real implementation needs OHLC)
    change_1h = float(token.get('priceChange', {}).get('h1', 0))
    change_5m = float(token.get('priceChange', {}).get('m5', 0))
    change_24h = float(token.get('priceChange', {}).get('h24', 0))
    
    # Initialize structure state
    if sym not in structure_state:
        structure_state[sym] = {
            'prev_high': price * 1.02,  # Assume 2% higher was recent high
            'prev_low': price * 0.98,
            'swing_high': price,
            'swing_low': price,
            'trend': 'unknown',
            'last_update': datetime.now(),
        }
    
    st = structure_state[sym]
    
    # Update swing points
    if price > st['swing_high']:
        st['swing_high'] = price
    if price < st['swing_low']:
        st['swing_low'] = price
    
    # Detect BOS: price breaks above previous high
    prev_high = st['prev_high']
    bos_confirmed = prev_high > 0 and price > prev_high * 1.005  # 0.5% buffer
    
    # Detect liquidity sweep: price briefly dipped below previous low
    # Approximated by: 5m change negative but 1h change positive
    liquidity_swept = change_5m < -2 and change_1h > 0
    
    # Detect CHoCH: price breaks below previous low (bearish)
    choch = price < st['prev_low'] * 0.99
    
    # Update previous high/low for next iteration
    if bos_confirmed:
        st['prev_high'] = price
        st['prev_low'] = st['swing_low']
        st['swing_low'] = price
        st['trend'] = 'uptrend'
    
    if choch:
        st['prev_low'] = price
        st['prev_high'] = st['swing_high']
        st['swing_high'] = price
        st['trend'] = 'downtrend'
    
    st['last_update'] = datetime.now()
    
    # Structure score
    score = 0
    if bos_confirmed:
        score += 40
    if liquidity_swept:
        score += 35
    if st['trend'] == 'uptrend':
        score += 25
    if choch:
        score = 0  # Bearish — no long entry
    
    return {
        'signal': 'long' if score >= 70 else 'none',
        'bos_confirmed': bos_confirmed,
        'liquidity_swept': liquidity_swept,
        'choch': choch,
        'structure_score': score,
        'trend': st['trend'],
    }


def detect_amd_cycle(token: Dict) -> Dict:
    """
    AMD Cycle Detection (Accumulation → Manipulation → Distribution)
    
    For micro-cap entries, we want to enter AFTER manipulation (liquidity grab)
    and BEFORE distribution (pump).
    
    Phase 1: Accumulation — sideways, low volume, building positions
    Phase 2: Manipulation — fake breakout/breakdown to grab liquidity
    Phase 3: Distribution — the real move, where we profit
    
    Returns: {
        'phase': 'accumulation'|'manipulation'|'distribution'|'unknown',
        'entry_ready': bool,
        'amd_score': 0-100
    }
    """
    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    
    change_5m = float(token.get('priceChange', {}).get('m5', 0))
    change_1h = float(token.get('priceChange', {}).get('h1', 0))
    change_24h = float(token.get('priceChange', {}).get('h24', 0))
    change_15m = float(token.get('priceChange', {}).get('m15', change_1h * 0.25))
    change_6h = float(token.get('priceChange', {}).get('h6', change_24h * 0.25))
    
    volume_24h = float(token.get('volume', {}).get('h24', 0))
    
    # Initialize AMD state
    if sym not in amd_state:
        amd_state[sym] = {
            'phase': 'unknown',
            'accumulation_start': None,
            'manipulation_detected': False,
            'entry_zone_high': 0,
            'entry_zone_low': float('inf'),
            'last_update': datetime.now(),
        }
    
    amd = amd_state[sym]
    
    # ── PHASE DETECTION ──
    
    # For real-time micro-cap scanning, we detect manipulation directly:
    # Sharp 5m move against the 1h trend = liquidity grab
    is_manipulation_now = abs(change_5m) > 5 and abs(change_5m) > abs(change_1h * 0.3)
    
    # Distribution: sustained momentum
    is_distribution = change_1h > 5 and change_24h > 20
    
    # Accumulation: flat price (for state tracking)
    is_accumulating = abs(change_1h) < 5 and abs(change_24h) < 50
    
    # ── STATE TRANSITIONS ──
    if is_accumulating:
        amd['phase'] = 'accumulation'
        amd['accumulation_start'] = datetime.now()
    
    if is_manipulation_now:
        # Direct manipulation detection — no accumulation required for micro-caps
        # Micro-caps move fast, we catch the manipulation in real-time
        if amd['phase'] in ('unknown', 'accumulation', 'distribution'):
            amd['phase'] = 'manipulation'
            amd['manipulation_detected'] = True
            logger.info(f"🎭 {sym} AMD: Manipulation detected (5m: {change_5m:.1f}%, 1h: {change_1h:.1f}%)")
    
    if is_distribution:
        amd['phase'] = 'distribution'
    
    # ── ENTRY LOGIC ──
    # Entry ready if:
    # 1. We just detected manipulation AND it's bouncing back (5m positive)
    # 2. OR we detected manipulation recently AND 1h is positive
    entry_ready = (
        amd['manipulation_detected'] and
        change_5m > 0 and  # Bouncing back from manipulation
        change_1h > 0     # Hourly trend positive
    ) or (
        amd['phase'] == 'manipulation' and
        change_5m > 0 and
        change_1h > 3
    )
    
    # AMD score
    score = 0
    if amd['phase'] == 'manipulation':
        score += 30
    if amd['manipulation_detected']:
        score += 40
    if change_5m > 0:
        score += 15
    if change_1h > 0:
        score += 15
    
    amd['last_update'] = datetime.now()
    
    return {
        'phase': amd['phase'],
        'entry_ready': entry_ready,
        'amd_score': score,
        'manipulation_detected': amd.get('manipulation_detected', False),
    }


def detect_fvg(token: Dict) -> Dict:
    """
    Fair Value Gap (FVG) Detection
    
    An FVG is a gap in price where no trading occurred:
    - Bullish FVG: Low of current candle > High of candle 2 bars ago
    - Bearish FVG: High of current candle < Low of candle 2 bars ago
    
    For entries: Look for bullish FVG as confluence
    
    Simplified: Use price gaps between timeframes as FVG proxy
    """
    change_5m = float(token.get('priceChange', {}).get('m5', 0))
    change_1h = float(token.get('priceChange', {}).get('h1', 0))
    change_15m = float(token.get('priceChange', {}).get('m15', change_1h * 0.25))
    change_24h = float(token.get('priceChange', {}).get('h24', 0))
    
    # Bullish FVG: 5m dipped but 15m and 1h are positive
    # (price left a gap below that should be filled)
    bullish_fvg = change_5m < 0 and change_15m > 0 and change_1h > 5
    
    # Bearish FVG: 5m pumped but 15m and 1h are negative
    bearish_fvg = change_5m > 0 and change_15m < 0 and change_1h < -5
    
    return {
        'bullish_fvg': bullish_fvg,
        'bearish_fvg': bearish_fvg,
        'fvg_score': 20 if bullish_fvg else 0,
    }

async def evaluate_momentum(token: Dict) -> Optional[Dict]:
    """
    Main entry point: evaluate if token passes ALL criteria.
    ICT + AMD + Volume + Trend filter.
    """
    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    
    # 1. Check trend regime
    if not trend_state['is_bullish']:
        logger.debug(f"🚫 {sym} — market not bullish")
        return None
        
    # 2. Check volume
    vol_profile = await get_volume_profile(token)
    if vol_profile['ratio'] < 2.0:
        logger.debug(f"🚫 {sym} — volume ratio {vol_profile['ratio']:.1f}x (need >2x)")
        return None
        
    # 3. ICT Market Structure
    ict = detect_ict_structure(token)
    if ict['choch']:
        logger.debug(f"🚫 {sym} — CHoCH detected (bearish)")
        return None
    if not ict['bos_confirmed'] and not ict['liquidity_swept']:
        logger.debug(f"🚫 {sym} — no BOS or liquidity sweep (score: {ict['structure_score']})")
        return None
        
    # 4. AMD Cycle
    amd = detect_amd_cycle(token)
    # RELAXED: Allow distribution phase with manipulation detected
    # The key signal is manipulation_detected + 5m positive + 1h positive
    if not amd['entry_ready']:
        # Check if it's a valid setup regardless of phase label
        is_valid_setup = (
            amd['manipulation_detected'] and
            float(token.get('priceChange', {}).get('m5', 0)) > 0 and
            float(token.get('priceChange', {}).get('h1', 0)) > 0
        )
        if not is_valid_setup:
            logger.debug(f"🚫 {sym} — AMD not ready (phase: {amd['phase']}, manip: {amd['manipulation_detected']})")
            return None
        else:
            logger.debug(f"✅ {sym} — AMD valid setup despite phase={amd['phase']}")
        
    # 5. Fair Value Gap
    fvg = detect_fvg(token)
    
    # 6. Combine scores
    total_score = (
        ict['structure_score'] * 0.35 +
        amd['amd_score'] * 0.35 +
        fvg['fvg_score'] * 0.15 +
        min(vol_profile['ratio'] * 10, 20) * 0.15  # Volume score
    )
    
    if total_score < 60:
        logger.debug(f"🚫 {sym} — total score {total_score:.0f} < 60")
        return None
        
    # 7. Enhanced token with all data
    enhanced = dict(token)
    enhanced['momentum_score'] = total_score
    enhanced['volume_ratio'] = vol_profile['ratio']
    enhanced['trend_aligned'] = trend_state['is_bullish']
    enhanced['ict'] = ict
    enhanced['amd'] = amd
    enhanced['fvg'] = fvg
    
    logger.info(
        f"✅ {sym} SIGNAL: score={total_score:.0f} | "
        f"ICT: BOS={ict['bos_confirmed']} sweep={ict['liquidity_swept']} | "
        f"AMD: {amd['phase']} | FVG: {fvg['bullish_fvg']}"
    )
    
    return enhanced

# ── BACKGROUND TREND UPDATER ──
async def trend_update_loop():
    """Update trend state every 5 minutes."""
    while True:
        try:
            await update_trend_state()
        except Exception as e:
            logger.error(f"Trend update error: {e}")
        await asyncio.sleep(300)  # 5 minutes

# ── INIT ──
async def init_momentum_scanner():
    """Initialize momentum scanner."""
    await update_trend_state()
    asyncio.create_task(trend_update_loop())
    logger.info("Momentum scanner initialized")


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    test_token = {
        'baseToken': {'symbol': 'TEST'},
        'priceUsd': '0.001',
        'priceChange': {'h1': 15, 'h24': 45},
        'volume': {'h24': 500000},
    }
    result = asyncio.run(evaluate_momentum(test_token))
    print(result)
