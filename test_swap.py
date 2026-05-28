#!/usr/bin/env python3
"""Test Jupiter swap with $0.50 worth of SOL → USDC."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from HERMES_SWAP_EXECUTOR import SwapManager as SM, JupiterSwap
from HERMES_wallet_integration import WalletManager

async def main():
    wallet = WalletManager()
    ok = await wallet.initialize(chain="solana", wallet="exodus")
    print(f"Wallet init: {ok}")
    if not ok:
        return

    addr = wallet.get_address()
    print(f"Address: {addr}")

    bal = await wallet.get_balance()
    print(f"Balance: {bal:.4f} SOL")

    sm = SM(wallet)
    await sm.initialize()

    sol_price = await sm.get_sol_price()
    print(f"SOL price: ${sol_price:.2f}")

    usd = 0.50
    lamports = await sm.usd_to_lamports(usd)
    print(f"${usd} = {lamports} lamports")

    # Test quote only — DO NOT execute
    jup = JupiterSwap()
    await jup.initialize()

    quote = await jup.get_quote(
        input_mint=SM.SOL_MINT,
        output_mint=SM.USDC_MINT,
        amount_in=lamports,
        slippage_bps=300,
    )
    print(f"Quote OK: {quote is not None}")
    if quote:
        out = int(quote.get("outAmount", 0))
        print(f"Out amount: {out / 1e6:.4f} USDC")
        impact = float(quote.get("priceImpactPct", 0))
        print(f"Price impact: {impact:.2f}%")
    await jup.close()

if __name__ == "__main__":
    asyncio.run(main())
