"""Per-trade cost model for v3 backtests.

Total cost components (round-trip, expressed as fraction of notional):
  - Jupiter / DEX routing fee     ~ 0.0005 (5 bps)
  - Priority + base tx fee        ~ 0.001  (~ $0.10 on a $100 trade at common SOL prices)
  - Slippage (each leg)           ~ f(trade_size / pool_liquidity); see below
  - MEV / sandwich variance       ~ 0.001 sigma (constant tax in expectation)

This module returns the *total* round-trip cost as a decimal so the harness
can deduct it from each trade's gross return.
"""

from typing import Optional


def slippage_one_side(trade_size_usd: float, pool_liquidity_usd: float) -> float:
    """
    Sqrt-impact model: slippage scales with (size / liquidity)^0.5.
    Caps at 5% per leg (pathologically thin pool).
    """
    if pool_liquidity_usd <= 0:
        return 0.05
    ratio = trade_size_usd / pool_liquidity_usd
    # Calibrated against Jupiter/Raydium impact curves observed Q1 2026:
    # $100 into $20K pool ≈ 0.5% impact; $1000 ≈ 1.6%
    return min(0.05, 0.06 * (ratio ** 0.5))


def round_trip_cost_pct(trade_size_usd: float,
                         pool_liquidity_usd: float,
                         dex_fee_bps: float = 5,
                         tx_fee_usd: float = 0.10,
                         mev_tax_pct: float = 0.001) -> float:
    """
    Returns total round-trip cost as a fraction of notional. Use as a direct
    deduction from gross PnL %.
    """
    dex_fee = (dex_fee_bps / 10_000) * 2  # buy + sell
    slip_in = slippage_one_side(trade_size_usd, pool_liquidity_usd)
    slip_out = slippage_one_side(trade_size_usd, pool_liquidity_usd)
    tx = (tx_fee_usd / trade_size_usd) if trade_size_usd > 0 else 0
    return dex_fee + slip_in + slip_out + tx + mev_tax_pct


def apply_costs(gross_pnl_pct: float,
                trade_size_usd: float,
                pool_liquidity_usd: float) -> float:
    """Net of round-trip cost. gross_pnl_pct expressed as decimal (e.g. 0.05 = +5%)."""
    return gross_pnl_pct - round_trip_cost_pct(trade_size_usd, pool_liquidity_usd)
