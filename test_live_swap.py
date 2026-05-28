#!/usr/bin/env python3
"""Execute a REAL $0.50 SOL → USDC swap to test live path."""
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from HERMES_SWAP_EXECUTOR import SwapManager as SM
from HERMES_wallet_integration import WalletManager

async def main():
    wallet = WalletManager()
    ok = await wallet.initialize(chain="solana", wallet="exodus")
    print(f"Wallet init: {ok}")
    if not ok:
        return

    addr = wallet.get_address()
    bal = await wallet.get_balance()
    print(f"Address: {addr}")
    print(f"Balance: {bal:.4f} SOL (~${bal*82:.2f})")

    if bal < 0.02:
        print("ERROR: Not enough SOL for swap + fees")
        return

    sm = SM(wallet)
    await sm.initialize()

    usd = 0.50
    lamports = await sm.usd_to_lamports(usd)
    print(f"\nExecuting REAL swap: ${usd} SOL → USDC")
    print(f"Lamports: {lamports}")

    result = await sm.execute_swap(
        input_mint=SM.SOL_MINT,
        output_mint=SM.USDC_MINT,
        amount_in=lamports,
        slippage_bps=300,
    )

    print(f"\nSuccess: {result.success}")
    print(f"Input: {result.amount_in} lamports")
    print(f"Output: {result.output_amount:.6f}")
    print(f"Price impact: {result.price_impact_pct:.2f}%")
    print(f"Tx: {result.tx_signature}")
    if result.error:
        print(f"Error: {result.error}")

    await sm.close()

if __name__ == "__main__":
    asyncio.run(main())
