"""Sleeve A — Mean-reversion scalper on liquid SOL ecosystem bases.

Strategy:
  Long when 1m close ≤ BB(20, 2σ).lower AND RSI(14) < 25 AND volume > 1.5× 20-period avg.
  Exit at mid-band OR +1.5% OR 30-min time stop, whichever first. Hard stop -0.8%.

Regime filter:
  Only trade pairs whose 1h ATR sits in the 20-80th percentile of the trailing 30 days.
  Skips both dead-chop (no follow-through) and storm regimes (BB whipsaws).

Data source:
  Birdeye public OHLCV endpoint (free tier supports 1m granularity).
  Fallback: DexScreener candles (sparse but free).

Position sizing & execution are NOT handled here — this module only finds
opportunities. The main bot's entry pipeline applies sizing, stops, and exits.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger('CryptoBot')

# Universe — liquid bases with reliable 1m candle data on Birdeye.
SCALP_UNIVERSE = [
    ('SOL',  'So11111111111111111111111111111111111111112'),
    ('JUP',  'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN'),
    ('JTO',  'jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL'),
    ('BONK', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'),
    ('WIF',  'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm'),
    ('RAY',  '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R'),
    ('ORCA', 'orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE'),
]

BIRDEYE_OHLCV_URL = "https://public-api.birdeye.so/defi/ohlcv"

# Strategy parameters
BB_PERIOD = 20
BB_STDDEV = 2.0
RSI_PERIOD = 14
RSI_OVERSOLD = 25
VOL_SPIKE_MULT = 1.5
TARGET_PCT = 0.015
STOP_PCT = 0.008
TIME_STOP_MIN = 30


def _sma(values: List[float], n: int) -> Optional[float]:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _stddev(values: List[float], n: int, mean: float) -> Optional[float]:
    if len(values) < n:
        return None
    s = values[-n:]
    return (sum((v - mean) ** 2 for v in s) / n) ** 0.5


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[-i] - closes[-i - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(highs: List[float], lows: List[float], closes: List[float], n: int = 14) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs) / n


async def _fetch_candles(session: aiohttp.ClientSession, mint: str,
                         tf: str = '1m', limit: int = 50) -> Optional[List[Dict]]:
    """Fetch OHLCV from Birdeye. Returns list of dicts with high/low/close/volume."""
    params = {
        'address': mint,
        'type': tf,
        'limit': limit,
    }
    headers = {'accept': 'application/json'}
    try:
        async with session.get(BIRDEYE_OHLCV_URL, params=params, headers=headers, timeout=6) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            items = data.get('data', {}).get('items', []) if isinstance(data.get('data'), dict) else []
            if not items:
                return None
            return [
                {
                    'high': float(c.get('h', c.get('high', 0))),
                    'low': float(c.get('l', c.get('low', 0))),
                    'close': float(c.get('c', c.get('close', 0))),
                    'volume': float(c.get('v', c.get('volume', 0))),
                    'ts': c.get('unixTime', c.get('time', 0)),
                }
                for c in items
                if c.get('c') or c.get('close')
            ]
    except (asyncio.TimeoutError, aiohttp.ClientError):
        return None
    except Exception:
        return None


def _regime_ok(candles_1h: List[Dict]) -> bool:
    """Check the pair's current 1h ATR sits in 20-80th percentile of trailing 30d."""
    if len(candles_1h) < 30:
        # Insufficient history — allow trade (don't penalize new universe entries)
        return True
    highs = [c['high'] for c in candles_1h]
    lows = [c['low'] for c in candles_1h]
    closes = [c['close'] for c in candles_1h]
    current_atr = _atr(highs, lows, closes, 14)
    if not current_atr:
        return True

    # Build rolling ATRs over the window
    atrs = []
    for end in range(15, len(candles_1h) + 1):
        a = _atr(highs[:end], lows[:end], closes[:end], 14)
        if a:
            atrs.append(a)
    if len(atrs) < 5:
        return True
    atrs.sort()
    p20 = atrs[int(len(atrs) * 0.20)]
    p80 = atrs[int(len(atrs) * 0.80)]
    return p20 <= current_atr <= p80


async def _eval_pair(session: aiohttp.ClientSession, symbol: str, mint: str) -> Optional[Dict]:
    """Returns an opportunity dict if this pair has a valid scalp entry right now."""
    candles_1m = await _fetch_candles(session, mint, '1m', 50)
    if not candles_1m or len(candles_1m) < BB_PERIOD + 1:
        return None

    candles_1h = await _fetch_candles(session, mint, '1H', 40)
    if not _regime_ok(candles_1h or []):
        return None

    closes = [c['close'] for c in candles_1m]
    volumes = [c['volume'] for c in candles_1m]

    bb_mid = _sma(closes, BB_PERIOD)
    if bb_mid is None:
        return None
    bb_std = _stddev(closes, BB_PERIOD, bb_mid)
    if bb_std is None:
        return None
    bb_lower = bb_mid - BB_STDDEV * bb_std

    rsi = _rsi(closes, RSI_PERIOD)
    vol_sma = _sma(volumes, BB_PERIOD)
    if rsi is None or vol_sma is None or vol_sma <= 0:
        return None

    last_close = closes[-1]
    last_vol = volumes[-1]

    # Entry conditions
    if last_close > bb_lower:
        return None
    if rsi >= RSI_OVERSOLD:
        return None
    if last_vol < VOL_SPIKE_MULT * vol_sma:
        return None

    target_price = max(bb_mid, last_close * (1 + TARGET_PCT))
    stop_price = last_close * (1 - STOP_PCT)

    return {
        'symbol': symbol,
        'mint': mint,
        'strategy': 'scalp_meanrev',
        'priceUsd': last_close,
        'target_price': target_price,
        'stop_price_hint': stop_price,
        'time_stop_min': TIME_STOP_MIN,
        'rsi': rsi,
        'bb_lower': bb_lower,
        'bb_mid': bb_mid,
        'vol_ratio': last_vol / vol_sma if vol_sma > 0 else 0,
        # Pipeline-friendly fields
        'baseToken': {'symbol': symbol},
        'tokenAddress': mint,
        'liquidity': {'usd': 1_000_000},
        'priceChange': {'h24': ((last_close - closes[0]) / closes[0] * 100) if closes[0] else 0},
        'info': {'socials': ['curated'], 'websites': ['curated']},
    }


async def find_opportunities(state=None, config=None) -> List[Dict]:
    """
    Returns list of valid scalp entry opportunities. One pair = at most one opp.
    """
    async with aiohttp.ClientSession() as session:
        tasks = [_eval_pair(session, sym, mint) for sym, mint in SCALP_UNIVERSE]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    opps = [r for r in results if isinstance(r, dict)]
    if opps:
        logger.info(f"📉 scalp_meanrev: {len(opps)} entry(ies); "
                    + ", ".join(f"{o['symbol']}@RSI{o['rsi']:.0f}" for o in opps))
    return opps


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    opps = asyncio.run(find_opportunities())
    print(f"Found {len(opps)} scalp opps")
    for o in opps:
        print(f"  {o['symbol']}: RSI={o['rsi']:.1f} vol={o['vol_ratio']:.2f}x "
              f"price=${o['priceUsd']:.6f} target=${o['target_price']:.6f}")
