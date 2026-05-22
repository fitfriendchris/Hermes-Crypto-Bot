"""
FEE CALCULATOR
Accounts for Jupiter swap costs: slippage + price impact + priority fee.
Used by entry sizing and PnL tracking to ensure accurate profit calculations.
"""

# Jupiter v6 fee structure (conservative estimates)
DEFAULT_SLIPPAGE_BPS = 300  # 3%
JUPITER_PLATFORM_FEE_BPS = 0  # Jupiter doesn't charge platform fee, but DEXs do
PRIORITY_FEE_SOL = 0.000005  # 5,000 lamports ~ $0.001 at $80/SOL

# DEX fees vary by route:
# - Raydium: 0.25% (25 bps)
# - Orca: 0.3% (30 bps)
# - Phoenix: 0.05% (5 bps)
# Average blended: ~0.25%
DEX_FEE_BPS = 25

def estimate_swap_cost(position_size_usd: float, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> dict:
    """
    Estimate total cost for a round-trip swap (buy + sell).
    
    Returns:
        {
            'slippage_pct': float,
            'dex_fee_pct': float,
            'priority_fee_usd': float,
            'total_entry_pct': float,   # one-way cost %
            'total_roundtrip_pct': float,  # buy + sell cost %
            'entry_cost_usd': float,
            'roundtrip_cost_usd': float,
        }
    """
    slippage_pct = slippage_bps / 10000  # 300 bps = 3%
    dex_fee_pct = DEX_FEE_BPS / 10000    # 25 bps = 0.25%
    
    # One-way: slippage + DEX fee (priority fee is negligible)
    total_entry_pct = slippage_pct + dex_fee_pct
    entry_cost_usd = position_size_usd * total_entry_pct
    
    # Round-trip: 2x one-way costs
    total_roundtrip_pct = total_entry_pct * 2
    roundtrip_cost_usd = position_size_usd * total_roundtrip_pct
    
    return {
        'slippage_pct': slippage_pct,
        'dex_fee_pct': dex_fee_pct,
        'priority_fee_usd': PRIORITY_FEE_SOL * 80,  # ~$0.40 at current SOL price
        'total_entry_pct': total_entry_pct,
        'total_roundtrip_pct': total_roundtrip_pct,
        'entry_cost_usd': entry_cost_usd,
        'roundtrip_cost_usd': roundtrip_cost_usd,
    }

def apply_entry_cost(position_size_usd: float, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> float:
    """
    Reduce position size by estimated entry cost.
    Ensures net position (after fees) still meets $2.50 minimum.
    """
    cost = estimate_swap_cost(position_size_usd, slippage_bps)
    net_size = position_size_usd - cost['entry_cost_usd']
    return max(net_size, 2.50)  # Never go below $2.50

def calculate_net_pnl(gross_pnl_usd: float, position_size_usd: float, slippage_bps: int = DEFAULT_SLIPPAGE_BPS) -> dict:
    """
    Calculate true PnL after accounting for round-trip fees.
    
    Returns:
        {
            'gross_pnl': float,
            'fees_usd': float,
            'net_pnl': float,
            'net_pnl_pct': float,
        }
    """
    cost = estimate_swap_cost(position_size_usd, slippage_bps)
    fees = cost['roundtrip_cost_usd']
    net = gross_pnl_usd - fees
    
    return {
        'gross_pnl': gross_pnl_usd,
        'fees_usd': fees,
        'net_pnl': net,
        'net_pnl_pct': (net / position_size_usd * 100) if position_size_usd > 0 else 0,
    }

# Quick reference for position sizes:
# $2.50 position:  round-trip cost ~ $0.16 (6.5%)
# $5.00 position:  round-trip cost ~ $0.33 (6.5%)
# $10.00 position: round-trip cost ~ $0.65 (6.5%)
# $20.00 position: round-trip cost ~ $1.30 (6.5%)
