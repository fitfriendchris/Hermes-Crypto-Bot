"""
rpc_manager.py — Hermes Solana Bot v2
Multi-RPC failover with health checks, round-robin, exponential backoff.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

logger = logging.getLogger(__name__)


@dataclass
class RPCEndpoint:
    name: str
    url: str
    client: AsyncClient | None = None
    healthy: bool = True
    last_check: float = 0.0
    latency_ms: float = 0.0
    failure_count: int = 0
    is_staked: bool = False  # staked RPCs get priority


class RPCManager:
    """Manages multiple Solana RPC endpoints with automatic failover.

    Usage:
        rpc = RPCManager(primary_url="...", fallback_url="...", public_url="...")
        await rpc.initialize()
        client = rpc.get_client()
        # use client...
        await rpc.close()
    """

    def __init__(
        self,
        primary_url: str,
        fallback_url: str,
        public_url: str,
        timeout_seconds: float = 30.0,
        health_check_interval_seconds: float = 30.0,
        max_retries: int = 3,
        retry_backoff_base: float = 1.0,
    ) -> None:
        self.endpoints: list[RPCEndpoint] = [
            RPCEndpoint(name="primary", url=primary_url, is_staked=True),
            RPCEndpoint(name="fallback", url=fallback_url),
            RPCEndpoint(name="public", url=public_url),
        ]
        self.timeout = timeout_seconds
        self.health_interval = health_check_interval_seconds
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base
        self._current_index: int = 0
        self._health_task: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def initialize(self) -> None:
        """Create AsyncClient for each endpoint and run initial health check."""
        for ep in self.endpoints:
            try:
                ep.client = AsyncClient(ep.url, timeout=self.timeout, commitment=Confirmed)
            except Exception as exc:
                logger.warning("Failed to init %s RPC: %s", ep.name, exc)
                ep.healthy = False
        await self._health_check_all()
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("RPCManager initialized with %d endpoints", len(self.endpoints))

    async def close(self) -> None:
        """Close all clients and stop health loop."""
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        for ep in self.endpoints:
            if ep.client:
                try:
                    await ep.client.close()
                except Exception as exc:
                    logger.debug("Error closing %s RPC: %s", ep.name, exc)
                ep.client = None
        logger.info("RPCManager closed")

    # ------------------------------------------------------------------ #
    # Client selection
    # ------------------------------------------------------------------ #
    def get_client(self) -> AsyncClient:
        """Return the best healthy client. Raises if none available."""
        healthy = [ep for ep in self.endpoints if ep.healthy and ep.client is not None]
        if not healthy:
            # Try to use any endpoint even if marked unhealthy (last resort)
            available = [ep for ep in self.endpoints if ep.client is not None]
            if not available:
                raise RuntimeError("No RPC endpoints available")
            logger.warning("All RPCs marked unhealthy — using %s as last resort", available[0].name)
            return available[0].client

        # Prefer staked, then lowest latency
        healthy.sort(key=lambda ep: (not ep.is_staked, ep.latency_ms))
        return healthy[0].client

    def get_endpoint_name(self) -> str:
        """Return name of currently preferred endpoint."""
        try:
            client = self.get_client()
            for ep in self.endpoints:
                if ep.client is client:
                    return ep.name
        except RuntimeError:
            pass
        return "none"

    # ------------------------------------------------------------------ #
    # Health checks
    # ------------------------------------------------------------------ #
    async def _health_check_all(self) -> None:
        """Check all endpoints in parallel."""
        await asyncio.gather(
            *[self._check_one(ep) for ep in self.endpoints],
            return_exceptions=True,
        )

    async def _check_one(self, ep: RPCEndpoint) -> None:
        """Single endpoint health check — getSlot is lightweight."""
        if ep.client is None:
            ep.healthy = False
            return
        start = time.time()
        try:
            resp = await ep.client.get_slot()
            ep.latency_ms = (time.time() - start) * 1000
            if resp.value is not None:
                ep.healthy = True
                ep.failure_count = 0
                logger.debug("%s RPC healthy (latency=%.1fms)", ep.name, ep.latency_ms)
            else:
                ep.healthy = False
                ep.failure_count += 1
        except Exception as exc:
            ep.healthy = False
            ep.failure_count += 1
            ep.latency_ms = 9999.0
            logger.warning("%s RPC health check failed: %s", ep.name, exc)

    async def _health_loop(self) -> None:
        """Background task: periodic health checks."""
        while True:
            try:
                await asyncio.sleep(self.health_interval)
                await self._health_check_all()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health loop error: %s", exc)

    # ------------------------------------------------------------------ #
    # Retry wrapper
    # ------------------------------------------------------------------ #
    async def call_with_retry(
        self,
        coro_factory: Callable[[AsyncClient], Any],
        description: str = "RPC call",
    ) -> Any:
        """Execute an RPC call with automatic failover and exponential backoff.

        Args:
            coro_factory: A callable that takes an AsyncClient and returns a coroutine.
            description: Human-readable description for logging.

        Returns:
            The RPC response value.

        Raises:
            RuntimeError: If all retries across all endpoints fail.
        """
        last_exception: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            for ep in self._endpoints_in_priority_order():
                if ep.client is None:
                    continue
                try:
                    result = await coro_factory(ep.client)
                    # Mark healthy on success
                    if not ep.healthy:
                        ep.healthy = True
                        ep.failure_count = 0
                    return result
                except Exception as exc:
                    last_exception = exc
                    ep.failure_count += 1
                    logger.warning(
                        "%s failed on %s (attempt %d/%d): %s",
                        description, ep.name, attempt, self.max_retries, exc,
                    )
                    # Small delay between endpoint tries
                    await asyncio.sleep(0.1)
            # Exponential backoff between retry rounds
            if attempt < self.max_retries:
                delay = self.retry_backoff_base * (2 ** (attempt - 1))
                logger.info("RPC retry backoff: %.1fs before attempt %d", delay, attempt + 1)
                await asyncio.sleep(delay)

        raise RuntimeError(
            f"{description} failed after {self.max_retries} retries on all endpoints"
        ) from last_exception

    def _endpoints_in_priority_order(self) -> list[RPCEndpoint]:
        """Rotate starting point for load distribution, but prefer healthy staked."""
        healthy = [ep for ep in self.endpoints if ep.healthy]
        if healthy:
            healthy.sort(key=lambda ep: (not ep.is_staked, ep.latency_ms))
            return healthy
        # Fallback: return all with clients
        return [ep for ep in self.endpoints if ep.client is not None]

    # ------------------------------------------------------------------ #
    # Convenience wrappers
    # ------------------------------------------------------------------ #
    async def get_slot(self) -> int:
        resp = await self.call_with_retry(lambda c: c.get_slot(), "get_slot")
        return resp.value

    async def get_balance(self, pubkey: str) -> int:
        resp = await self.call_with_retry(
            lambda c: c.get_balance(pubkey), f"get_balance({pubkey[:8]}...)"
        )
        return resp.value

    async def get_token_largest_accounts(self, mint: str) -> list:
        from solders.pubkey import Pubkey
        pk = Pubkey.from_string(mint) if isinstance(mint, str) else mint
        resp = await self.call_with_retry(
            lambda c: c.get_token_largest_accounts(pk), f"token_largest_accounts({mint[:8]}...)"
        )
        return resp.value or []

    async def get_parsed_account_info(self, pubkey: str) -> Any:
        from solders.pubkey import Pubkey
        pk = Pubkey.from_string(pubkey) if isinstance(pubkey, str) else pubkey
        resp = await self.call_with_retry(
            lambda c: c.get_account_info_json_parsed(pk),
            f"parsed_account_info({pubkey[:8]}...)",
        )
        return resp.value

    async def get_signatures_for_address(
        self, address: str, limit: int = 10
    ) -> list:
        from solders.pubkey import Pubkey
        pk = Pubkey.from_string(address) if isinstance(address, str) else address
        resp = await self.call_with_retry(
            lambda c: c.get_signatures_for_address(pk, limit=limit),
            f"signatures_for_address({address[:8]}...)",
        )
        return resp.value or []

    async def simulate_transaction(self, tx: Any) -> Any:
        resp = await self.call_with_retry(
            lambda c: c.simulate_transaction(tx), "simulate_transaction"
        )
        return resp.value

    async def send_transaction(self, tx: Any, opts: Any | None = None) -> str:
        resp = await self.call_with_retry(
            lambda c: c.send_transaction(tx, opts=opts), "send_transaction"
        )
        return resp.value

    async def get_signature_statuses(self, signatures: list[str]) -> list:
        resp = await self.call_with_retry(
            lambda c: c.get_signature_statuses(signatures), "get_signature_statuses"
        )
        return resp.value or []


if __name__ == "__main__":
    async def main():
        rpc = RPCManager(
            primary_url="https://api.mainnet-beta.solana.com",
            fallback_url="https://api.mainnet-beta.solana.com",
            public_url="https://api.mainnet-beta.solana.com",
        )
        await rpc.initialize()
        slot = await rpc.get_slot()
        print(f"slot={slot} endpoint={rpc.get_endpoint_name()}")
        await rpc.close()

    asyncio.run(main())
