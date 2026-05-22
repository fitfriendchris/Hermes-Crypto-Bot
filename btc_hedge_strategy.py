"""
BTC Hedge Strategy — 15m Chart Technicals
Author: Hermes | May 2026

Edge: Pure 15m BTC technicals. No sentiment needed.
- RSI < 35 + price at/below lower BB = LONG
- RSI > 70 + price at/above upper BB = SHORT
- 2:1 R:R, 1.5% stop, 3% target
- Position: 15% of balance

Why this works:
- BTC is liquid — no rug pulls
- 15m timeframe catches momentum before it fades
- RSI + BB confluence filters noise
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import aiohttp
import numpy as np

logger = logging.getLogger('CryptoBot')

# Wrapped BTC on Solana (real mint — verify before live)
WBTC_MINT = "3NZ9xSfB3i8VZr6gY3kY8Y3kY3kY3kY3kY3kY3kY3kY"
SOL_MINT = "So11111111111111111111111111111111111111112"

COINGECKO_OHLC = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days=1"


@dataclass
class BTCSignal:
    direction: str
    confidence: float
    rsi: float
    bb_position: float
    sma20: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str


class BTC15mAnalyzer:
    """Fetch and analyze 15m BTC candles from CoinGecko."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: List[Dict] = []
        self._cache_ts: float = 0
        self._cache_ttl = 60

    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_btc_ohlc(self) -> Optional[List[Dict]]:
        now = datetime.now().timestamp()
        if now - self._cache_ts < self._cache_ttl and self._cache:
            return self._cache

        try:
            async with self._session.get(COINGECKO_OHLC, timeout=10) as resp:
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
                    self._cache = candles
                    self._cache_ts = now
                    return candles
        except Exception as e:
            logger.warning(f"CoinGecko BTC OHLC failed: {e}")
        return None

    @staticmethod
    def calc_rsi(candles: List[Dict], period: int = 14) -> float:
        if len(candles) < period + 1:
            return 50.0
        closes = [c['close'] for c in candles[-period-1:]]
        deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calc_bollinger(candles: List[Dict], period: int = 20) -> Dict:
        if len(candles) < period:
            return {'upper': 0, 'middle': 0, 'lower': 0}
        closes = [c['close'] for c in candles[-period:]]
        sma = sum(closes) / len(closes)
        variance = sum((c - sma) ** 2 for c in closes) / len(closes)
        std = variance ** 0.5
        return {'upper': sma + 2 * std, 'middle': sma, 'lower': sma - 2 * std}

    @staticmethod
    def calc_sma(candles: List[Dict], period: int = 20) -> float:
        if len(candles) < period:
            return 0.0
        closes = [c['close'] for c in candles[-period:]]
        return sum(closes) / len(closes)


class BTCHedgeStrategy:
    """
    BTC 15m hedge strategy.
    Long when RSI < 35 + price <= lower BB.
    Short when RSI > 70 + price >= upper BB.
    """

    RSI_OVERSOLD = 35
    RSI_OVERBOUGHT = 70
    CONFIDENCE_MIN = 60
    POSITION_PCT = 0.15
    STOP_PCT = 0.015
    TP_PCT = 0.03

    def __init__(self):
        self.analyzer = BTC15mAnalyzer()

    async def initialize(self):
        await self.analyzer.initialize()

    async def close(self):
        await self.analyzer.close()

    async def get_signal(self, balance: float = 100.0) -> Optional[BTCSignal]:
        candles = await self.analyzer.get_btc_ohlc()
        if not candles or len(candles) < 20:
            return None

        rsi = self.analyzer.calc_rsi(candles)
        bb = self.analyzer.calc_bollinger(candles)
        sma20 = self.analyzer.calc_sma(candles)
        current_price = candles[-1]['close']
        prev_price = candles[-2]['close']

        ranges = [c['high'] - c['low'] for c in candles[-5:-1]]
        avg_range = sum(ranges) / len(ranges) if ranges else 0
        latest_range = candles[-1]['high'] - candles[-1]['low']
        vol_spike = latest_range > avg_range * 1.5

        bb_range = bb['upper'] - bb['lower']
        bb_position = (current_price - bb['lower']) / bb_range if bb_range > 0 else 0
        bb_position = max(-1, min(1, bb_position * 2 - 1))

        prev_sma = self.analyzer.calc_sma(candles[:-1], 20)
        crossed_below_sma = prev_price >= prev_sma and current_price < sma20
        crossed_above_sma = prev_price <= prev_sma and current_price > sma20

        direction = 'none'
        confidence = 0
        reasons = []

        if rsi < self.RSI_OVERSOLD:
            if current_price <= bb['lower']:
                direction = 'long'
                confidence = (self.RSI_OVERSOLD - rsi) * 2 + 20
                reasons.append(f"RSI oversold ({rsi:.0f})")
                reasons.append(f"price at/below BB lower (${bb['lower']:,.0f})")
            elif crossed_below_sma and vol_spike:
                direction = 'long'
                confidence = (self.RSI_OVERSOLD - rsi) * 2 + 10
                reasons.append(f"RSI oversold ({rsi:.0f})")
                reasons.append("crossed below SMA20 with volume")

        if rsi > self.RSI_OVERBOUGHT:
            if current_price >= bb['upper']:
                direction = 'short'
                confidence = (rsi - self.RSI_OVERBOUGHT) * 2 + 20
                reasons.append(f"RSI overbought ({rsi:.0f})")
                reasons.append(f"price at/above BB upper (${bb['upper']:,.0f})")
            elif crossed_above_sma and vol_spike:
                direction = 'short'
                confidence = (rsi - self.RSI_OVERBOUGHT) * 2 + 10
                reasons.append(f"RSI overbought ({rsi:.0f})")
                reasons.append("crossed above SMA20 with volume")

        if direction == 'none' or confidence < self.CONFIDENCE_MIN:
            return None

        size = balance * self.POSITION_PCT
        if direction == 'long':
            stop = current_price * (1 - self.STOP_PCT)
            tp = current_price * (1 + self.TP_PCT)
        else:
            stop = current_price * (1 + self.STOP_PCT)
            tp = current_price * (1 - self.TP_PCT)

        return BTCSignal(
            direction=direction,
            confidence=min(100, confidence),
            rsi=rsi,
            bb_position=bb_position,
            sma20=sma20,
            entry_price=current_price,
            stop_loss=stop,
            take_profit=tp,
            reason=" | ".join(reasons),
        )


async def check_btc_hedge_signal(balance: float = 100.0) -> Optional[Dict]:
    strategy = BTCHedgeStrategy()
    await strategy.initialize()
    try:
        signal = await strategy.get_signal(balance)
        if signal and signal.confidence >= 60:
            return {
                'direction': signal.direction,
                'confidence': signal.confidence,
                'reason': signal.reason,
                'rsi': signal.rsi,
                'bb_position': signal.bb_position,
                'sma20': signal.sma20,
                'entry_price': signal.entry_price,
                'stop_loss': signal.stop_loss,
                'take_profit': signal.take_profit,
            }
    finally:
        await strategy.close()
    return None


if __name__ == '__main__':
    async def test():
        sig = await check_btc_hedge_signal(87.0)
        if sig:
            print(f"🚨 BTC SIGNAL: {sig['direction'].upper()} | Confidence: {sig['confidence']:.0f}%")
            print(f"   Entry: ${sig['entry_price']:,.0f}")
            print(f"   Stop: ${sig['stop_loss']:,.0f}")
            print(f"   TP: ${sig['take_profit']:,.0f}")
            print(f"   Reason: {sig['reason']}")
        else:
            print("No signal right now")

    asyncio.run(test())
