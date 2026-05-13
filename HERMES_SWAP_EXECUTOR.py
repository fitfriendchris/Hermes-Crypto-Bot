"""
HERMES SWAP EXECUTOR
Jupiter v6 aggregator — Solana token swaps (buy SOL→token, sell token→SOL).
ETH tokens remain paper-only until an EVM executor is added.
Author: Hermes | May 2026
"""

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp

logger = logging.getLogger('CryptoBot')

JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL  = "https://quote-api.jup.ag/v6/swap"

SOL_MINT  = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

MAX_PRICE_IMPACT_PCT = 5.0   # abort if slippage > 5%
MIN_LAMPORTS         = 100_000  # 0.0001 SOL floor


@dataclass
class SwapResult:
    success: bool
    input_mint: str
    output_mint: str
    amount_in: int           # raw input units (lamports for SOL, raw for token)
    output_amount: float     # human-readable output
    price_impact_pct: float
    tx_signature: Optional[str] = None
    error: Optional[str] = None


class JupiterSwap:
    """Jupiter v6 aggregator: quote → serialize → sign → send."""

    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self._rpc_url = rpc_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        logger.info("Jupiter v6 swap engine ready")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_in: int,
        slippage_bps: int = 300,
    ) -> Optional[dict]:
        params = {
            "inputMint":        input_mint,
            "outputMint":       output_mint,
            "amount":           str(amount_in),
            "slippageBps":      str(slippage_bps),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        try:
            async with self._session.get(JUPITER_QUOTE_URL, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Jupiter quote {resp.status}: {body[:200]}")
                    return None
                return await resp.json()
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
            return None

    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_in: int,
        slippage_bps: int = 300,
    ) -> SwapResult:
        # 1 — quote
        quote = await self.get_quote(input_mint, output_mint, amount_in, slippage_bps)
        if not quote:
            return SwapResult(False, input_mint, output_mint, amount_in, 0.0, 0.0,
                              error="Quote failed")

        price_impact = float(quote.get("priceImpactPct", 0))
        if price_impact > MAX_PRICE_IMPACT_PCT:
            return SwapResult(False, input_mint, output_mint, amount_in, 0.0,
                              price_impact,
                              error=f"Price impact {price_impact:.2f}% > {MAX_PRICE_IMPACT_PCT}%")

        out_amount_raw = int(quote.get("outAmount", 0))

        # 2 — serialize via Jupiter
        # Priority: EXODUS_PRIVATE_KEY → PHANTOM_PRIVATE_KEY (use whichever is funded)
        raw_key = (os.getenv("EXODUS_PRIVATE_KEY", "").strip() or
                   os.getenv("PHANTOM_PRIVATE_KEY", "").strip())
        if not raw_key:
            return SwapResult(False, input_mint, output_mint, amount_in,
                              out_amount_raw / 1e9, price_impact,
                              error="No Solana private key in .env (PHANTOM_PRIVATE_KEY or EXODUS_PRIVATE_KEY)")

        try:
            from solders.keypair import Keypair
            keypair = Keypair.from_base58_string(raw_key)
            user_pk = str(keypair.pubkey())
        except Exception as e:
            return SwapResult(False, input_mint, output_mint, amount_in, 0.0,
                              price_impact, error=f"Bad private key: {e}")

        payload = {
            "quoteResponse":              quote,
            "userPublicKey":              user_pk,
            "wrapAndUnwrapSol":           True,
            "dynamicComputeUnitLimit":    True,
            "prioritizationFeeLamports":  5_000,
        }
        try:
            async with self._session.post(
                JUPITER_SWAP_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return SwapResult(False, input_mint, output_mint, amount_in,
                                      out_amount_raw / 1e9, price_impact,
                                      error=f"Serialize {resp.status}: {body[:200]}")
                swap_resp = await resp.json()
        except Exception as e:
            return SwapResult(False, input_mint, output_mint, amount_in, 0.0,
                              price_impact, error=str(e))

        # 3 — sign + send
        try:
            raw_tx = base64.b64decode(swap_resp["swapTransaction"])

            from solders.transaction import VersionedTransaction
            from solana.rpc.async_api import AsyncClient
            from solana.rpc.types import TxOpts

            vtx = VersionedTransaction.from_bytes(raw_tx)
            vtx_signed = VersionedTransaction(vtx.message, [keypair])

            client = AsyncClient(self._rpc_url)
            opts = TxOpts(skip_preflight=False, preflight_commitment="confirmed")
            resp = await client.send_raw_transaction(bytes(vtx_signed), opts=opts)
            await client.close()

            sig = str(resp.value)
            out_human = out_amount_raw / 1e9
            logger.info(
                f"SWAP OK | {sig[:20]}... | "
                f"in={amount_in} → out={out_human:.6f} | impact={price_impact:.2f}%"
            )
            return SwapResult(True, input_mint, output_mint, amount_in,
                              out_human, price_impact, tx_signature=sig)

        except Exception as e:
            logger.error(f"Jupiter sign/send failed: {e}")
            return SwapResult(False, input_mint, output_mint, amount_in,
                              out_amount_raw / 1e9, price_impact, error=str(e))


class SwapManager:
    """Top-level manager used by the bot and sweeper."""

    SOL_MINT  = SOL_MINT
    USDC_MINT = USDC_MINT

    def __init__(self, wallet_manager):
        self.wallet  = wallet_manager
        self.jupiter = JupiterSwap()
        self._sol_price:    float = 0.0
        self._sol_price_ts: float = 0.0

    async def initialize(self):
        await self.jupiter.initialize()

    async def execute_swap(
        self,
        input_mint: str,
        output_mint: str,
        amount_in: int,
        slippage_bps: int = 300,
    ) -> SwapResult:
        return await self.jupiter.execute_swap(
            input_mint=input_mint,
            output_mint=output_mint,
            amount_in=amount_in,
            slippage_bps=slippage_bps,
        )

    async def get_sol_price(self) -> float:
        """Live SOL/USD price — cached 60 s."""
        now = time.time()
        if self._sol_price > 0 and now - self._sol_price_ts < 60:
            return self._sol_price
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.dexscreener.com/latest/dex/tokens/"
                    "So11111111111111111111111111111111111111112",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs:
                            price = float(pairs[0].get("priceUsd", 0))
                            if price > 0:
                                self._sol_price    = price
                                self._sol_price_ts = now
                                return price
        except Exception:
            pass
        return self._sol_price or 150.0

    async def usd_to_lamports(self, usd: float) -> int:
        sol_price = await self.get_sol_price()
        return int((usd / sol_price) * 1e9)

    async def close(self):
        await self.jupiter.close()
