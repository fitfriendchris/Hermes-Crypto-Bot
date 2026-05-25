"""
copy_engine.py — Hermes Solana Bot v2
Copy-trade engine: track verified wallets, mirror trades with sizing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config_loader import HermesConfig
from discovery_engine import DiscoveredToken
from rpc_manager import RPCManager
from safety_circuits import SafetyCircuits
from state_manager import StateManager
from swap_engine import SwapEngine, SwapResult

logger = logging.getLogger(__name__)


@dataclass
class TrackedWallet:
    address: str
    name: str = ""
    trust_level: str = "paper"  # "paper" | "0.5x" | "1.0x"
    trades_mirrored: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    peak_pnl: float = 0.0
    added_at: str = ""
    last_trade_at: str = ""
    is_active: bool = True


class CopyEngine:
    """Mirrors trades from verified profitable wallets.

    Trust ladder:
    1. paper — simulate only, no real money
    2. 0.5x  — live with half the calculated position size
    3. 1.0x  — full position size

    Wallet promotion/demotion based on performance.
    """

    def __init__(
        self,
        config: HermesConfig,
        rpc_manager: RPCManager,
        swap_engine: SwapEngine,
        state_manager: StateManager,
        safety: SafetyCircuits,
    ) -> None:
        self.cfg = config
        self.rpc = rpc_manager
        self.swap = swap_engine
        self.state = state_manager
        self.safety = safety
        self.wallets: dict[str, TrackedWallet] = {}
        self._load_wallets()

    def _load_wallets(self) -> None:
        """Load tracked wallets from state."""
        s = self.state.state
        for addr, data in s.copied_wallets.items():
            self.wallets[addr] = TrackedWallet(**data)

    def _save_wallets(self) -> None:
        s = self.state.state
        s.copied_wallets = {w.address: w.__dict__ for w in self.wallets.values()}
        self.state.save()

    # ------------------------------------------------------------------ #
    # Wallet management
    # ------------------------------------------------------------------ #
    def add_wallet(self, address: str, name: str = "", trust_level: str = "paper") -> TrackedWallet:
        """Add a new wallet to track."""
        if address in self.wallets:
            return self.wallets[address]
        if len(self.wallets) >= self.cfg.copy_trade.max_wallets:
            raise ValueError(f"Max {self.cfg.copy_trade.max_wallets} wallets reached")
        w = TrackedWallet(
            address=address,
            name=name or address[:8],
            trust_level=trust_level,
            added_at=_now(),
        )
        self.wallets[address] = w
        self._save_wallets()
        logger.info("Added wallet %s (trust=%s)", address[:8], trust_level)
        return w

    def remove_wallet(self, address: str) -> None:
        if address in self.wallets:
            del self.wallets[address]
            self._save_wallets()

    # ------------------------------------------------------------------ #
    # Discovery + verification
    # ------------------------------------------------------------------ #
    async def discover_wallets(self, min_trades: int | None = None) -> list[dict]:
        """Discover candidate wallets via Birdeye/DexScreener top traders.

        This is a placeholder — real implementation needs on-chain analysis
        of profitable wallets. For now, return empty list and rely on manual adds.
        """
        # TODO: Implement on-chain wallet performance analysis
        # - Query Solscan/Birdeye for top traders on trending tokens
        # - Filter by win rate, drawdown, trade count
        # - Return ranked list
        return []

    # ------------------------------------------------------------------ #
    # Trade mirroring
    # ------------------------------------------------------------------ #
    async def on_target_buy(
        self,
        wallet_address: str,
        token_mint: str,
        token_symbol: str,
        target_invested_usd: float,
        token_price_usd: float,
    ) -> SwapResult | None:
        """Called when a tracked wallet buys a token. Mirror if safe."""
        wallet = self.wallets.get(wallet_address)
        if not wallet or not wallet.is_active:
            return None

        # Check wallet kill criteria
        if not self._wallet_is_healthy(wallet):
            logger.info("Skipping mirror for unhealthy wallet %s", wallet.name)
            return None

        # Calculate position size
        size = self._calculate_position_size(wallet, target_invested_usd)
        if size <= 0:
            return None

        # Safety circuits
        allowed, reason = self.safety.can_trade(self.state.state.balance_usd)
        if not allowed:
            logger.info("Safety circuit blocked: %s", reason)
            return None

        if self.cfg.mode == "paper":
            # Simulate the trade
            logger.info("PAPER: Mirror buy %s $%.2f (copied %s)",
                       token_symbol, size, wallet.name)
            return None  # No real swap in paper mode

        # Live: execute swap
        # Convert size to lamports (simplified — assumes SOL input)
        # Real implementation needs token → SOL → token routing
        try:
            # This is simplified — real implementation needs proper routing
            # For now, log and return
            logger.info("LIVE: Would mirror buy %s $%.2f (copied %s)",
                       token_symbol, size, wallet.name)
            # TODO: Implement actual swap via swap_engine
            return None
        except Exception as exc:
            logger.error("Mirror buy failed: %s", exc)
            return None

    async def on_target_sell(
        self,
        wallet_address: str,
        token_mint: str,
    ) -> SwapResult | None:
        """Called when a tracked wallet sells a token. Mirror the sell."""
        # Check if we hold this token
        if token_mint not in self.state.state.positions:
            return None

        wallet = self.wallets.get(wallet_address)
        if not wallet or not wallet.is_active:
            return None

        if self.cfg.mode == "paper":
            logger.info("PAPER: Mirror sell %s (copied %s)",
                       token_mint[:8], wallet.name)
            return None

        # Live: sell our position
        logger.info("LIVE: Would mirror sell %s (copied %s)",
                   token_mint[:8], wallet.name)
        # TODO: Implement actual sell via swap_engine
        return None

    # ------------------------------------------------------------------ #
    # Sizing logic
    # ------------------------------------------------------------------ #
    def _calculate_position_size(
        self, wallet: TrackedWallet, target_invested_usd: float
    ) -> float:
        """Calculate our position size based on trust level and capital.

        Formula:
        - Base = min(target_invested_usd, cfg.base_position_size_usd)
        - Trust multiplier: paper=0, 0.5x=0.5, 1.0x=1.0
        - Max cap = cfg.max_position_size_usd
        """
        trust_mult = {"paper": 0.0, "0.5x": 0.5, "1.0x": 1.0}.get(wallet.trust_level, 0.0)
        if trust_mult == 0.0:
            return 0.0

        base = min(target_invested_usd, self.cfg.position.base_size_usd)
        size = base * trust_mult
        return min(size, self.cfg.position.max_size_usd)

    # ------------------------------------------------------------------ #
    # Wallet health
    # ------------------------------------------------------------------ #
    def _wallet_is_healthy(self, wallet: TrackedWallet) -> bool:
        """Check if wallet should still be copied."""
        cfg = self.cfg.copy_trade
        if wallet.consecutive_losses >= cfg.kill_consecutive_losses:
            return False
        if wallet.max_drawdown_pct >= cfg.kill_drawdown_pct:
            return False
        if wallet.total_pnl < 0 and abs(wallet.total_pnl) > self.cfg.capital_usd * 0.15:
            return False
        return True

    def update_wallet_performance(
        self, wallet_address: str, pnl: float
    ) -> None:
        """Update wallet stats after a mirrored trade closes."""
        wallet = self.wallets.get(wallet_address)
        if not wallet:
            return
        wallet.trades_mirrored += 1
        wallet.total_pnl += pnl
        if pnl > 0:
            wallet.wins += 1
            wallet.consecutive_losses = 0
        else:
            wallet.losses += 1
            wallet.consecutive_losses += 1
        if wallet.total_pnl > wallet.peak_pnl:
            wallet.peak_pnl = wallet.total_pnl
        dd = (wallet.peak_pnl - wallet.total_pnl) / abs(wallet.peak_pnl) * 100 if wallet.peak_pnl > 0 else 0
        wallet.max_drawdown_pct = max(wallet.max_drawdown_pct, dd)
        wallet.last_trade_at = _now()
        self._save_wallets()

    # ------------------------------------------------------------------ #
    # Trust ladder
    # ------------------------------------------------------------------ #
    def promote_wallet(self, address: str) -> None:
        """Promote wallet to next trust level after success."""
        wallet = self.wallets.get(address)
        if not wallet:
            return
        ladder = self.cfg.copy_trade.trust_ladder
        idx = ladder.index(wallet.trust_level) if wallet.trust_level in ladder else -1
        if idx >= 0 and idx < len(ladder) - 1:
            old = wallet.trust_level
            wallet.trust_level = ladder[idx + 1]
            logger.info("Promoted %s: %s → %s", wallet.name, old, wallet.trust_level)
            self._save_wallets()

    def demote_wallet(self, address: str) -> None:
        """Demote or remove wallet after failure."""
        wallet = self.wallets.get(address)
        if not wallet:
            return
        ladder = self.cfg.copy_trade.trust_ladder
        idx = ladder.index(wallet.trust_level) if wallet.trust_level in ladder else -1
        if idx > 0:
            old = wallet.trust_level
            wallet.trust_level = ladder[idx - 1]
            logger.info("Demoted %s: %s → %s", wallet.name, old, wallet.trust_level)
            self._save_wallets()
        else:
            # Already at bottom — deactivate
            wallet.is_active = False
            logger.warning("Deactivated %s (too many losses)", wallet.name)
            self._save_wallets()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> str:
        lines = ["📋 Copy-Trade Wallets"]
        for w in self.wallets.values():
            win_rate = w.wins / max(w.trades_mirrored, 1) * 100
            lines.append(
                f"  {w.name} [{w.trust_level}] | "
                f"mirrored={w.trades_mirrored} | "
                f"win_rate={win_rate:.0f}% | "
                f"pnl=${w.total_pnl:+.2f} | "
                f"dd={w.max_drawdown_pct:.1f}% | "
                f"active={'✅' if w.is_active else '❌'}"
            )
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    print("CopyEngine loaded. Import and use with full config.")
