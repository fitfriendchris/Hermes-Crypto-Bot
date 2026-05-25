"""
discovery_engine.py — Hermes Solana Bot v2
Token discovery via Birdeye + DexScreener with risk filtering.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from risk_scanner import RiskScanner, RugReport

logger = logging.getLogger(__name__)

# Rate limits
BIRDEYE_RATE_LIMIT = 60  # requests per minute (free tier)
DEXSCREENER_RATE_LIMIT = 60


@dataclass
class DiscoveredToken:
    mint: str
    symbol: str
    name: str
    price_usd: float
    market_cap: float
    liquidity_usd: float
    volume_24h: float
    price_change_24h_pct: float
    holder_count: int
    age_hours: float
    source: str  # "birdeye" | "dexscreener"
    raw_data: dict = field(default_factory=dict)
    rug_report: RugReport | None = None


class DiscoveryEngine:
    """Discovers tokens from Birdeye + DexScreener, filters via risk scanner.

    Usage:
        engine = DiscoveryEngine(risk_scanner, birdeye_key="...")
        await engine.initialize()
        tokens = await engine.discover()
        safe = [t for t in tokens if t.rug_report and t.rug_report.is_safe]
    """

    def __init__(
        self,
        risk_scanner: RiskScanner,
        birdeye_api_key: str = "",
        birdeye_url: str = "https://public-api.birdeye.so",
        dexscreener_url: str = "https://api.dexscreener.com/latest/dex",
        max_rug_score: int = 30,
        min_liquidity_usd: float = 10_000.0,
        min_volume_24h_usd: float = 5_000.0,
    ) -> None:
        self.risk = risk_scanner
        self.birdeye_key = birdeye_api_key
        self.birdeye_url = birdeye_url.rstrip("/")
        self.dexscreener_url = dexscreener_url.rstrip("/")
        self.max_rug_score = max_rug_score
        self.min_liquidity = min_liquidity_usd
        self.min_volume = min_volume_24h_usd
        self.http = httpx.AsyncClient(timeout=15.0)
        # Rate limit tracking
        self._birdeye_calls: list[float] = []
        self._dex_calls: list[float] = []

    async def close(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ #
    # Main discovery
    # ------------------------------------------------------------------ #
    async def discover(self) -> list[DiscoveredToken]:
        """Run full discovery pipeline. Returns tokens that passed risk scan."""
        all_tokens: list[DiscoveredToken] = []

        # Birdeye trending
        try:
            birdeye_tokens = await self._birdeye_trending()
            all_tokens.extend(birdeye_tokens)
            logger.info("Birdeye: %d tokens", len(birdeye_tokens))
        except Exception as exc:
            logger.warning("Birdeye discovery failed: %s", exc)

        # DexScreener trending
        try:
            dex_tokens = await self._dexscreener_trending()
            all_tokens.extend(dex_tokens)
            logger.info("DexScreener: %d tokens", len(dex_tokens))
        except Exception as exc:
            logger.warning("DexScreener discovery failed: %s", exc)

        # Deduplicate by mint
        seen: set[str] = set()
        unique: list[DiscoveredToken] = []
        for t in all_tokens:
            if t.mint not in seen:
                seen.add(t.mint)
                unique.append(t)

        # Apply basic quality filter
        qualified = [
            t for t in unique
            if t.liquidity_usd >= self.min_liquidity
            and t.volume_24h >= self.min_volume
        ]
        logger.info("Qualified: %d / %d", len(qualified), len(unique))

        # Run risk scanner on each (with rate limiting)
        safe: list[DiscoveredToken] = []
        for t in qualified:
            try:
                report = await self.risk.scan(t.mint, t.symbol)
                t.rug_report = report
                if report.is_safe:
                    safe.append(t)
            except Exception as exc:
                logger.warning("Risk scan failed for %s: %s", t.mint[:8], exc)
            await asyncio.sleep(1.0)  # Be gentle with RPC

        logger.info("Safe tokens: %d / %d", len(safe), len(qualified))
        return safe

    # ------------------------------------------------------------------ #
    # Birdeye
    # ------------------------------------------------------------------ #
    async def _birdeye_trending(self) -> list[DiscoveredToken]:
        """Get trending tokens from Birdeye."""
        await self._rate_limit_birdeye()
        headers = {"X-API-KEY": self.birdeye_key} if self.birdeye_key else {}
        resp = await self.http.get(
            f"{self.birdeye_url}/public/token_trending",
            params={"offset": 0, "limit": 50},
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        tokens: list[DiscoveredToken] = []
        for item in data.get("data", {}).get("tokens", []):
            try:
                tokens.append(DiscoveredToken(
                    mint=item.get("address", ""),
                    symbol=item.get("symbol", "???"),
                    name=item.get("name", ""),
                    price_usd=float(item.get("price", 0)),
                    market_cap=float(item.get("mcap", 0)),
                    liquidity_usd=float(item.get("liquidity", 0)),
                    volume_24h=float(item.get("v24hUSD", 0)),
                    price_change_24h_pct=float(item.get("v24hChangePercent", 0)),
                    holder_count=int(item.get("holder", 0)),
                    age_hours=float(item.get("age", 0)) / 3600,
                    source="birdeye",
                    raw_data=item,
                ))
            except (ValueError, TypeError):
                continue
        return tokens

    # ------------------------------------------------------------------ #
    # DexScreener
    # ------------------------------------------------------------------ #
    async def _dexscreener_trending(self) -> list[DiscoveredToken]:
        """Get trending tokens from DexScreener."""
        await self._rate_limit_dexscreener()
        resp = await self.http.get(
            f"{self.dexscreener_url}/tokens/solana",
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        tokens: list[DiscoveredToken] = []
        for item in data.get("pairs", [])[:50]:
            try:
                base = item.get("baseToken", {})
                mint = base.get("address", "")
                if not mint:
                    continue
                liquidity = item.get("liquidity", {})
                volume = item.get("volume", {})
                tokens.append(DiscoveredToken(
                    mint=mint,
                    symbol=base.get("symbol", "???"),
                    name=base.get("name", ""),
                    price_usd=float(item.get("priceUsd", 0)),
                    market_cap=float(item.get("marketCap", 0) or 0),
                    liquidity_usd=float(liquidity.get("usd", 0) or 0),
                    volume_24h=float(volume.get("h24", 0) or 0),
                    price_change_24h_pct=float(item.get("priceChange", {}).get("h24", 0) or 0),
                    holder_count=0,  # DexScreener doesn't provide this reliably
                    age_hours=0.0,
                    source="dexscreener",
                    raw_data=item,
                ))
            except (ValueError, TypeError):
                continue
        return tokens

    # ------------------------------------------------------------------ #
    # Rate limiting
    # ------------------------------------------------------------------ #
    async def _rate_limit_birdeye(self) -> None:
        now = time.time()
        self._birdeye_calls = [t for t in self._birdeye_calls if now - t < 60]
        if len(self._birdeye_calls) >= BIRDEYE_RATE_LIMIT:
            sleep_for = 60 - (now - self._birdeye_calls[0])
            if sleep_for > 0:
                logger.debug("Birdeye rate limit: sleeping %.1fs", sleep_for)
                await asyncio.sleep(sleep_for)
        self._birdeye_calls.append(time.time())

    async def _rate_limit_dexscreener(self) -> None:
        now = time.time()
        self._dex_calls = [t for t in self._dex_calls if now - t < 60]
        if len(self._dex_calls) >= DEXSCREENER_RATE_LIMIT:
            sleep_for = 60 - (now - self._dex_calls[0])
            if sleep_for > 0:
                logger.debug("DexScreener rate limit: sleeping %.1fs", sleep_for)
                await asyncio.sleep(sleep_for)
        self._dex_calls.append(time.time())


if __name__ == "__main__":
    print("DiscoveryEngine loaded. Import and use with RiskScanner.")
