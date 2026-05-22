#!/usr/bin/env python3
"""
BTC Discrepancy Arbitrage Bot — Polymarket vs Spot Exchanges
Production-ready async architecture for cross-venue binary options arbitrage.

Author: Hermes (Sovereign Quant Stack)
Version: 1.0.0
"""

import asyncio
import signal
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Dict, List
import logging

from config import Config
from spot_feed import SpotFeedManager
from polymarket_client import PolymarketClient
from candle_aggregator import CandleAggregator
from risk_manager import RiskManager
from opportunity_engine import OpportunityEngine
from execution_stub import ExecutionStub

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler("btc_arb.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("BTC_ARB")

# ---------------------------------------------------------------------------
# Core Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArbitrageSignal:
    """Immutable signal emitted when edge exceeds threshold."""
    market_id: str
    market_slug: str
    resolution_price: Decimal
    spot_price: Decimal
    yes_ask: Decimal          # Polymarket "Yes" best ask
    fair_value: Decimal       # V_fair calculated from spot
    edge_bps: int             # Edge in basis points
    confidence: float         # 0.0-1.0 based on momentum + volume
    timestamp: float
    timeframe: str            # '5m', '15m', '1d'


class BTCArbitrageBot:
    """
    Main event loop orchestrating spot feeds, candle aggregation,
    Polymarket order book monitoring, and signal generation.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._shutdown_event = asyncio.Event()

        # Subsystems
        self.spot_feed = SpotFeedManager(cfg)
        self.polymarket = PolymarketClient(cfg)
        self.candles = CandleAggregator(cfg)
        self.risk = RiskManager(cfg)
        self.opportunities = OpportunityEngine(cfg)
        self.execution = ExecutionStub(cfg)

        # Shared state (protected by asyncio.Lock where needed)
        self._latest_spot: Dict[str, Decimal] = {}
        self._active_signals: List[ArbitrageSignal] = []
        self._lock = asyncio.Lock()

        logger.info("BTCArbitrageBot initialized")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Launch all subsystems and block until shutdown."""
        logger.info("Starting bot...")

        # Register signal handlers for graceful exit
        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_event_loop().add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(self._handle_signal(s))
            )

        tasks = [
            asyncio.create_task(self._spot_feed_loop()),
            asyncio.create_task(self._polymarket_loop()),
            asyncio.create_task(self._candle_loop()),
            asyncio.create_task(self._signal_loop()),
            asyncio.create_task(self._risk_loop()),
        ]

        await self._shutdown_event.wait()

        logger.info("Shutdown signal received. Cancelling tasks...")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Bot stopped cleanly.")

    async def _handle_signal(self, sig: signal.Signals) -> None:
        logger.warning(f"Received {sig.name}. Initiating graceful shutdown...")
        self._shutdown_event.set()

    # ------------------------------------------------------------------
    # Core Loops
    # ------------------------------------------------------------------

    async def _spot_feed_loop(self) -> None:
        """
        WebSocket loop: connect to Binance/Coinbase spot feed,
        parse trades/ticker, update latest price atomically.
        """
        while not self._shutdown_event.is_set():
            try:
                await self.spot_feed.connect()
                async for tick in self.spot_feed.stream():
                    if self._shutdown_event.is_set():
                        break
                    async with self._lock:
                        self._latest_spot[tick.symbol] = tick.price
                    # Push tick into candle aggregator
                    await self.candles.ingest_tick(tick)
            except Exception as exc:
                logger.error(f"Spot feed error: {exc}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    async def _polymarket_loop(self) -> None:
        """
        REST + WS loop: poll active BTC prediction markets,
        fetch order book depth for Yes/No contracts.
        """
        while not self._shutdown_event.is_set():
            try:
                markets = await self.polymarket.fetch_btc_markets()
                for mkt in markets:
                    book = await self.polymarket.fetch_order_book(mkt.market_id)
                    await self.opportunities.update_market(mkt, book)
                await asyncio.sleep(self.cfg.polymarket_poll_sec)
            except Exception as exc:
                logger.error(f"Polymarket loop error: {exc}")
                await asyncio.sleep(self.cfg.polymarket_poll_sec)

    async def _candle_loop(self) -> None:
        """
        Triggered on candle close: compute momentum metrics (VWAP, ROC)
        and feed into opportunity engine.
        """
        while not self._shutdown_event.is_set():
            try:
                closed = await self.candles.wait_for_close()
                for candle in closed:
                    self.opportunities.ingest_candle(candle)
            except Exception as exc:
                logger.error(f"Candle loop error: {exc}")

    async def _signal_loop(self) -> None:
        """
        Primary decision loop: cross-reference spot price vs implied probability.
        If edge > threshold → emit signal → execution stub.
        """
        while not self._shutdown_event.is_set():
            try:
                async with self._lock:
                    spot = self._latest_spot.get(self.cfg.primary_pair)
                if spot is None:
                    await asyncio.sleep(0.1)
                    continue

                signals = self.opportunities.scan(spot)
                for sig in signals:
                    if self.risk.allow_signal(sig):
                        await self._execute_signal(sig)
                    else:
                        logger.info(f"Signal BLOCKED by risk manager: {sig.market_slug}")

                await asyncio.sleep(self.cfg.signal_interval_sec)
            except Exception as exc:
                logger.error(f"Signal loop error: {exc}")

    async def _risk_loop(self) -> None:
        """
        Background circuit-breaker monitor:
        check P&L, slippage, and kill positions if needed.
        """
        while not self._shutdown_event.is_set():
            try:
                await self.risk.monitor_positions()
                await asyncio.sleep(self.cfg.risk_monitor_sec)
            except Exception as exc:
                logger.error(f"Risk loop error: {exc}")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute_signal(self, sig: ArbitrageSignal) -> None:
        logger.info(f"EXECUTE | {sig.market_slug} | Edge {sig.edge_bps} bps | Conf {sig.confidence:.2f}")
        # In production, this routes to Polymarket CLOB orders + hedge on perp
        await self.execution.submit(sig)
        await self.risk.register_position(sig)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = Config.from_env()
    bot = BTCArbitrageBot(cfg)
    asyncio.run(bot.start())
