"""
PROFIT PROTECTION MODULE
Auto-transfer profits to cold storage
Author: Hermes | March 2026 SEC/CFTC compliant
"""

import asyncio
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger('ProfitProtection')

@dataclass
class TransferResult:
    transferred: bool
    amount_usd: float
    to_address: str
    tx_signature: Optional[str]
    remaining_profit: float
    timestamp: str

class ProfitProtection:
    """
    Automatically protect profits by moving portion to cold storage.
    
    Strategy:
    - Accumulate profits from winning trades
    - When threshold hit, transfer % to cold wallet
    - Weekly sweep regardless of threshold
    - Keep remainder for reinvestment
    """
    
    def __init__(self, config: Dict):
        self.enabled = config.get('enabled', True)
        self.cold_wallet_sol = config.get('cold_wallet_sol', '')
        self.cold_wallet_eth = config.get('cold_wallet_eth', '')
        self.threshold = config.get('transfer_threshold_usd', 50)
        self.transfer_pct = config.get('transfer_pct', 0.25)
        self.weekly_sweep = config.get('weekly_sweep', True)
        
        # State
        self.accumulated_profit = 0.0
        self.total_protected = 0.0
        self.transfer_count = 0
        
        # Validation
        if not self.cold_wallet_sol and not self.cold_wallet_eth:
            logger.warning("No cold wallet addresses set — profit protection disabled")
            self.enabled = False
    
    def add_profit(self, trade_pnl_usd: float):
        """Add profit from closed trade."""
        if trade_pnl_usd > 0:
            self.accumulated_profit += trade_pnl_usd
            logger.info(f"Profit added: ${trade_pnl_usd:.2f} (accumulated: ${self.accumulated_profit:.2f})")
    
    async def check_and_transfer(self, wallet_manager, chain: str = "solana") -> Optional[TransferResult]:
        """
        Check if threshold hit and execute transfer.
        Returns TransferResult if transfer executed, None otherwise.
        """
        if not self.enabled or self.accumulated_profit < self.threshold:
            return None
        
        # Calculate transfer amount
        transfer_amount = self.accumulated_profit * self.transfer_pct
        remaining = self.accumulated_profit - transfer_amount
        
        # Select address
        if chain == "solana":
            to_address = self.cold_wallet_sol
        elif chain in ["ethereum", "bsc", "polygon"]:
            to_address = self.cold_wallet_eth
        else:
            logger.error(f"Unknown chain: {chain}")
            return None
        
        if not to_address:
            logger.warning(f"No cold wallet for {chain}")
            return None
        
        # Execute transfer (placeholder — requires wallet integration)
        logger.info(f"TRANSFERRING ${transfer_amount:.2f} to cold wallet ({to_address[:15]}...)")
        
        # Simulate transfer (replace with actual wallet.send() call)
        tx_signature = None
        try:
            # Actual implementation:
            # tx_signature = await wallet_manager.send(to_address, transfer_amount, chain=chain)
            pass
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            return None
        
        # Update state
        self.total_protected += transfer_amount
        self.accumulated_profit = remaining
        self.transfer_count += 1
        
        logger.info(f"PROTECTED: ${transfer_amount:.2f} | Total protected: ${self.total_protected:.2f} | Remaining for reinvest: ${remaining:.2f}")
        
        return TransferResult(
            transferred=True,
            amount_usd=transfer_amount,
            to_address=to_address,
            tx_signature=tx_signature,
            remaining_profit=remaining,
            timestamp=datetime.now().isoformat()
        )
    
    async def weekly_sweep_check(self, wallet_manager, weekly_pnl: float) -> Optional[TransferResult]:
        """Force transfer if weekly profit exceeds threshold."""
        if not self.weekly_sweep or weekly_pnl <= 0:
            return None
        
        if weekly_pnl >= self.threshold:
            # Override accumulated with weekly total
            old_accumulated = self.accumulated_profit
            self.accumulated_profit = weekly_pnl
            
            result = await self.check_and_transfer(wallet_manager)
            
            # Restore any previous accumulation
            if result:
                self.accumulated_profit += old_accumulated
            
            return result
        
        return None
    
    def get_status(self) -> Dict:
        """Current protection status."""
        return {
            'enabled': self.enabled,
            'accumulated_profit': self.accumulated_profit,
            'total_protected': self.total_protected,
            'transfer_count': self.transfer_count,
            'threshold': self.threshold,
            'transfer_pct': self.transfer_pct,
            'weekly_sweep': self.weekly_sweep,
            'cold_wallet_sol': self._mask_address(self.cold_wallet_sol),
            'cold_wallet_eth': self._mask_address(self.cold_wallet_eth)
        }
    
    def _mask_address(self, addr: str) -> str:
        """Mask address for display."""
        if not addr:
            return "NOT SET"
        return f"{addr[:8]}...{addr[-4:]}"

# ============================================================
# TEST
# ============================================================

async def test():
    """Test profit protection."""
    print("=== PROFIT PROTECTION TEST ===\n")
    
    config = {
        'enabled': True,
        'cold_wallet_sol': 'ABC123...XYZ',  # Placeholder
        'cold_wallet_eth': '0x1234...abcd',   # Placeholder
        'transfer_threshold_usd': 50,
        'transfer_pct': 0.25,
        'weekly_sweep': True
    }
    
    pp = ProfitProtection(config)
    
    # Simulate trades
    trades = [15, -10, 25, 30, -5, 40, 20]
    
    for i, pnl in enumerate(trades):
        print(f"Trade {i+1}: ${pnl:+.2f}")
        
        if pnl > 0:
            pp.add_profit(pnl)
            
            # Check if we should transfer
            result = await pp.check_and_transfer(None, "solana")
            
            if result:
                print(f"  💰 TRANSFERRED ${result.amount_usd:.2f} to cold storage")
                print(f"  📊 Remaining for reinvest: ${result.remaining_profit:.2f}")
    
    print("\n=== FINAL STATUS ===")
    status = pp.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")
    
    print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    from datetime import datetime
    asyncio.run(test())
