"""
hermes_v2.py — Hermes Solana Bot v2
Main orchestrator. Runs discovery, risk scan, copy-trade, safety circuits.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from config_loader import HermesConfig, load_config
from copy_engine import CopyEngine
from discovery_engine import DiscoveryEngine
from rpc_manager import RPCManager
from risk_scanner import RiskScanner
from safety_circuits import SafetyCircuits
from state_manager import StateManager
from swap_engine import SwapEngine
from tax_exporter import TaxExporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.FileHandler("logs/hermes_v2.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("hermes_v2")


class HermesBot:
    """Main bot orchestrator."""

    def __init__(self) -> None:
        self.cfg: HermesConfig | None = None
        self.rpc: RPCManager | None = None
        self.state: StateManager | None = None
        self.safety: SafetyCircuits | None = None
        self.risk: RiskScanner | None = None
        self.discovery: DiscoveryEngine | None = None
        self.swap: SwapEngine | None = None
        self.copy: CopyEngine | None = None
        self.tax: TaxExporter | None = None
        self._running = False

    async def initialize(self) -> None:
        """Initialize all modules."""
        logger.info("🚀 Hermes v2 initializing...")

        # Config
        self.cfg = load_config()
        logger.info("Mode: %s | Capital: $%.2f", self.cfg.mode, self.cfg.capital_usd)

        # State
        self.state = StateManager("state/hermes_state.json")
        st = self.state.load()
        if st.balance_usd == 0:
            st.balance_usd = self.cfg.capital_usd
            st.peak_balance_usd = self.cfg.capital_usd
            st.day_start_balance = self.cfg.capital_usd
            st.week_start_balance = self.cfg.capital_usd
            self.state.save()
        logger.info("Balance: $%.2f | Positions: %d", st.balance_usd, len(st.positions))

        # RPC
        self.rpc = RPCManager(
            primary_url=self.cfg.rpc.primary_url,
            fallback_url=self.cfg.rpc.fallback_url,
            public_url=self.cfg.rpc.public_url,
            timeout_seconds=self.cfg.rpc.timeout_seconds,
            max_retries=self.cfg.rpc.max_retries,
        )
        await self.rpc.initialize()
        logger.info("RPC: %s", self.rpc.get_endpoint_name())

        # Safety
        self.safety = SafetyCircuits(
            daily_loss_limit_usd=self.cfg.safety.daily_loss_limit_usd,
            weekly_loss_limit_usd=self.cfg.safety.weekly_loss_limit_usd,
            max_drawdown_pct=self.cfg.safety.max_drawdown_pct,
            consecutive_loss_halt=self.cfg.safety.consecutive_loss_halt,
            rug_halt_threshold=self.cfg.safety.rug_halt_threshold,
            rpc_failure_rate_threshold=self.cfg.safety.rpc_failure_rate_threshold,
            paper_mode_gate_hours=self.cfg.safety.paper_mode_gate_hours,
        )
        allowed, reason = self.safety.can_trade(st.balance_usd)
        logger.info("Safety: %s", "✅ PASS" if allowed else f"❌ {reason}")

        # Risk scanner
        self.risk = RiskScanner(
            rpc_manager=self.rpc,
            birdeye_api_key="",  # TODO: load from env
            max_rug_score=self.cfg.risk.max_rug_score,
        )
        logger.info("Risk scanner ready (max_score=%d)", self.cfg.risk.max_rug_score)

        # Discovery
        self.discovery = DiscoveryEngine(
            risk_scanner=self.risk,
            birdeye_api_key="",
            max_rug_score=self.cfg.risk.max_rug_score,
            min_liquidity_usd=self.cfg.position.min_liquidity_usd,
            min_volume_24h_usd=self.cfg.position.min_volume_24h_usd,
        )
        logger.info("Discovery engine ready")

        # Tax
        self.tax = TaxExporter()
        logger.info("Tax exporter ready")

        # Swap engine (only if wallet configured and valid)
        self.swap = None
        if self.cfg.mode == "live" and self.cfg.wallet.private_key_base58:
            try:
                from solders.keypair import Keypair
                wallet = Keypair.from_base58_string(self.cfg.wallet.private_key_base58)
                self.swap = SwapEngine(
                    rpc_manager=self.rpc,
                    wallet_keypair=wallet,
                    default_slippage_bps=self.cfg.position.default_slippage_bps,
                    max_slippage_bps=self.cfg.position.max_slippage_bps,
                )
                logger.info("Swap engine ready (wallet=%s...)", wallet.pubkey())
            except Exception as exc:
                logger.error("Failed to load wallet: %s — swap engine disabled", exc)
                self.swap = None
        else:
            logger.info("Swap engine: disabled (paper mode or no wallet)")

        # Copy engine
        if self.swap:
            self.copy = CopyEngine(
                config=self.cfg,
                rpc_manager=self.rpc,
                swap_engine=self.swap,
                state_manager=self.state,
                safety=self.safety,
            )
            logger.info("Copy engine ready")
        else:
            self.copy = None

        logger.info("✅ Hermes v2 initialized")

    async def close(self) -> None:
        """Graceful shutdown."""
        logger.info("🛑 Shutting down...")
        self._running = False
        if self.discovery:
            await self.discovery.close()
        if self.risk:
            await self.risk.close()
        if self.rpc:
            await self.rpc.close()
        if self.swap:
            await self.swap.close()
        logger.info("👋 Hermes v2 stopped")

    # ------------------------------------------------------------------ #
    # Main loop
    # ------------------------------------------------------------------ #
    async def run(self) -> None:
        """Main trading loop."""
        self._running = True
        cycle = 0

        while self._running:
            cycle += 1
            logger.info("=" * 50)
            logger.info("Cycle %d | Balance: $%.2f | Mode: %s",
                       cycle, self.state.state.balance_usd, self.cfg.mode)

            try:
                await self._run_cycle()
            except Exception as exc:
                logger.exception("Cycle error: %s", exc)

            # Sleep between cycles
            await asyncio.sleep(60)

    async def _run_cycle(self) -> None:
        """Single trading cycle."""
        # 1. Safety check
        allowed, reason = self.safety.can_trade(self.state.state.balance_usd)
        if not allowed:
            logger.warning("Circuit: %s", reason)
            return

        # 2. Discovery
        logger.info("🔍 Discovering tokens...")
        try:
            tokens = await self.discovery.discover()
            logger.info("Found %d safe tokens", len(tokens))
        except Exception as exc:
            logger.warning("Discovery failed: %s", exc)
            tokens = []

        # 3. For paper mode: simulate some activity
        if self.cfg.mode == "paper":
            logger.info("📄 PAPER MODE — no real trades")
            # TODO: Simulate trades against historical data or mock prices
            return

        # 4. Live: check for copy-trade opportunities
        if self.copy:
            # TODO: Implement wallet monitoring + mirroring
            logger.info("Copy engine: %d wallets tracked", len(self.copy.wallets))

        # 5. Status report
        logger.info(self.safety.status())
        if self.copy:
            logger.info(self.copy.status())

    # ------------------------------------------------------------------ #
    # Signals
    # ------------------------------------------------------------------ #
    def _signal_handler(self, sig: int, frame: Any) -> None:
        logger.info("Received signal %d", sig)
        asyncio.create_task(self.close())


async def main() -> None:
    bot = HermesBot()
    try:
        await bot.initialize()
        await bot.run()
    except KeyboardInterrupt:
        pass
    finally:
        await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
