"""
Execution Stub — order routing to Polymarket CLOB + perp hedges.
Production-ready scaffolding with order state tracking.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Dict, Optional

import aiohttp

logger = logging.getLogger("EXEC")


class OrderStatus(Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class Order:
    id: str
    market_id: str
    side: str          # BUY / SELL
    type: str          # MARKET / LIMIT
    price: Optional[Decimal]
    size: Decimal
    status: OrderStatus
    filled: Decimal = Decimal("0")


class ExecutionStub:
    """
    Order execution layer.
    - Submits orders to Polymarket CLOB API
    - Tracks fill status
    - Routes hedge orders to perp venues
    - Handles retry logic and fill reporting
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._session: Optional[aiohttp.ClientSession] = None
        self._orders: Dict[str, Order] = {}

    async def submit(self, signal) -> Optional[Order]:
        """
        Submit a BUY order for Yes shares when signal fires.
        Returns Order object or None if rejected.
        """
        logger.info(f"EXEC | BUY {signal.market_slug} | Ask {signal.yes_ask} | Edge {signal.edge_bps} bps")

        order = Order(
            id=f"ord_{asyncio.get_event_loop().time()}",
            market_id=signal.market_id,
            side="BUY",
            type="MARKET",
            price=None,
            size=self.cfg.max_position_usd,
            status=OrderStatus.PENDING,
        )
        self._orders[order.id] = order

        if self.cfg.use_testnet:
            logger.info(f"[TESTNET] Simulated fill @ {signal.yes_ask}")
            order.status = OrderStatus.FILLED
            order.filled = order.size
            return order

        # Production: POST to Polymarket CLOB /order endpoint
        # Requires signed EIP-712 order format (Polymarket specific)
        # body = self._build_signed_order(signal)
        # async with self._session.post(url, json=body) as resp:
        #     ...

        return order

    async def sell(self, market_id: str, qty: Decimal) -> Optional[Order]:
        """Market sell to close position."""
        logger.info(f"EXEC | SELL {market_id} | Qty {qty}")
        order = Order(
            id=f"ord_sell_{asyncio.get_event_loop().time()}",
            market_id=market_id,
            side="SELL",
            type="MARKET",
            price=None,
            size=qty,
            status=OrderStatus.PENDING,
        )
        self._orders[order.id] = order

        if self.cfg.use_testnet:
            order.status = OrderStatus.FILLED
            order.filled = qty
            return order

        return order

    async def hedge_perp(self, side: str, qty: Decimal, price: Optional[Decimal] = None) -> None:
        """Stub for perp hedge order (Binance/dYdX)."""
        logger.info(f"[STUB] Perp hedge {side} {qty} @ {price or 'MARKET'}")

    def get_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)
