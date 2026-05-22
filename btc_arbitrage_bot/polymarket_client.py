"""
Polymarket CLOB (Centralized Limit Order Book) client.
Fetches active BTC prediction markets and order book depth.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger("POLY")

# Polymarket CLOB endpoints (Gamma + CLOB)
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


@dataclass(frozen=True)
class PolymarketMarket:
    market_id: str
    slug: str
    question: str
    resolution_time: float      # Unix epoch
    condition_id: str
    yes_token_id: str
    no_token_id: str
    strike: Optional[Decimal]   # Extracted from question text if possible


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    size: Decimal


@dataclass
class PolymarketBook:
    market_id: str
    yes_asks: List[OrderBookLevel]
    yes_bids: List[OrderBookLevel]
    no_asks: List[OrderBookLevel]
    no_bids: List[OrderBookLevel]
    timestamp: float


class PolymarketClient:
    """
    Async REST client for Polymarket CLOB.
    - Searches active BTC markets via Gamma API
    - Pulls order book snapshots via CLOB API
    - Handles rate-limiting with exponential backoff
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._session: Optional[aiohttp.ClientSession] = None
        self._backoff_sec = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_btc_markets(self) -> List[PolymarketMarket]:
        """
        Query Gamma API for active markets matching Bitcoin price questions.
        Returns list of structured market objects.
        """
        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": "100",
            "tag": "Bitcoin",
        }
        data = await self._get(f"{GAMMA_API}/markets", params=params)
        markets = []
        for m in data or []:
            q = m.get("question", "")
            if not self._is_btc_price_market(q):
                continue
            try:
                markets.append(PolymarketMarket(
                    market_id=str(m["id"]),
                    slug=m.get("slug", ""),
                    question=q,
                    resolution_time=m.get("resolutionTime", 0) / 1000,
                    condition_id=m["conditionId"],
                    yes_token_id=m["tokens"][0]["token_id"],
                    no_token_id=m["tokens"][1]["token_id"],
                    strike=self._extract_strike(q),
                ))
            except Exception as exc:
                logger.debug(f"Skipping malformed market: {exc}")
                continue
        logger.info(f"Found {len(markets)} active BTC prediction markets")
        return markets

    async def fetch_order_book(self, market_id: str) -> PolymarketBook:
        """
        Fetch CLOB order book for a given market condition.
        Returns bid/ask for Yes and No tokens.
        """
        url = f"{CLOB_API}/book"
        # Polymarket CLOB uses token_id for book queries
        # We fetch both sides; in practice you'd know which token is YES
        params = {"market": market_id, "side": "BUY"}   # Simplified
        data = await self._get(url, params=params)

        # Parse raw book (Polymarket CLOB returns array of [price, size])
        yes_asks = self._parse_levels(data.get("asks", []))
        yes_bids = self._parse_levels(data.get("bids", []))

        # Invert for "No" side: No price = 1 - Yes price
        no_asks = [OrderBookLevel(Decimal("1") - lvl.price, lvl.size) for lvl in yes_bids]
        no_bids = [OrderBookLevel(Decimal("1") - lvl.price, lvl.size) for lvl in yes_asks]

        return PolymarketBook(
            market_id=market_id,
            yes_asks=yes_asks,
            yes_bids=yes_bids,
            no_asks=no_asks,
            no_bids=no_bids,
            timestamp=asyncio.get_event_loop().time(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get(self, url: str, params: Optional[Dict] = None) -> dict:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            )

        while True:
            try:
                async with self._session.get(url, params=params) as resp:
                    if resp.status == 429:
                        logger.warning(f"Rate limited. Backing off {self._backoff_sec}s")
                        await asyncio.sleep(self._backoff_sec)
                        self._backoff_sec = min(self._backoff_sec * 2, 60)
                        continue
                    resp.raise_for_status()
                    self._backoff_sec = 1.0
                    return await resp.json()
            except aiohttp.ClientError as exc:
                logger.error(f"Request failed: {exc}. Retry in {self._backoff_sec}s")
                await asyncio.sleep(self._backoff_sec)
                self._backoff_sec = min(self._backoff_sec * 2, 60)

    def _parse_levels(self, raw: List[List]) -> List[OrderBookLevel]:
        levels = []
        for row in raw:
            try:
                levels.append(OrderBookLevel(
                    price=Decimal(str(row[0])),
                    size=Decimal(str(row[1])),
                ))
            except Exception:
                continue
        return levels

    def _is_btc_price_market(self, question: str) -> bool:
        """Heuristic: does this question relate to BTC price thresholds?"""
        q = question.lower()
        return (
            "bitcoin" in q or "btc" in q
        ) and any(
            kw in q for kw in ["above", "below", "hit", "reach", "$"]
        )

    def _extract_strike(self, question: str) -> Optional[Decimal]:
        """
        Naive regex-free extraction of price threshold from question text.
        Example: 'Will Bitcoin be above $105,000?' → 105000
        """
        import re
        match = re.search(r"\$([\d,]+(?:\.\d+)?)", question)
        if match:
            return Decimal(match.group(1).replace(",", ""))
        return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
