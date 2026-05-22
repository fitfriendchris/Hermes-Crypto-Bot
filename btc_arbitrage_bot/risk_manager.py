"""
Risk Manager — circuit breakers, position limits, and "get back" routine.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger("RISK")


@dataclass
class Position:
    signal
    entry_time: float
    cost_basis: Decimal
    qty: Decimal
    pnl_usd: Decimal = Decimal("0")


class RiskManager:
    """
    Enforces capital limits and executes the "get back" safety routine.
    - Max position size
    - Daily loss circuit breaker
    - Slippage-based abort
    - Automatic hedge or market exit on adverse move
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._positions: Dict[str, Position] = {}
        self._daily_pnl: Decimal = Decimal("0")
        self._circuit_open: bool = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Signal Gating
    # ------------------------------------------------------------------

    def allow_signal(self, signal) -> bool:
        """Return True if risk limits permit new trade."""
        if self._circuit_open:
            logger.warning("CIRCUIT BREAKER OPEN — no new trades")
            return False
        if self._daily_pnl <= -self.cfg.max_daily_loss_usd:
            logger.warning("Daily loss limit reached")
            self._circuit_open = True
            return False
        # Position count limit
        if len(self._positions) >= 3:
            logger.info("Max concurrent positions reached")
            return False
        return True

    # ------------------------------------------------------------------
    # Position Tracking
    # ------------------------------------------------------------------

    async def register_position(self, signal) -> None:
        async with self._lock:
            cost = signal.yes_ask * self.cfg.max_position_usd
            self._positions[signal.market_id] = Position(
                signal=signal,
                entry_time=signal.timestamp,
                cost_basis=cost,
                qty=self.cfg.max_position_usd,
            )
            logger.info(f"Registered position {signal.market_slug} | Size ${self.cfg.max_position_usd}")

    async def monitor_positions(self) -> None:
        """
        Background loop: evaluate open positions.
        If P&L breach or slippage spike → trigger get_back().
        """
        async with self._lock:
            for mkt_id, pos in list(self._positions.items()):
                # Simulate mark-to-market
                current_pnl = self._estimate_pnl(pos)
                pos.pnl_usd = current_pnl
                self._daily_pnl += current_pnl

                # Circuit trigger: position moving against us > 2%
                if current_pnl < -self.cfg.max_position_usd * Decimal("0.02"):
                    logger.warning(f"GET BACK triggered for {mkt_id} | PnL ${current_pnl}")
                    await self._get_back(mkt_id, pos)
                    continue

                # Hard stop: daily drawdown
                if self._daily_pnl <= -self.cfg.max_daily_loss_usd:
                    logger.error("DAILY LOSS LIMIT HIT — killing all positions")
                    await self._emergency_flatten()
                    self._circuit_open = True
                    return

    def _estimate_pnl(self, pos: Position) -> Decimal:
        """
        Estimate unrealized P&L.
        In production this queries current book mid for the position.
        """
        # Simplified: assume we can exit at last known signal price
        return Decimal("0")

    # ------------------------------------------------------------------
    # Get Back / Emergency Routines
    # ------------------------------------------------------------------

    async def _get_back(self, mkt_id: str, pos: Position) -> None:
        """
        "Get back" routine: market-sell the Yes shares immediately.
        Optionally hedge on perp if cross-venue hedge is enabled.
        """
        logger.critical(f"GET BACK EXECUTE | {mkt_id}")
        # 1. Market sell Yes shares on Polymarket
        await self._market_sell_yes(mkt_id, pos.qty)
        # 2. Optional: hedge on perp (stub)
        await self._hedge_perp(pos.signal.spot_price, pos.qty)
        # 3. Remove tracking
        del self._positions[mkt_id]

    async def _emergency_flatten(self) -> None:
        """Sell ALL open positions immediately."""
        tasks = []
        for mkt_id, pos in list(self._positions.items()):
            tasks.append(self._market_sell_yes(mkt_id, pos.qty))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._positions.clear()
        logger.critical("EMERGENCY FLATTEN COMPLETE")

    async def _market_sell_yes(self, mkt_id: str, qty: Decimal) -> None:
        """Stub: executes market sell on Polymarket CLOB."""
        logger.info(f"[STUB] Market sell {qty} Yes shares for {mkt_id}")
        # Production: POST /order with side=SELL, type=MARKET

    async def _hedge_perp(self, spot_price: Decimal, qty: Decimal) -> None:
        """Stub: open short perp position to hedge directional exposure."""
        logger.info(f"[STUB] Hedge short {qty} @ {spot_price} on perp")
        # Production: Binance/dYdX perp short order
