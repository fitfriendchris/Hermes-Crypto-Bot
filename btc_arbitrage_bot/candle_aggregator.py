"""
Multi-timeframe candle aggregator with VWAP + momentum calculations.
Builds 5m, 15m, and 1d Japanese candlesticks from tick stream.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional
from collections import deque

import numpy as np

logger = logging.getLogger("CANDLES")


@dataclass
class Candle:
    """Standard OHLCV + metadata."""
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: float          # Unix epoch of candle open
    timeframe: str            # '5m', '15m', '1d'
    vwap: Decimal = Decimal("0")
    roc: float = 0.0          # Rate of change vs previous close
    ticks: int = 0


@dataclass
class _PartialCandle:
    """Mutable accumulator for the current incomplete candle."""
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    timestamp: float
    tick_count: int = 0
    cumulative_pv: Decimal = Decimal("0")   # price * volume for VWAP


class CandleAggregator:
    """
    Async candle builder.
    - Ingests ticks, accumulates into open candles
    - Emits closed candles via queue for downstream consumers
    - Tracks last N candles for momentum metrics
    """

    TF_SECONDS = {
        "5m": 300,
        "15m": 900,
        "1d": 86400,
    }

    def __init__(self, cfg):
        self.cfg = cfg
        self._partial: Dict[str, _PartialCandle] = {}
        self._closed_queue: asyncio.Queue[List[Candle]] = asyncio.Queue()
        self._history: Dict[str, deque] = {tf: deque(maxlen=100) for tf in cfg.timeframes}
        self._last_close: Dict[str, Decimal] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest_tick(self, tick) -> None:
        """Route a tick into all relevant timeframes."""
        now = tick.timestamp
        for tf in self.cfg.timeframes:
            await self._accumulate(tf, now, tick.price, tick.qty)

    async def wait_for_close(self) -> List[Candle]:
        """Blocking consumer: returns list of candles that just closed."""
        return await self._closed_queue.get()

    def latest_candles(self, timeframe: str, n: int = 2) -> List[Candle]:
        """Return last N closed candles for a timeframe."""
        hist = self._history.get(timeframe, deque())
        return list(hist)[-n:]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _accumulate(self, tf: str, now: float, price: Decimal, qty: Decimal) -> None:
        sec = self.TF_SECONDS[tf]
        bucket_ts = (int(now) // sec) * sec

        partial = self._partial.get(tf)

        # New candle bucket started
        if partial is None or partial.timestamp != bucket_ts:
            if partial is not None:
                await self._close_candle(tf, partial)
            self._partial[tf] = _PartialCandle(
                open=price,
                high=price,
                low=price,
                close=price,
                volume=qty,
                timestamp=bucket_ts,
                cumulative_pv=price * qty,
            )
            return

        # Accumulate into existing
        partial.high = max(partial.high, price)
        partial.low = min(partial.low, price)
        partial.close = price
        partial.volume += qty
        partial.cumulative_pv += price * qty
        partial.tick_count += 1

    async def _close_candle(self, tf: str, partial: _PartialCandle) -> None:
        vwap = (
            partial.cumulative_pv / partial.volume
            if partial.volume > 0
            else partial.close
        )
        prev = self._last_close.get(tf)
        roc = (
            float((partial.close - prev) / prev) * 100
            if prev and prev > 0
            else 0.0
        )

        candle = Candle(
            open=partial.open,
            high=partial.high,
            low=partial.low,
            close=partial.close,
            volume=partial.volume,
            timestamp=partial.timestamp,
            timeframe=tf,
            vwap=vwap.quantize(Decimal("0.01")),
            roc=roc,
            ticks=partial.tick_count,
        )

        self._last_close[tf] = partial.close
        self._history[tf].append(candle)
        await self._closed_queue.put([candle])
        logger.info(f"Closed {tf} candle @ {candle.close} | VWAP {candle.vwap} | ROC {roc:.3f}%")
