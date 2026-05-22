"""
Real-time spot exchange WebSocket feed.
Supports Binance (default) and Coinbase Advanced.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator, Optional

import websockets

logger = logging.getLogger("SPOT_FEED")


@dataclass(frozen=True)
class Tick:
    symbol: str
    price: Decimal
    qty: Decimal
    timestamp: float
    is_buyer_maker: bool


class SpotFeedManager:
    def __init__(self, cfg):
        self.cfg = cfg
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_delay = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish WebSocket connection with exponential-backoff retry."""
        while True:
            try:
                logger.info(f"Connecting to {self.cfg.spot_ws_url} ...")
                self._ws = await websockets.connect(
                    self.cfg.spot_ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                )
                self._reconnect_delay = 1.0
                logger.info("Spot WebSocket connected.")
                return
            except Exception as exc:
                logger.error(f"Connection failed: {exc}. Retry in {self._reconnect_delay}s")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60)

    async def stream(self) -> AsyncIterator[Tick]:
        """Yield parsed Ticks indefinitely. Re-raise on fatal error."""
        if self._ws is None:
            raise RuntimeError("WebSocket not connected. Call connect() first.")

        try:
            async for raw in self._ws:
                tick = self._parse(raw)
                if tick:
                    yield tick
        except websockets.ConnectionClosed as exc:
            logger.warning(f"WebSocket closed: {exc}")
            raise  # Let outer loop reconnect
        except Exception as exc:
            logger.error(f"Stream error: {exc}")
            raise

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self, raw: str) -> Optional[Tick]:
        try:
            msg = json.loads(raw)

            # Binance trade stream
            if self.cfg.spot_exchange == "binance":
                if msg.get("e") == "trade":
                    return Tick(
                        symbol=msg["s"],
                        price=Decimal(str(msg["p"])),
                        qty=Decimal(str(msg["q"])),
                        timestamp=msg["T"] / 1000.0,
                        is_buyer_maker=msg["m"],
                    )

            # Coinbase matches channel
            if self.cfg.spot_exchange == "coinbase":
                if msg.get("type") == "match":
                    return Tick(
                        symbol=msg["product_id"].replace("-", ""),
                        price=Decimal(str(msg["price"])),
                        qty=Decimal(str(msg["size"])),
                        timestamp=msg["time"],
                        is_buyer_maker=msg["side"] == "sell",
                    )

            return None
        except Exception as exc:
            logger.debug(f"Parse error on: {raw[:200]} — {exc}")
            return None
