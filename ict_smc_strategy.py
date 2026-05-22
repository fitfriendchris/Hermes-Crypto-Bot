"""
ICT/SMC Strategy with SMT + AMD
Author: Hermes | May 2026

Core Concepts:
- ICT: Inner Circle Trader concepts (liquidity, fair value gaps, order blocks)
- SMC: Smart Money Concepts (institutional order flow)
- SMT: Smart Money Tool (divergence between correlated assets)
- AMD: Accumulation, Manipulation, Distribution (market maker model)

DXY Hedge: When DXY moves opposite to BTC, hedge accordingly.
DXY up = USD strength = BTC down (inverse correlation ~-0.7)
"""

import asyncio
import logging
import numpy as np
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List

import aiohttp

logger = logging.getLogger('CryptoBot')

# ── DATA ──
COINGECKO_BTC = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days=1"
COINGECKO_DXY = "https://api.coingecko.com/api/v3/coins/usd-coin/ohlc?vs_currency=eur&days=1"  # Proxy for DXY


@dataclass
class ICTSignal:
    direction: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    fvg_confluence: bool
    liq_sweep: bool
    smt_divergence: bool
    amd_phase: str
    dxy_correlation: float
    reason: str


class ICTDataFeed:
    """Fetch 15m BTC + DXY proxy data."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._btc_cache: List[Dict] = []
        self._dxy_cache: List[Dict] = []
        self._cache_ts: float = 0
        self._cache_ttl = 60

    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_btc_ohlc(self) -> Optional[List[Dict]]:
        now = datetime.now().timestamp()
        if now - self._cache_ts < self._cache_ttl and self._btc_cache:
            return self._btc_cache

        try:
            async with self._session.get(COINGECKO_BTC, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = []
                    for c in data:
                        candles.append({
                            'timestamp': int(c[0]),
                            'open': float(c[1]),
                            'high': float(c[2]),
                            'low': float(c[3]),
                            'close': float(c[4]),
                        })
                    self._btc_cache = candles
                    self._cache_ts = now
                    return candles
        except Exception as e:
            logger.warning(f"BTC OHLC failed: {e}")
        return None

    async def get_dxy_proxy(self) -> Optional[List[Dict]]:
        """Use USDC/EUR as DXY proxy (inverse of DXY direction)."""
        now = datetime.now().timestamp()
        if now - self._cache_ts < self._cache_ttl and self._dxy_cache:
            return self._dxy_cache

        try:
            async with self._session.get(COINGECKO_DXY, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candles = []
                    for c in data:
                        candles.append({
                            'timestamp': int(c[0]),
                            'open': float(c[1]),
                            'high': float(c[2]),
                            'low': float(c[3]),
                            'close': float(c[4]),
                        })
                    self._dxy_cache = candles
                    return candles
        except Exception as e:
            logger.warning(f"DXY proxy failed: {e}")
        return None


class ICTAnalyzer:
    """
    ICT/SMC Analysis Engine
    
    Key concepts:
    1. LIQUIDITY SWEEP: Price takes out previous high/low, then reverses
    2. ORDER BLOCK: Last bearish/bullish candle before a strong move
    3. FAIR VALUE GAP (FVG): Imbalance zone between candles
    4. SMT: BTC makes new high but DXY doesn't = divergence
    5. AMD: Accumulation (range) -> Manipulation (sweep) -> Distribution (trend)
    """

    def __init__(self, feed: ICTDataFeed):
        self.feed = feed

    # ── ICT PATTERNS ──
    def detect_liquidity_sweep(self, candles: List[Dict], lookback: int = 10) -> Dict:
        """
        Detect if price swept liquidity (took out previous high/low) then reversed.
        Returns: {'swept_high': bool, 'swept_low': bool, 'reversal_strength': float}
        """
        if len(candles) < lookback + 2:
            return {'swept_high': False, 'swept_low': False, 'strength': 0}

        recent = candles[-lookback-1:]
        prev_high = max(c['high'] for c in recent[:-1])
        prev_low = min(c['low'] for c in recent[:-1])
        
        current = candles[-1]
        
        swept_high = current['high'] > prev_high and current['close'] < prev_high
        swept_low = current['low'] < prev_low and current['close'] > prev_low
        
        # Reversal strength: how far did it reverse?
        if swept_high:
            strength = (current['high'] - current['close']) / (current['high'] - prev_high)
        elif swept_low:
            strength = (current['close'] - current['low']) / (prev_low - current['low'])
        else:
            strength = 0
            
        return {
            'swept_high': swept_high,
            'swept_low': swept_low,
            'strength': min(1.0, strength),
        }

    def detect_fvg(self, candles: List[Dict]) -> List[Dict]:
        """
        Detect Fair Value Gaps (imbalances).
        Bullish FVG: current low > previous high (gap up)
        Bearish FVG: current high < previous low (gap down)
        Returns list of active FVGs.
        """
        if len(candles) < 3:
            return []
        
        fvgs = []
        for i in range(2, len(candles)):
            c0 = candles[i-2]  # 2 bars ago
            c1 = candles[i-1]  # 1 bar ago
            c2 = candles[i]     # current
            
            # Bullish FVG: c2 low > c0 high (gap up)
            if c2['low'] > c0['high']:
                fvgs.append({
                    'type': 'bullish',
                    'top': c2['low'],
                    'bottom': c0['high'],
                    'timestamp': c2['timestamp'],
                    'mid': (c2['low'] + c0['high']) / 2,
                })
            
            # Bearish FVG: c2 high < c0 low (gap down)
            if c2['high'] < c0['low']:
                fvgs.append({
                    'type': 'bearish',
                    'top': c0['low'],
                    'bottom': c2['high'],
                    'timestamp': c2['timestamp'],
                    'mid': (c0['low'] + c2['high']) / 2,
                })
        
        return fvgs

    def detect_order_block(self, candles: List[Dict], lookback: int = 5) -> Optional[Dict]:
        """
        Detect Order Block (last opposite candle before a strong move).
        Bullish OB: last bearish candle before strong up move
        Bearish OB: last bullish candle before strong down move
        """
        if len(candles) < lookback + 3:
            return None

        recent = candles[-lookback:]
        
        # Find strong move
        move_pct = (recent[-1]['close'] - recent[0]['open']) / recent[0]['open']
        
        if abs(move_pct) < 0.005:  # Need 0.5%+ move
            return None
        
        is_bullish_move = move_pct > 0
        
        # Find last opposite candle
        for i in range(len(recent) - 2, -1, -1):
            c = recent[i]
            is_bearish = c['close'] < c['open']
            is_bullish = c['close'] > c['open']
            
            if is_bullish_move and is_bearish:
                return {
                    'type': 'bullish',
                    'high': c['high'],
                    'low': c['low'],
                    'open': c['open'],
                    'close': c['close'],
                    'timestamp': c['timestamp'],
                }
            elif not is_bullish_move and is_bullish:
                return {
                    'type': 'bearish',
                    'high': c['high'],
                    'low': c['low'],
                    'open': c['open'],
                    'close': c['close'],
                    'timestamp': c['timestamp'],
                }
        
        return None

    def detect_amd(self, candles: List[Dict], lookback: int = 20) -> Dict:
        """
        Detect AMD (Accumulation, Manipulation, Distribution) phase.
        
        Accumulation: Price in range, low volatility
        Manipulation: Sweep of range high/low, then reversal
        Distribution: Strong trend out of range
        """
        if len(candles) < lookback:
            return {'phase': 'none', 'confidence': 0}

        recent = candles[-lookback:]
        highs = [c['high'] for c in recent]
        lows = [c['low'] for c in recent]
        closes = [c['close'] for c in recent]
        
        range_high = max(highs)
        range_low = min(lows)
        range_size = (range_high - range_low) / np.mean(closes)
        
        # Volatility
        ranges = [c['high'] - c['low'] for c in recent]
        avg_range = np.mean(ranges)
        volatility = np.std(ranges) / avg_range if avg_range > 0 else 0
        
        # Trend strength
        first_close = closes[0]
        last_close = closes[-1]
        trend_pct = (last_close - first_close) / first_close
        
        # Detect phase
        phase = 'none'
        confidence = 0
        
        if range_size < 0.02 and volatility < 0.5:  # 2% range, low vol
            phase = 'accumulation'
            confidence = 50 + (0.5 - volatility) * 100
        elif abs(trend_pct) > 0.03:  # 3%+ trend
            phase = 'distribution'
            confidence = min(100, abs(trend_pct) * 1000)
        else:
            # Check for manipulation (sweep + reversal)
            sweep = self.detect_liquidity_sweep(candles, 5)
            if sweep['swept_high'] or sweep['swept_low']:
                if sweep['strength'] > 0.3:
                    phase = 'manipulation'
                    confidence = sweep['strength'] * 100
        
        return {
            'phase': phase,
            'confidence': min(100, confidence),
            'range_high': range_high,
            'range_low': range_low,
            'range_size_pct': range_size * 100,
            'trend_pct': trend_pct * 100,
        }

    def detect_smt(self, btc: List[Dict], dxy: List[Dict]) -> Dict:
        """
        SMT (Smart Money Tool) Divergence.
        If BTC makes new high but DXY doesn't fall = bearish divergence for BTC
        If BTC makes new low but DXY doesn't rise = bullish divergence for BTC
        
        Note: DXY inverse to BTC. DXY up = USD strong = BTC down.
        """
        if len(btc) < 10 or len(dxy) < 10:
            return {'bullish_divergence': False, 'bearish_divergence': False, 'correlation': 0}
        
        # Recent highs/lows
        btc_highs = [c['high'] for c in btc[-10:]]
        btc_lows = [c['low'] for c in btc[-10:]]
        dxy_highs = [c['high'] for c in dxy[-10:]]
        dxy_lows = [c['low'] for c in dxy[-10:]]
        
        btc_high_now = btc_highs[-1]
        btc_high_prev = max(btc_highs[:-1])
        btc_low_now = btc_lows[-1]
        btc_low_prev = min(btc_lows[:-1])
        
        dxy_high_now = dxy_highs[-1]
        dxy_high_prev = max(dxy_highs[:-1])
        dxy_low_now = dxy_lows[-1]
        dxy_low_prev = min(dxy_lows[:-1])
        
        # Correlation
        btc_closes = [c['close'] for c in btc[-10:]]
        dxy_closes = [c['close'] for c in dxy[-10:]]
        correlation = np.corrcoef(btc_closes, dxy_closes)[0, 1] if len(btc_closes) > 1 else 0
        
        # SMT: BTC new high, DXY not making new low (should if inverse correlation)
        # This means USD is not strengthening despite BTC pumping = fake pump
        bearish_smt = btc_high_now > btc_high_prev and dxy_low_now > dxy_low_prev * 0.999
        
        # SMT: BTC new low, DXY not making new high (should if inverse correlation)
        # This means USD is not weakening despite BTC dumping = fake dump
        bullish_smt = btc_low_now < btc_low_prev and dxy_high_now < dxy_high_prev * 1.001
        
        return {
            'bullish_divergence': bullish_smt,
            'bearish_divergence': bearish_smt,
            'correlation': correlation,
        }


class ICTStrategy:
    """
    Main ICT/SMC strategy combining:
    1. Liquidity sweeps (taking out stops)
    2. Order blocks (institutional levels)
    3. FVG confluence (imbalance zones)
    4. SMT divergence (correlated asset divergence)
    5. AMD phase (accumulation/manipulation/distribution)
    6. DXY hedge (inverse correlation)
    """

    def __init__(self):
        self.feed = ICTDataFeed()
        self.analyzer = ICTAnalyzer(self.feed)

    async def initialize(self):
        await self.feed.initialize()

    async def close(self):
        await self.feed.close()

    async def get_signal(self, balance: float = 100.0) -> Optional[ICTSignal]:
        """Generate ICT/SMC signal with DXY hedge."""
        # Fetch data
        btc = await self.feed.get_btc_ohlc()
        dxy = await self.feed.get_dxy_proxy()
        
        if not btc or len(btc) < 20:
            return None

        current_price = btc[-1]['close']
        
        # ── ICT ANALYSIS ──
        sweep = self.analyzer.detect_liquidity_sweep(btc)
        fvgs = self.analyzer.detect_fvg(btc)
        ob = self.analyzer.detect_order_block(btc)
        amd = self.analyzer.detect_amd(btc)
        
        # ── SMT ANALYSIS ──
        smt = {'bullish_divergence': False, 'bearish_divergence': False, 'correlation': 0}
        if dxy:
            smt = self.analyzer.detect_smt(btc, dxy)
        
        # ── SIGNAL GENERATION ──
        direction = None
        confidence = 0
        reasons = []
        
        # LONG conditions
        if sweep['swept_low'] and sweep['strength'] > 0.3:
            # Price swept liquidity below, now reversing up
            confidence += 25
            reasons.append(f"liquidity sweep low (strength: {sweep['strength']:.0%})")
            
            # Check FVG confluence
            bullish_fvgs = [f for f in fvgs if f['type'] == 'bullish']
            if bullish_fvgs:
                nearest_fvg = min(bullish_fvgs, key=lambda f: abs(f['mid'] - current_price))
                if abs(nearest_fvg['mid'] - current_price) / current_price < 0.005:
                    confidence += 15
                    reasons.append(f"bullish FVG confluence at ${nearest_fvg['mid']:,.0f}")
            
            # Check SMT
            if smt['bullish_divergence']:
                confidence += 20
                reasons.append("SMT bullish divergence (BTC low, DXY not high)")
            
            # Check AMD
            if amd['phase'] in ['manipulation', 'accumulation']:
                confidence += 10
                reasons.append(f"AMD phase: {amd['phase']}")
            
            # Check DXY hedge
            if smt['correlation'] < -0.5:
                # Strong inverse correlation, DXY should support BTC move
                confidence += 10
                reasons.append(f"DXY inverse corr: {smt['correlation']:.2f}")
            
            direction = 'long'
        
        # SHORT conditions
        elif sweep['swept_high'] and sweep['strength'] > 0.3:
            # Price swept liquidity above, now reversing down
            confidence += 25
            reasons.append(f"liquidity sweep high (strength: {sweep['strength']:.0%})")
            
            # Check FVG confluence
            bearish_fvgs = [f for f in fvgs if f['type'] == 'bearish']
            if bearish_fvgs:
                nearest_fvg = min(bearish_fvgs, key=lambda f: abs(f['mid'] - current_price))
                if abs(nearest_fvg['mid'] - current_price) / current_price < 0.005:
                    confidence += 15
                    reasons.append(f"bearish FVG confluence at ${nearest_fvg['mid']:,.0f}")
            
            # Check SMT
            if smt['bearish_divergence']:
                confidence += 20
                reasons.append("SMT bearish divergence (BTC high, DXY not low)")
            
            # Check AMD
            if amd['phase'] in ['manipulation', 'distribution']:
                confidence += 10
                reasons.append(f"AMD phase: {amd['phase']}")
            
            # Check DXY hedge
            if smt['correlation'] < -0.5:
                confidence += 10
                reasons.append(f"DXY inverse corr: {smt['correlation']:.2f}")
            
            direction = 'short'
        
        if not direction or confidence < 50:
            return None
        
        # Calculate trade plan
        # Stop: beyond the sweep level
        if direction == 'long':
            stop = sweep['swept_low'] and btc[-1]['low'] * 0.995 or current_price * 0.98
            tp = current_price * 1.03  # 3:1 R:R minimum
        else:
            stop = sweep['swept_high'] and btc[-1]['high'] * 1.005 or current_price * 1.02
            tp = current_price * 0.97
        
        return ICTSignal(
            direction=direction,
            confidence=min(100, confidence),
            entry_price=current_price,
            stop_loss=stop,
            take_profit=tp,
            fvg_confluence=any('FVG' in r for r in reasons),
            liq_sweep=sweep['swept_high'] or sweep['swept_low'],
            smt_divergence=smt['bullish_divergence'] or smt['bearish_divergence'],
            amd_phase=amd['phase'],
            dxy_correlation=smt['correlation'],
            reason=" | ".join(reasons),
        )


async def check_ict_signal(balance: float = 100.0) -> Optional[Dict]:
    """Quick check function for main bot loop."""
    strategy = ICTStrategy()
    await strategy.initialize()
    try:
        signal = await strategy.get_signal(balance)
        if signal and signal.confidence >= 50:
            return {
                'direction': signal.direction,
                'confidence': signal.confidence,
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit': signal.take_profit,
                'fvg_confluence': signal.fvg_confluence,
                'liq_sweep': signal.liq_sweep,
                'smt_divergence': signal.smt_divergence,
                'amd_phase': signal.amd_phase,
                'dxy_correlation': signal.dxy_correlation,
                'reason': signal.reason,
            }
    finally:
        await strategy.close()
    return None


if __name__ == '__main__':
    async def test():
        sig = await check_ict_signal(87.0)
        if sig:
            print(f"🚨 ICT SIGNAL: {sig['direction'].upper()} | Confidence: {sig['confidence']:.0f}%")
            print(f"   Entry: ${sig['entry_price']:,.0f}")
            print(f"   Stop: ${sig['stop_loss']:,.0f}")
            print(f"   TP: ${sig['take_profit']:,.0f}")
            print(f"   SMT: {sig['smt_divergence']} | AMD: {sig['amd_phase']} | DXY corr: {sig['dxy_correlation']:.2f}")
            print(f"   Reason: {sig['reason']}")
        else:
            print("No ICT signal right now")

    asyncio.run(test())
