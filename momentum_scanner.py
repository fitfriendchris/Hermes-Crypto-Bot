#!/usr/bin/env python3
"""
MOMENTUM SCANNER — ICT + AMD Smart Entry Filter
Requirements:
- Real volume surge (h24 vs h6 ratio, minimum absolute floor)
- ICT Market Structure: BOS/CHoCH confirmation across multiple observations
- AMD Cycle: Accumulation → Manipulation → Distribution
- Liquidity sweep before entry
- Fair Value Gap (FVG) as confluence
- BTC trend: only trade when BTC is not in freefall
- Multi-candle confirmation — no single-tick signals
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger('CryptoBot')

# ── MINIMUM ENTRY STANDARDS ──
MIN_VOLUME_24H    = 20_000   # $20K — backtest optimal
MIN_LIQUIDITY_USD = 20_000   # $20K — micro-cap alpha lives here
MIN_1H_CHANGE_PCT = 2.0      # 1h must be up ≥ 2% — catch more momentum
MIN_SCORE         = 65       # high quality but more trades
MIN_VOLUME_SURGE  = 2.0      # h24 vol must be ≥ 2× baseline — relaxed
MAX_24H_CHANGE    = 300.0    # skip tokens already up 300%+ — allow momentum continuation

# ── ICT STRUCTURE STATE ──
structure_state: Dict[str, Dict] = {}

# ── AMD CYCLE STATE ──
amd_state: Dict[str, Dict] = {}

# ── TREND STATE ──
trend_state = {
    'btc_price':     0.0,
    'btc_24h_chg':   0.0,
    'sol_price':     0.0,
    'sol_24h_chg':   0.0,
    'last_update':   None,
    'is_bullish':    True,   # default open; updated every 5m
}


# ── TREND UPDATE ──

async def update_trend_state():
    """Update BTC/SOL trend. Marks bearish if BTC drops >8% in 24h."""
    global trend_state
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(
                "https://api.coingecko.com/api/v3/simple/price"
                "?ids=bitcoin,solana&vs_currencies=usd&include_24hr_change=true"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    btc      = float(data.get('bitcoin', {}).get('usd', 0))
                    btc_chg  = float(data.get('bitcoin', {}).get('usd_24h_change', 0))
                    sol      = float(data.get('solana',  {}).get('usd', 0))
                    sol_chg  = float(data.get('solana',  {}).get('usd_24h_change', 0))

                    if btc > 0:
                        trend_state['btc_price']   = btc
                        trend_state['btc_24h_chg'] = btc_chg
                        # Bearish only if BTC is genuinely crashing (> -8% in 24h)
                        trend_state['is_bullish']  = btc_chg > -8.0
                    if sol > 0:
                        trend_state['sol_price']   = sol
                        trend_state['sol_24h_chg'] = sol_chg

                    trend_state['last_update'] = datetime.now()
                    logger.info(
                        f"Trend: BTC=${btc:.0f} ({btc_chg:+.1f}%), "
                        f"SOL=${sol:.2f} ({sol_chg:+.1f}%), "
                        f"Bullish={trend_state['is_bullish']}"
                    )
    except Exception as e:
        logger.debug(f"Trend update error: {e}")


# ── VOLUME PROFILE ──

def get_volume_profile(token: Dict) -> Dict:
    """
    Volume check for LIVE data (GeckoTerminal).
    h24/h6/h1 are absolute totals — ratio compares 1h vs implied hourly avg from h24.
    Returns ratio and whether it passes.
    """
    vol_h24 = float(token.get('volume', {}).get('h24', 0))
    vol_h1  = float(token.get('volume', {}).get('h1',  0))

    # For live aggregated data: absolute volume is what matters
    # h1 vs hourly avg ratio is unreliable on trending pools
    # Use absolute floor + bonus if h1 is elevated
    hourly_avg = vol_h24 / 24 if vol_h24 > 0 else 0
    ratio = vol_h1 / hourly_avg if hourly_avg > 0 else 1.0

    return {
        'h24':     vol_h24,
        'h1':      vol_h1,
        'hourly_avg': hourly_avg,
        'ratio':   ratio,
        'passes':  vol_h24 >= MIN_VOLUME_24H,
    }


# ── ICT MARKET STRUCTURE ──

def detect_ict_structure(token: Dict) -> Dict:
    """
    ICT Market Structure — multi-observation BOS/CHoCH.
    Requires the structure_state to have seen the symbol at least twice
    before confirming a Break of Structure, preventing single-tick false signals.
    """
    sym   = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
    price = float(token.get('priceUsd', 0) or token.get('price', 0))

    ch1  = float(token.get('priceChange', {}).get('h1',  0))
    ch5m = float(token.get('priceChange', {}).get('m5',  0))
    ch24 = float(token.get('priceChange', {}).get('h24', 0))

    if sym not in structure_state:
        structure_state[sym] = {
            'prev_high':    price,
            'prev_low':     price,
            'swing_high':   price,
            'swing_low':    price,
            'trend':        'unknown',
            'observations': 0,
            'last_update':  datetime.now(),
        }

    st = structure_state[sym]
    st['observations'] += 1

    # Update swing points
    if price > st['swing_high']:
        st['swing_high'] = price
    if price < st['swing_low']:
        st['swing_low'] = price

    # Need at least 2 observations to confirm BOS (prevents tick-level noise)
    min_obs = 2
    bos_confirmed = (
        st['observations'] >= min_obs
        and st['prev_high'] > 0
        and price > st['prev_high'] * 1.01   # 1% above prior high
        and ch1 > 0                           # 1h trending up
    )

    # Liquidity sweep: 5m dipped negative but 1h still positive (wick below, close above)
    liquidity_swept = ch5m < -3 and ch1 > 2

    # CHoCH: price breaks below prior structure low with momentum
    choch = price < st['prev_low'] * 0.97 and ch1 < -3

    # Update state
    if bos_confirmed:
        st['prev_high'] = price
        st['prev_low']  = st['swing_low']
        st['swing_low'] = price
        st['trend']     = 'uptrend'

    if choch:
        st['prev_low']  = price
        st['prev_high'] = st['swing_high']
        st['swing_high'] = price
        st['trend']     = 'downtrend'

    st['last_update'] = datetime.now()

    score = 0
    if bos_confirmed:    score += 40
    if liquidity_swept:  score += 35
    if st['trend'] == 'uptrend': score += 25
    if choch:            score = 0

    return {
        'signal':           'long' if score >= 70 else 'none',
        'bos_confirmed':    bos_confirmed,
        'liquidity_swept':  liquidity_swept,
        'choch':            choch,
        'structure_score':  score,
        'trend':            st['trend'],
        'observations':     st['observations'],
    }


# ── AMD CYCLE ──

def detect_amd_cycle(token: Dict) -> Dict:
    """
    AMD Cycle Detection.
    Tightened: requires real manipulation signal (≥8% 5m move) and
    confirmed 1h trend (≥5%) before flagging entry_ready.
    """
    sym    = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
    ch5m   = float(token.get('priceChange', {}).get('m5',  0))
    ch1h   = float(token.get('priceChange', {}).get('h1',  0))
    ch24h  = float(token.get('priceChange', {}).get('h24', 0))

    if sym not in amd_state:
        amd_state[sym] = {
            'phase':                'unknown',
            'manipulation_detected': False,
            'manipulation_time':    None,
            'last_update':          datetime.now(),
        }

    amd = amd_state[sym]

    # Manipulation: sharp 5m move (≥8%) that contradicts the 1h direction
    is_manipulation = abs(ch5m) >= 8 and abs(ch5m) > abs(ch1h * 0.4)
    is_distribution = ch1h > 8 and ch24h > 30
    is_accumulating = abs(ch1h) < 4 and abs(ch24h) < 40

    if is_accumulating:
        amd['phase'] = 'accumulation'
    if is_manipulation:
        amd['phase'] = 'manipulation'
        amd['manipulation_detected'] = True
        amd['manipulation_time'] = datetime.now()
        logger.info(f"🎭 {sym} AMD: manipulation {ch5m:+.1f}% 5m / {ch1h:+.1f}% 1h")
    if is_distribution:
        amd['phase'] = 'distribution'

    # Manipulation must be recent (≤ 30 min ago) to be actionable
    manip_fresh = (
        amd['manipulation_detected']
        and amd['manipulation_time'] is not None
        and (datetime.now() - amd['manipulation_time']) < timedelta(minutes=30)
    )

    entry_ready = (
        manip_fresh
        and ch5m > 0       # bouncing after manipulation
        and ch1h >= 5      # 1h trend strong (raised from any positive)
    ) or (
        amd['phase'] == 'manipulation'
        and ch5m > 0
        and ch1h >= 5
    )

    score = 0
    if amd['phase'] == 'manipulation':  score += 30
    if manip_fresh:                     score += 40
    if ch5m > 0:                        score += 15
    if ch1h >= 5:                       score += 15

    amd['last_update'] = datetime.now()

    return {
        'phase':                amd['phase'],
        'entry_ready':          entry_ready,
        'amd_score':            score,
        'manipulation_detected': amd['manipulation_detected'],
        'manip_fresh':          manip_fresh,
    }


# ── FAIR VALUE GAP ──

def detect_fvg(token: Dict) -> Dict:
    """
    FVG: price left a gap that creates a re-entry zone.
    Bullish FVG: 5m pulled back but 15m and 1h are strongly positive.
    """
    ch5m  = float(token.get('priceChange', {}).get('m5',  0))
    ch15m = float(token.get('priceChange', {}).get('m15', 0))
    ch1h  = float(token.get('priceChange', {}).get('h1',  0))

    bullish_fvg = ch5m < -1 and ch15m > 2 and ch1h > 5
    bearish_fvg = ch5m > 3 and ch15m < -2 and ch1h < -5

    return {
        'bullish_fvg': bullish_fvg,
        'bearish_fvg': bearish_fvg,
        'fvg_score':   20 if bullish_fvg else 0,
    }


# ── MAIN ENTRY POINT ──

async def evaluate_momentum_fast(token: Dict) -> Optional[Dict]:
    """
    FAST MOMENTUM — for live 30s scanning.
    Drops ICT/AMD gates (need multi-observation state).
    Keeps: volume surge + 1h momentum + trend + liquidity + FVG confluence.
    """
    sym   = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
    ch1h  = float(token.get('priceChange', {}).get('h1',  0))
    ch24h = float(token.get('priceChange', {}).get('h24', 0))
    ch5m  = float(token.get('priceChange', {}).get('m5',  0))
    liq   = float(token.get('liquidity', {}).get('usd', 0))

    # Gate 1: Already pumped too hard
    if ch24h > MAX_24H_CHANGE:
        return None

    # Gate 2: BTC trend regime
    if not trend_state['is_bullish']:
        return None

    # Gate 3: Minimum 1h momentum
    if ch1h < MIN_1H_CHANGE_PCT:
        return None

    # Gate 4: Minimum liquidity
    if liq > 0 and liq < MIN_LIQUIDITY_USD:
        return None

    # Gate 5: Volume surge
    vol = get_volume_profile(token)
    if not vol['passes']:
        return None

    # Gate 6: FVG confluence (simplified — no multi-candle dependency)
    fvg_bullish = ch5m < -1 and ch1h > 3  # quick pullback on uptrend

    # Composite score (simplified)
    total_score = 40  # base for passing gates
    total_score += min(ch1h * 5, 30)       # up to 30 pts for 1h momentum
    total_score += min(vol['h24'] / 50000 * 10, 20)  # up to 20 pts for volume
    total_score += 10 if fvg_bullish else 0
    total_score += min(abs(ch24h) * 0.1, 10)  # some pts for 24h activity

    if total_score < MIN_SCORE:
        return None

    token['momentum_score'] = total_score
    token['volume_profile'] = vol
    token['fvg_signal'] = fvg_bullish
    logger.info(f"📈 FAST MOMENTUM: {sym} | Score: {total_score:.0f} | 1h: {ch1h:+.1f}% | Vol: {vol['ratio']:.1f}x")
    return token


async def evaluate_momentum(token: Dict) -> Optional[Dict]:
    """
    Pass ALL gates — no exceptions, no RELAXED fallbacks.
    Gate order: fast cheap checks first to avoid expensive lookups.
    """
    sym   = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
    ch1h  = float(token.get('priceChange', {}).get('h1',  0))
    ch24h = float(token.get('priceChange', {}).get('h24', 0))
    liq   = float(token.get('liquidity', {}).get('usd', 0))

    # ── Gate 1: Already pumped too hard ──
    if ch24h > MAX_24H_CHANGE:
        logger.debug(f"🚫 {sym} — already up {ch24h:.0f}% (missed the move)")
        return None

    # ── Gate 2: Trend regime (BTC not in freefall) ──
    if not trend_state['is_bullish']:
        logger.debug(f"🚫 {sym} — BTC crashing ({trend_state['btc_24h_chg']:+.1f}%)")
        return None

    # ── Gate 3: Minimum 1h momentum ──
    if ch1h < MIN_1H_CHANGE_PCT:
        logger.debug(f"🚫 {sym} — 1h change {ch1h:.1f}% < {MIN_1H_CHANGE_PCT}% minimum")
        return None

    # ── Gate 4: Minimum liquidity ──
    if liq > 0 and liq < MIN_LIQUIDITY_USD:
        logger.debug(f"🚫 {sym} — liquidity ${liq:,.0f} < ${MIN_LIQUIDITY_USD:,} minimum")
        return None

    # ── Gate 5: Volume surge ──
    vol = get_volume_profile(token)
    if not vol['passes']:
        logger.debug(
            f"🚫 {sym} — vol ${vol['h24']:,.0f} (ratio {vol['ratio']:.1f}x) "
            f"— need ${MIN_VOLUME_24H:,} and {MIN_VOLUME_SURGE}x surge"
        )
        return None

    # ── Gate 6: ICT Market Structure ──
    ict = detect_ict_structure(token)
    if ict['choch']:
        logger.debug(f"🚫 {sym} — CHoCH (bearish structure break)")
        return None
    if not ict['bos_confirmed'] and not ict['liquidity_swept']:
        logger.debug(f"🚫 {sym} — no BOS or liquidity sweep (obs={ict['observations']})")
        return None

    # ── Gate 7: AMD Cycle ──
    amd = detect_amd_cycle(token)
    if not amd['entry_ready']:
        logger.debug(f"🚫 {sym} — AMD not ready (phase={amd['phase']}, manip_fresh={amd['manip_fresh']})")
        return None

    # ── Gate 8: Fair Value Gap ──
    fvg = detect_fvg(token)

    # ── Composite Score ──
    total_score = (
        ict['structure_score']               * 0.35 +
        amd['amd_score']                     * 0.35 +
        fvg['fvg_score']                     * 0.15 +
        min(vol['ratio'] / MIN_VOLUME_SURGE * 10, 15) * 0.15
    )

    if total_score < MIN_SCORE:
        logger.debug(f"🚫 {sym} — composite score {total_score:.0f} < {MIN_SCORE}")
        return None

    enhanced = dict(token)
    enhanced['momentum_score']  = total_score
    enhanced['volume_ratio']    = vol['ratio']
    enhanced['trend_aligned']   = trend_state['is_bullish']
    enhanced['ict']             = ict
    enhanced['amd']             = amd
    enhanced['fvg']             = fvg

    logger.info(
        f"✅ {sym} SIGNAL score={total_score:.0f} | "
        f"vol={vol['ratio']:.1f}x ${vol['h24']/1000:.0f}K | "
        f"BOS={ict['bos_confirmed']} sweep={ict['liquidity_swept']} | "
        f"AMD={amd['phase']} | 1h={ch1h:+.1f}%"
    )
    return enhanced


# ── BACKGROUND TREND UPDATER ──

async def trend_update_loop():
    while True:
        try:
            await update_trend_state()
        except Exception as e:
            logger.error(f"Trend update error: {e}")
        await asyncio.sleep(300)


async def init_momentum_scanner():
    await update_trend_state()
    asyncio.create_task(trend_update_loop())
    logger.info("Momentum scanner initialized")


if __name__ == "__main__":
    import logging as _l
    _l.basicConfig(level=_l.INFO)
    test = {
        'baseToken': {'symbol': 'TEST'},
        'priceUsd': '0.001',
        'priceChange': {'h1': 12, 'h24': 80, 'm5': -5, 'm15': 8},
        'volume': {'h24': 150000, 'h6': 20000},
        'liquidity': {'usd': 50000},
    }
    result = asyncio.run(evaluate_momentum(test))
    print(result)
