"""
MEME COIN POSITION SIZING CALCULATOR
Ensures every trade can actually exit back to SOL in profit.
Author: Hermes | May 2026
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MemeTrade:
    symbol: str
    token_address: str
    entry_price: float
    position_usd: float
    liquidity_usd: float
    taker_fee: float = 0.02  # 2% Jupiter fee
    slippage_pct: float = 0.0  # Calculated from liquidity
    
    @property
    def slippage(self) -> float:
        """Estimated slippage for the position size."""
        return (self.position_usd / self.liquidity_usd) * 100
    
    @property
    def cost_basis(self) -> float:
        """Total cost including fees."""
        fee = self.position_usd * self.taker_fee
        slip = self.position_usd * (self.slippage / 100)
        return self.position_usd + fee + slip
    
    @property
    def breakeven_pct(self) -> float:
        """Price increase needed just to break even."""
        # Need to cover: entry slippage + exit slippage + fees on both sides
        total_friction = (self.slippage * 2) + (self.taker_fee * 2 * 100)
        return total_friction
    
    @property
    def min_profit_pct(self) -> float:
        """Minimum gain for a worthwhile trade (breakeven + 5% profit)."""
        return self.breakeven_pct + 5.0
    
    @property
    def max_safe_position(self) -> float:
        """Max position that doesn't cause >2% slippage."""
        return self.liquidity_usd * 0.02
    
    def is_viable(self, momentum_1h: float) -> bool:
        """Check if trade meets minimum viability criteria."""
        return (
            self.position_usd <= self.max_safe_position and
            self.slippage <= 2.0 and
            momentum_1h >= self.min_profit_pct
        )
    
    def exit_value(self, current_price: float) -> float:
        """Calculate exit value after fees and slippage."""
        # Exit slippage
        exit_slip = self.position_usd * (self.slippage / 100)
        # Exit fee
        exit_fee = self.position_usd * self.taker_fee
        # Raw profit/loss
        raw_value = self.position_usd * (current_price / self.entry_price)
        return raw_value - exit_slip - exit_fee
    
    def pnl(self, current_price: float) -> float:
        """Real PnL after all fees."""
        return self.exit_value(current_price) - self.cost_basis


def calculate_viable_trades(liquidity: float, momentum_1h: float, momentum_6h: float, symbol: str = "TOKEN") -> Optional[MemeTrade]:
    """
    Calculate if a token is viable for momentum trading.
    
    Returns viable trade params or None if not tradeable.
    """
    # Try $500 position
    for size in [500, 1000, 2000, 100]:
        trade = MemeTrade(
            symbol=symbol,
            token_address="",
            entry_price=0.0,
            position_usd=size,
            liquidity_usd=liquidity,
        )
        
        if trade.is_viable(momentum_1h):
            return trade
    
    return None


# Example analysis
if __name__ == "__main__":
    print("=" * 80)
    print("MEME COIN POSITION SIZING ANALYSIS")
    print("=" * 80)
    print()
    
    # Test various liquidity levels
    test_cases = [
        ("Micro-cap", 25000, 50.0),
        ("Small-cap", 50000, 30.0),
        ("Mid-cap", 150000, 20.0),
        ("Large-cap", 500000, 10.0),
        ("VINE (high liq)", 2000000, 5.0),
    ]
    
    for name, liq, momentum in test_cases:
        trade = calculate_viable_trades(liq, momentum, 0, name)
        
        print(f"\n{name} — Liquidity: ${liq:,.0f} | 1h Momentum: +{momentum}%")
        print("-" * 60)
        
        if trade:
            print(f"  Viable Position: ${trade.position_usd:,.0f}")
            print(f"  Slippage: {trade.slippage:.2f}%")
            print(f"  Breakeven: {trade.breakeven_pct:.1f}%")
            print(f"  Min Profit Target: {trade.min_profit_pct:.1f}%")
            print(f"  Status: ✅ VIABLE")
        else:
            print(f"  Status: ❌ NOT VIABLE — Liquidity too low for profitable exit")
            # Show what would be needed
            min_liq = 500 * 50  # $500 position * 50x for <2% slippage
            print(f"  Need ${min_liq:,.0f}+ liquidity for $500 trades")
    
    print("\n" + "=" * 80)
    print("LIVE TOKEN ANALYSIS")
    print("=" * 80)
    
    # Real tokens from scan
    tokens = [
        ("memecoins", 35000, 11.0, 107.0),
        ("Peace", 23000, 30.0, -10.0),
        ("MANIFEST", 205000, 14.6, 43.7),
        ("Goblin", 442000, 6.0, 6.4),
        ("67", 409000, 6.2, -2.8),
        ("VINE", 2000000, 1.4, 11.0),
        ("TROLL", 4000000, 0.5, -4.0),
    ]
    
    print(f"\n{'Token':12s} | {'Liq':>8s} | {'1h%':>6s} | {'6h%':>6s} | {'Pos':>6s} | {'Slip':>5s} | {'Break':>6s} | {'Target':>6s} | {'Status':>8s}")
    print("-" * 95)
    
    for symbol, liq, m1, m6 in tokens:
        trade = calculate_viable_trades(liq, m1, m6, symbol)
        
        if trade:
            status = "✅"
            pos = f"${trade.position_usd:.0f}"
            slip = f"{trade.slippage:.1f}%"
            be = f"{trade.breakeven_pct:.1f}%"
            target = f"{trade.min_profit_pct:.1f}%"
        else:
            status = "❌"
            pos = "N/A"
            slip = "N/A"
            be = "N/A"
            target = "N/A"
        
        print(f"{symbol:12s} | ${liq/1e3:6.0f}K | {m1:+5.1f}% | {m6:+5.1f}% | {pos:>6s} | {slip:>5s} | {be:>6s} | {target:>6s} | {status:>8s}")
    
    print("\n" + "=" * 80)
    print("RECOMMENDED MINIMUMS")
    print("=" * 80)
    print("""
For a $500 position to be viable:
  - Minimum liquidity: $50,000 (for <1% slippage)
  - Minimum 1h momentum: 10% (to cover fees + profit)
  - Maximum hold time: 24 hours
  - Hard stop: -15%
  
For a $1,000 position:
  - Minimum liquidity: $100,000
  - Minimum 1h momentum: 10%
  
For a $2,000 position:
  - Minimum liquidity: $200,000
  - Minimum 1h momentum: 10%
    """)
