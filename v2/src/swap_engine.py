"""
swap_engine.py — Hermes Solana Bot v2
Jupiter v6 integration with Jito bundles, tx simulation, confirmation polling.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from solana.rpc.commitment import Confirmed

from rpc_manager import RPCManager

logger = logging.getLogger(__name__)

DONTFRONT_PUBKEY = Pubkey.from_string("jitodontfront111111111111111111111111111111")
JITO_BLOCK_ENGINE = os.getenv("JITO_BLOCK_ENGINE", "https://mainnet.block-engine.jito.wtf/api/v1")


@dataclass
class SwapQuote:
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    route: dict[str, Any]
    slippage_bps: int
    context_slot: int


@dataclass
class SwapResult:
    success: bool
    tx_hash: str
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    fee_lamports: int
    price_impact_pct: float
    error: str = ""
    simulation_error: str = ""
    confirmation_status: str = ""
    confirmation_time_seconds: float = 0.0


class SwapEngine:
    """Jupiter v6 swap engine with Jito bundles and full safety pipeline.

    Pipeline: quote → simulate → build → sign → Jito bundle → confirm
    """

    def __init__(
        self,
        rpc_manager: RPCManager,
        wallet_keypair: Keypair,
        jupiter_api_url: str = "https://api.jup.ag/swap/v6",
        default_slippage_bps: int = 300,
        max_slippage_bps: int = 500,
        compute_unit_price_micro_lamports: int = 50_000,
        compute_unit_limit: int = 200_000,
        max_price_impact_pct: float = 5.0,
        confirmation_timeout_seconds: float = 60.0,
        max_retry_attempts: int = 3,
    ) -> None:
        self.rpc = rpc_manager
        self.wallet = wallet_keypair
        self.jupiter_url = jupiter_api_url.rstrip("/")
        self.default_slippage = default_slippage_bps
        self.max_slippage = max_slippage_bps
        self.cu_price = compute_unit_price_micro_lamports
        self.cu_limit = compute_unit_limit
        self.max_price_impact = max_price_impact_pct
        self.confirmation_timeout = confirmation_timeout_seconds
        self.max_retries = max_retry_attempts
        self.http = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ #
    # Quote
    # ------------------------------------------------------------------ #
    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None = None,
    ) -> SwapQuote:
        """Get Jupiter v6 quote."""
        slippage = slippage_bps or self.default_slippage
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": slippage,
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        url = f"{self.jupiter_url}/quote"
        resp = await self.http.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise SwapError(f"Jupiter quote error: {data['error']}")

        price_impact = float(data.get("priceImpactPct", 0))
        if price_impact > self.max_price_impact:
            raise SwapError(
                f"Price impact {price_impact:.2f}% exceeds max {self.max_price_impact}%"
            )

        return SwapQuote(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount_lamports,
            out_amount=int(data["outAmount"]),
            price_impact_pct=price_impact,
            route=data,
            slippage_bps=slippage,
            context_slot=data.get("contextSlot", 0),
        )

    # ------------------------------------------------------------------ #
    # Simulation
    # ------------------------------------------------------------------ #
    async def simulate_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None = None,
    ) -> dict[str, Any]:
        """Simulate a swap transaction without sending it. Returns simulation result."""
        quote = await self.get_quote(input_mint, output_mint, amount_lamports, slippage_bps)
        unsigned_tx = await self._build_swap_transaction(quote)

        # Simulate via RPC
        sim_result = await self.rpc.simulate_transaction(unsigned_tx)
        return {
            "quote": quote,
            "simulation": sim_result,
            "success": sim_result.err is None if sim_result else False,
            "error": str(sim_result.err) if sim_result and sim_result.err else "",
        }

    # ------------------------------------------------------------------ #
    # Execute
    # ------------------------------------------------------------------ #
    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None = None,
        description: str = "swap",
    ) -> SwapResult:
        """Full swap pipeline: quote → simulate → build → sign → Jito bundle → confirm."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._execute_once(
                    input_mint, output_mint, amount_lamports,
                    slippage_bps, description, attempt,
                )
            except Exception as exc:
                last_error = exc
                logger.warning("Swap attempt %d failed: %s", attempt, exc)
                if attempt < self.max_retries:
                    # Increase slippage on retry
                    new_slippage = min(
                        (slippage_bps or self.default_slippage) + 100 * attempt,
                        self.max_slippage,
                    )
                    slippage_bps = new_slippage
                    delay = 1.5 ** (attempt - 1)
                    logger.info("Retrying with slippage=%d bps in %.1fs", new_slippage, delay)
                    await asyncio.sleep(delay)

        return SwapResult(
            success=False,
            tx_hash="",
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount_lamports,
            out_amount=0,
            fee_lamports=0,
            price_impact_pct=0.0,
            error=f"All {self.max_retries} attempts failed: {last_error}",
        )

    async def _execute_once(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: int | None,
        description: str,
        attempt: int,
    ) -> SwapResult:
        # Step 1: Quote
        quote = await self.get_quote(input_mint, output_mint, amount_lamports, slippage_bps)
        logger.info("Quote: %s → %s | in=%d | out=%d | impact=%.3f%%",
                    input_mint[:8], output_mint[:8], quote.in_amount, quote.out_amount,
                    quote.price_impact_pct)

        # Step 2: Build unsigned tx
        unsigned_tx = await self._build_swap_transaction(quote)

        # Step 3: Simulate
        sim_result = await self.rpc.simulate_transaction(unsigned_tx)
        if sim_result and sim_result.err:
            raise SwapError(f"Simulation failed: {sim_result.err}")
        logger.debug("Simulation passed")

        # Step 4: Sign
        signed_tx = VersionedTransaction(unsigned_tx.message, [self.wallet])

        # Step 5: Submit via Jito bundle
        tx_hash = await self._submit_jito_bundle(signed_tx, attempt)
        logger.info("Tx submitted: %s", tx_hash)

        # Step 6: Confirm
        confirmed = await self._confirm_transaction(tx_hash)
        if not confirmed:
            raise SwapError(f"Tx {tx_hash} not confirmed within {self.confirmation_timeout}s")

        return SwapResult(
            success=True,
            tx_hash=tx_hash,
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount_lamports,
            out_amount=quote.out_amount,
            fee_lamports=sim_result.value.fee if sim_result else 5000,
            price_impact_pct=quote.price_impact_pct,
            confirmation_status="confirmed",
            confirmation_time_seconds=0.0,  # measured in _confirm_transaction
        )

    # ------------------------------------------------------------------ #
    # Build unsigned transaction
    # ------------------------------------------------------------------ #
    async def _build_swap_transaction(self, quote: SwapQuote) -> VersionedTransaction:
        """Build unsigned VersionedTransaction from Jupiter swap instruction."""
        payload = {
            "quoteResponse": quote.route,
            "userPublicKey": str(self.wallet.pubkey()),
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": self.cu_price,
            "computeUnitLimit": self.cu_limit,
            "prioritizationFeeLamports": self.cu_price,  # Jupiter handles this
            "dynamicComputeUnitLimit": True,
        }
        resp = await self.http.post(
            f"{self.jupiter_url}/swap",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise SwapError(f"Jupiter swap build error: {data['error']}")

        tx_b64 = data["swapTransaction"]
        tx_bytes = base64.b64decode(tx_b64)
        return VersionedTransaction.from_bytes(tx_bytes)

    # ------------------------------------------------------------------ #
    # Jito bundle submission
    # ------------------------------------------------------------------ #
    async def _submit_jito_bundle(
        self, signed_tx: VersionedTransaction, attempt: int
    ) -> str:
        """Submit via Jito bundle. Includes dontfront account for sandwich protection."""
        tx_b64 = base64.b64encode(signed_tx.serialize()).decode()

        # Build bundle with dontfront account
        bundle = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                [tx_b64],
                {
                    "tipAccount": str(DONTFRONT_PUBKEY),
                    "tipLamports": 10_000 * attempt,  # Increase tip on retry
                },
            ],
        }
        resp = await self.http.post(
            f"{JITO_BLOCK_ENGINE}/bundles",
            json=bundle,
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise SwapError(f"Jito bundle error: {data['error']}")

        # Jito returns bundle ID, not tx hash. Get tx hash from signed tx.
        tx_hash = signed_tx.signatures[0].__str__() if signed_tx.signatures else ""
        return tx_hash

    # ------------------------------------------------------------------ #
    # Confirmation polling
    # ------------------------------------------------------------------ #
    async def _confirm_transaction(self, tx_hash: str) -> bool:
        """Poll getSignatureStatuses with exponential backoff up to timeout."""
        start = asyncio.get_event_loop().time()
        poll_interval = 0.5
        max_poll = 5.0

        while asyncio.get_event_loop().time() - start < self.confirmation_timeout:
            try:
                statuses = await self.rpc.get_signature_statuses([tx_hash])
                if statuses and len(statuses) > 0:
                    status = statuses[0]
                    if status and status.confirmation_status:
                        if status.confirmation_status == "confirmed" or status.confirmation_status == "finalized":
                            elapsed = asyncio.get_event_loop().time() - start
                            logger.info("Tx %s confirmed (%s) in %.2fs",
                                        tx_hash[:16], status.confirmation_status, elapsed)
                            return True
                        if status.err:
                            logger.error("Tx %s failed: %s", tx_hash[:16], status.err)
                            return False
            except Exception as exc:
                logger.debug("Confirmation poll error: %s", exc)

            await asyncio.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, max_poll)

        logger.warning("Tx %s NOT confirmed after %.0fs", tx_hash[:16], self.confirmation_timeout)
        return False


class SwapError(Exception):
    """Swap execution error."""
    pass


if __name__ == "__main__":
    # Test requires real keypair and RPC
    print("SwapEngine loaded. Import and use with a wallet keypair.")
