"""
Secondary Venue Module — Expandable scaffold for Kalshi, dYdX, or other prediction markets.
Provides a unified interface so the opportunity engine can compare implied probabilities
across multiple venues for the same BTC price event.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger("SECONDARY")


@dataclass(frozen=True)
class VenueMarket:
    """Normalized representation of a prediction market from any venue."""
    venue: str                    # 'kalshi', 'dydx', 'custom'
    market_id: str
    event_ticker: str             # e.g. 'KXBT-25JAN1-105K'
    question: str
    strike: Optional[Decimal]
    expiration: float             # Unix epoch
    yes_bid: Decimal              # Best bid for Yes
    yes_ask: Decimal              # Best ask for Yes
    volume_24h: Optional[Decimal]
    open_interest: Optional[Decimal]
    timestamp: float


class SecondaryVenueBase:
    """Abstract base for any secondary prediction venue."""

    async def fetch_btc_markets(self) -> List[VenueMarket]:
        raise NotImplementedError

    async def fetch_order_book(self, market_id: str) -> Dict:
        raise NotImplementedError


class KalshiClient(SecondaryVenueBase):
    """
    Kalshi API client (U.S. regulated prediction market).
    https://trading-api.readme.io/reference/
    """

    BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"

    def __init__(self, cfg):
        self.cfg = cfg
        self._session: Optional[aiohttp.ClientSession] = None
        self._backoff = 1.0

    async def fetch_btc_markets(self) -> List[VenueMarket]:
        """Query active BTC price events from Kalshi."""
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

        markets = []
        try:
            # Kalshi uses 'events' and 'markets' endpoints
            async with self._session.get(f"{self.BASE_URL}/events", params={"status": "open"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                for event in data.get("events", []):
                    if not self._is_btc_event(event.get("title", "")):
                        continue
                    # Each event has multiple markets (Yes/No, ranges, etc.)
                    event_markets = await self._fetch_event_markets(event["event_ticker"])
                    markets.extend(event_markets)
        except Exception as exc:
            logger.error(f"Kalshi fetch error: {exc}")

        logger.info(f"Kalshi: {len(markets)} BTC markets found")
        return markets

    async def _fetch_event_markets(self, event_ticker: str) -> List[VenueMarket]:
        """Fetch individual markets for a BTC event."""
        url = f"{self.BASE_URL}/events/{event_ticker}/markets"
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
            markets = []
            for m in data.get("markets", []):
                try:
                    strike = self._extract_strike(m.get("title", ""))
                    markets.append(VenueMarket(
                        venue="kalshi",
                        market_id=m["ticker"],
                        event_ticker=event_ticker,
                        question=m.get("title", ""),
                        strike=strike,
                        expiration=m.get("close_time", 0),
                        yes_bid=Decimal(str(m.get("yes_bid", 0))),
                        yes_ask=Decimal(str(m.get("yes_ask", 0))),
                        volume_24h=Decimal(str(m.get("volume", 0))),
                        open_interest=Decimal(str(m.get("open_interest", 0))),
                        timestamp=asyncio.get_event_loop().time(),
                    ))
                except Exception as exc:
                    logger.debug(f"Skipping Kalshi market: {exc}")
                    continue
            return markets

    async def fetch_order_book(self, market_id: str) -> Dict:
        """Fetch order book for a specific Kalshi market."""
        url = f"{self.BASE_URL}/markets/{market_id}/orderbook"
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()

    def _is_btc_event(self, title: str) -> bool:
        t = title.lower()
        return ("bitcoin" in t or "btc" in t) and any(kw in t for kw in ["price", "above", "below", "$"])

    def _extract_strike(self, title: str) -> Optional[Decimal]:
        import re
        match = re.search(r"\$([\d,]+(?:\.\d+)?)", title)
        return Decimal(match.group(1).replace(",", "")) if match else None


class VenueAggregator:
    """
    Aggregator that collects data from multiple secondary venues
    and exposes a unified comparison interface.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._venues: Dict[str, SecondaryVenueBase] = {
            "kalshi": KalshiClient(cfg),
            # "dydx": DydxPredictionClient(cfg),  # Expandable
        }
        self._cache: Dict[str, List[VenueMarket]] = {}

    async def refresh_all(self) -> None:
        """Poll all venues and update cache."""
        for name, client in self._venues.items():
            try:
                markets = await client.fetch_btc_markets()
                self._cache[name] = markets
            except Exception as exc:
                logger.error(f"Venue {name} refresh failed: {exc}")

    def get_best_yes_ask(self, strike: Decimal, min_volume: Decimal = Decimal("1000")) -> Optional[VenueMarket]:
        """
        Find the cheapest Yes ask across all venues for a given strike.
        Useful for multi-venue arbitrage (buy on cheapest, sell on most expensive).
        """
        candidates = []
        for venue, markets in self._cache.items():
            for m in markets:
                if m.strike == strike and m.volume_24h and m.volume_24h >= min_volume:
                    candidates.append(m)

        if not candidates:
            return None
        return min(candidates, key=lambda x: x.yes_ask)

    def compare_implied_probabilities(self, strike: Decimal) -> Dict[str, Decimal]:
        """
        Return a dict of venue -> implied probability (yes_ask price)
        for a given strike level. Used to detect cross-venue mispricing.
        """
        result = {}
        for venue, markets in self._cache.items():
            for m in markets:
                if m.strike == strike:
                    result[venue] = m.yes_ask
                    break
        return result
