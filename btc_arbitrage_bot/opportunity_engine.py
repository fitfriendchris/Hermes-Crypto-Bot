"""
Opportunity Engine — correlates spot price action to prediction market fair value.
Implements the "Get Back" formula for implied edge calculation.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

from polymarket_client import PolymarketMarket, PolymarketBook
from candle_aggregator import Candle

logger = logging.getLogger("OPP_ENGINE")

# Simple fee assumption: 2% taker on Polymarket + 0.1% spot slippage
POLY_TAKER_FEE = Decimal("0.02")
SPOT_SLIPPAGE = Decimal("0.001")


@dataclass
class ActiveMarket:
    market: PolymarketMarket
    book: PolymarketBook
    last_update: float


class OpportunityEngine:
    """
    Core math module.
    - Maintains snapshot of active Polymarket order books
    - Ingests spot candles to detect momentum
    - Computes fair value (V_fair) of Yes contracts from spot price
    - Emits signals when edge > threshold
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._markets: Dict[str, ActiveMarket] = {}
        self._latest_candles: Dict[str, Candle] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def update_market(self, market: PolymarketMarket, book: PolymarketBook) -> None:
        async with self._lock:
            self._markets[market.market_id] = ActiveMarket(
                market=market,
                book=book,
                last_update=asyncio.get_event_loop().time(),
            )

    def ingest_candle(self, candle: Candle) -> None:
        self._latest_candles[candle.timeframe] = candle

    # ------------------------------------------------------------------
    # Scan Loop
    # ------------------------------------------------------------------

    def scan(self, spot_price: Decimal) -> List:
        """
        Iterate all tracked markets, compute edge, return signals.
        """
        signals = []
        for mkt in self._markets.values():
            sig = self._evaluate(mkt, spot_price)
            if sig:
                signals.append(sig)
        return signals

    def _evaluate(self, am: ActiveMarket, spot: Decimal):
        mkt = am.market
        book = am.book

        if mkt.strike is None or not book.yes_asks:
            return None

        # --- Fair Value Calculation ---
        # For binary "Will BTC be above $X?"
        # V_fair ≈ probability from distance to strike + momentum
        # Delta/Probability approximation using sigmoid + velocity boost

        distance = float((spot - mkt.strike) / mkt.strike)  # e.g. 0.03 = 3% above
        v_fair = self._sigmoid_probability(distance)

        # Momentum boost: if 5m/15m candle is strongly directional, adjust
        v_fair = self._apply_momentum_boost(v_fair)

        v_fair = Decimal(str(v_fair))

        # --- Market Ask ---
        best_yes_ask = book.yes_asks[0].price   # cheapest Yes available
        if best_yes_ask <= 0:
            return None

        # --- Edge ---
        gross_edge = v_fair - best_yes_ask
        net_edge = gross_edge - POLY_TAKER_FEE - SPOT_SLIPPAGE

        if net_edge <= 0:
            return None

        edge_bps = int((net_edge / best_yes_ask) * 10000)
        if edge_bps < self.cfg.min_edge_bps:
            return None

        # Confidence score: combine momentum + liquidity + recency
        confidence = self._confidence(am, v_fair, best_yes_ask)
        if confidence < self.cfg.confidence_threshold:
            return None

        # Build signal
        from main import ArbitrageSignal
        return ArbitrageSignal(
            market_id=mkt.market_id,
            market_slug=mkt.slug,
            resolution_price=mkt.strike,
            spot_price=spot,
            yes_ask=best_yes_ask,
            fair_value=v_fair.quantize(Decimal("0.0001")),
            edge_bps=edge_bps,
            confidence=confidence,
            timestamp=asyncio.get_event_loop().time(),
            timeframe=self._dominant_timeframe(),
        )

    # ------------------------------------------------------------------
    # Math Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sigmoid_probability(distance: float, steepness: float = 25.0) -> float:
        """
        Sigmoid probability map.
        distance = % above strike (negative if below)
        Returns 0.0-1.0 probability of Yes resolution.
        """
        import math
        return 1.0 / (1.0 + math.exp(-steepness * distance))

    def _apply_momentum_boost(self, base_prob: float) -> float:
        """
        If recent 5m/15m candles show strong directional momentum,
        shift probability toward certainty (0 or 1).
        """
        boost = 0.0
        for tf in ("5m", "15m"):
            c = self._latest_candles.get(tf)
            if c and c.roc:
                # Strong positive ROC → push prob toward 1.0
                if c.roc > 1.0:
                    boost += min(c.roc * 0.02, 0.15)
                elif c.roc < -1.0:
                    boost -= min(abs(c.roc) * 0.02, 0.15)
        return max(0.0, min(1.0, base_prob + boost))

    def _confidence(self, am: ActiveMarket, v_fair: Decimal, ask: Decimal) -> float:
        """
        Composite confidence 0.0-1.0 based on:
        - Book depth (liquidity)
        - Candle momentum alignment
        - Recency of data
        """
        score = 0.5

        # Liquidity bonus
        total_yes_ask_size = sum(lvl.size for lvl in am.book.yes_asks[:3])
        if total_yes_ask_size >= Decimal("1000"):
            score += 0.2
        elif total_yes_ask_size >= Decimal("500"):
            score += 0.1

        # Momentum alignment
        c5 = self._latest_candles.get("5m")
        if c5:
            if c5.roc > 0.5 and v_fair > ask:
                score += 0.15
            elif c5.roc < -0.5 and v_fair < ask:
                score += 0.15

        # Recency penalty
        age = asyncio.get_event_loop().time() - am.last_update
        if age > 10:
            score -= 0.1

        return max(0.0, min(1.0, score))

    def _dominant_timeframe(self) -> str:
        """Return the highest-confidence timeframe currently driving the signal."""
        for tf in ("5m", "15m", "1d"):
            if tf in self._latest_candles:
                return tf
        return "5m"
