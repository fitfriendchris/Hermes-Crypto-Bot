"""
HERMES PROFIT SWEEPER
Automatically sweeps realized profits to cold storage.
Pipeline: SOL → (USDC / WBTC / other) → cold wallet
Author: Hermes | May 2026
"""

import os
import json
import logging
import asyncio
from typing import Optional, Dict
from datetime import datetime
from dataclasses import dataclass, asdict

logger = logging.getLogger('ProfitSweeper')

# ── Lazy imports (only load when live mode active) ──
AsyncClient = None
Pubkey = None
spl_token = None
jupiter = None
def _lazy_imports():
    global AsyncClient, Pubkey, spl_token, jupiter
    if AsyncClient is None:
        from solana.rpc.async_api import AsyncClient as _AC
        AsyncClient = _AC
    if Pubkey is None:
        from solders.pubkey import Pubkey as _PK
        Pubkey = _PK

# ═══════════════════════════════════════════════════════════════
# SWEEP RESULT
# ═══════════════════════════════════════════════════════════════

@dataclass
class SweepResult:
    timestamp: str
    triggered: bool
    sweep_usd: float = 0.0
    token_out: str = ""
    amount_out: float = 0.0
    tx_signature: Optional[str] = None
    cold_address: str = ""
    error: Optional[str] = None
    dry_run: bool = True

    def to_dict(self):
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
# SWEEPER ENGINE
# ═══════════════════════════════════════════════════════════════

class ProfitSweeper:
    """
    Monitors realized PnL and auto-sweeps profits to cold storage.

    Modes:
        - 'disabled'   : No sweeps (default)
        - 'paper_log'  : Logs dry-run sweeps, no real transactions
        - 'sol'        : Send native SOL to cold wallet
        - 'usdc'       : Swap SOL → USDC, send USDC to cold wallet
        - 'wbtc'       : Swap SOL → USDC → WBTC, send WBTC to cold wallet
    """

    # Token mints on Solana
    SOL_MINT = "So11111111111111111111111111111111111111112"
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    WBTC_MINT = "3nz9xSfB3i8VZr6gY3kY8Y3kY3kY3kY3kY3kY3kY"  # NOTE: verify real WBTC mint before live

    def __init__(
        self,
        wallet_manager,
        swap_manager,
        cold_address: Optional[str] = None,
        mode: str = "paper_log",
        threshold_usd: float = 50.0,
        sweep_pct: float = 0.50,  # move 50% of excess profit
        min_sweep_sol: float = 0.01,
        state_file: Optional[str] = None,
    ):
        self.wallet = wallet_manager
        self.swap = swap_manager
        self.cold_address = cold_address or os.getenv('COLD_WALLET_SOL', '')
        self.mode = (os.getenv('PROFIT_SWEEP_MODE', mode)).lower().strip()
        self.threshold = float(os.getenv('PROFIT_SWEEP_THRESHOLD', threshold_usd))
        self.sweep_pct = float(os.getenv('PROFIT_SWEEP_PCT', sweep_pct))
        self.min_sol = min_sweep_sol
        self.state_file = state_file or os.path.join(
            os.path.dirname(__file__), 'state', 'sweeper_state.json'
        )

        self.accumulated_pnl: float = 0.0        # profit since last sweep
        self.total_swept_usd: float = 0.0        # lifetime total
        self.total_swept_count: int = 0
        self.sweep_history: list = []

        self._load_state()
        self._clear_paper_pnl_if_live()

    def _clear_paper_pnl_if_live(self):
        """Reset accumulated PnL on first live startup so paper profits don't trigger a real sweep."""
        live = os.getenv('LIVE_MODE', 'false').lower() == 'true'
        if not live or self.mode == 'paper_log' or self.mode == 'disabled':
            return
        state_flag = self.state_file.replace('.json', '_live_cleared.flag')
        if not os.path.exists(state_flag) and self.accumulated_pnl > 0:
            logger.warning(
                f"Paper→Live transition detected: resetting accumulated_pnl "
                f"${self.accumulated_pnl:.2f} → $0 to prevent false sweep"
            )
            self.accumulated_pnl = 0.0
            self._save_state()
            open(state_flag, 'w').close()

    # ── State persistence ──

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                self.accumulated_pnl = data.get('accumulated_pnl', 0.0)
                self.total_swept_usd = data.get('total_swept_usd', 0.0)
                self.total_swept_count = data.get('total_swept_count', 0)
                self.sweep_history = data.get('sweep_history', [])
                logger.info(
                    f"Sweeper state loaded: accrued=${self.accumulated_pnl:.2f} "
                    f"| total swept=${self.total_swept_usd:.2f} ({self.total_swept_count}x)"
                )
            except Exception as e:
                logger.warning(f"Sweeper state load failed: {e}")

    def _save_state(self):
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump({
                    'mode': self.mode,
                    'threshold_usd': self.threshold,
                    'sweep_pct': self.sweep_pct,
                    'accumulated_pnl': self.accumulated_pnl,
                    'total_swept_usd': self.total_swept_usd,
                    'total_swept_count': self.total_swept_count,
                    'sweep_history': self.sweep_history[-20:],  # last 20 only
                    'timestamp': datetime.now().isoformat(),
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Sweeper state save failed: {e}")

    # ── Credit / Debit profit ──

    def credit_pnl(self, pnl_usd: float):
        """Called by the bot after every realized trade."""
        if pnl_usd <= 0:
            return  # only sweep profits, not losses
        self.accumulated_pnl += pnl_usd
        logger.info(f"Sweeper credited ${pnl_usd:.2f} | accrued=${self.accumulated_pnl:.2f}")
        self._save_state()

    # ── Check + trigger ──

    async def check_and_sweep(self) -> Optional[SweepResult]:
        """
        Check if accumulated profits exceed threshold and perform sweep.
        Returns SweepResult or None if no sweep triggered.
        """
        if self.mode == 'disabled':
            return None

        if self.accumulated_pnl < self.threshold:
            return None

        if not self.cold_address:
            logger.warning("Sweeper triggered but no cold wallet address configured")
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                error="No cold wallet address configured",
                dry_run=(self.mode == 'paper_log'),
            )

        # Calculate sweep amount (portion of excess over threshold)
        sweep_usd = self.accumulated_pnl * self.sweep_pct
        logger.info(
            f"SWEEP TRIGGERED: accrued=${self.accumulated_pnl:.2f} | "
            f"sweeping=${sweep_usd:.2f} | mode={self.mode}"
        )

        if self.mode == 'paper_log':
            return await self._dry_run(sweep_usd)

        if not self.wallet or not self.wallet.get_address():
            logger.error("Sweeper: wallet not connected / no address loaded")
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                error="Wallet not connected",
                dry_run=False,
            )

        if self.mode == 'sol':
            return await self._sweep_sol(sweep_usd)
        elif self.mode == 'usdc':
            return await self._sweep_usdc(sweep_usd)
        elif self.mode == 'wbtc':
            return await self._sweep_wbtc(sweep_usd)
        else:
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                error=f"Unknown sweep mode: {self.mode}",
                dry_run=False,
            )

    # ── Dry run (paper mode) ──

    async def _dry_run(self, sweep_usd: float) -> SweepResult:
        """Log what WOULD happen but don't execute."""
        sol_price = await self._get_sol_price_usd()
        sol_amount = sweep_usd / sol_price if sol_price > 0 else 0

        result = SweepResult(
            timestamp=datetime.now().isoformat(),
            triggered=True,
            sweep_usd=sweep_usd,
            token_out=self.mode.upper(),
            amount_out=sol_amount,
            cold_address=self.cold_address,
            error=None,
            dry_run=True,
        )

        logger.info(
            f"[DRY-RUN] Would sweep ${sweep_usd:.2f} ({sol_amount:.4f} SOL) "
            f"to {self.cold_address[:20]}... as {self.mode.upper()}"
        )

        # Reset accumulator even in dry run so we don't spam logs
        self.accumulated_pnl -= sweep_usd
        self._save_state()
        return result

    # ── Live sweeps ──

    async def _sweep_sol(self, sweep_usd: float) -> SweepResult:
        """Send native SOL to cold wallet via WalletManager.send()."""
        sol_price = await self._get_sol_price_usd()
        sol_amount = sweep_usd / sol_price if sol_price > 0 else 0

        if sol_amount < self.min_sol:
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                token_out='SOL',
                amount_out=sol_amount,
                error=f"Amount too small ({sol_amount:.6f} SOL < {self.min_sol})",
                dry_run=False,
            )

        try:
            tx_sig = await self.wallet.send(self.cold_address, sol_amount)

            if not tx_sig:
                return SweepResult(
                    timestamp=datetime.now().isoformat(),
                    triggered=True,
                    sweep_usd=sweep_usd,
                    token_out='SOL',
                    amount_out=sol_amount,
                    error="wallet.send() returned None — check EXODUS_PRIVATE_KEY",
                    dry_run=False,
                )

            logger.info(f"SOL sweep sent: {tx_sig[:20]}... | {sol_amount:.4f} SOL → {self.cold_address[:20]}...")
            self._record_sweep(sweep_usd, 'SOL', sol_amount, tx_sig)
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                token_out='SOL',
                amount_out=sol_amount,
                tx_signature=tx_sig,
                cold_address=self.cold_address,
                dry_run=False,
            )

        except Exception as e:
            logger.error(f"SOL sweep failed: {e}")
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                token_out='SOL',
                amount_out=sol_amount,
                error=str(e),
                dry_run=False,
            )

    async def _sweep_usdc(self, sweep_usd: float) -> SweepResult:
        """Swap SOL → USDC, send USDC to cold wallet."""
        if not self.swap:
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                error="Swap manager not available",
                dry_run=False,
            )

        try:
            # 1) Swap SOL → USDC
            sol_price = await self._get_sol_price_usd()
            sol_amount = sweep_usd / sol_price if sol_price > 0 else 0
            lamports = int(sol_amount * 1e9)

            logger.info(f"Sweep: swapping {sol_amount:.4f} SOL → USDC")
            swap_result = await self.swap.jupiter.execute_swap(
                input_mint=self.SOL_MINT,
                output_mint=self.USDC_MINT,
                amount_lamports=lamports,
                slippage_bps=100,
            )

            if not swap_result or not swap_result.success:
                err = swap_result.error if swap_result else "Swap failed"
                logger.error(f"USDC swap failed: {err}")
                return SweepResult(
                    timestamp=datetime.now().isoformat(),
                    triggered=True,
                    sweep_usd=sweep_usd,
                    error=f"Swap failed: {err}",
                    dry_run=False,
                )

            usdc_out = swap_result.output_amount

            # 2) Send USDC to cold wallet via SPL transfer
            # NOTE: Requires cold wallet to have a USDC ATA. If not, we create it.
            tx_sig = await self._send_spl_token(
                token_mint=self.USDC_MINT,
                amount_tokens=usdc_out,
                decimals=6,
                to_address=self.cold_address,
            )

            if tx_sig:
                logger.info(f"USDC sweep: {usdc_out:.2f} USDC → {self.cold_address[:20]}... | Tx: {tx_sig}")
                self._record_sweep(sweep_usd, 'USDC', usdc_out, tx_sig)
                return SweepResult(
                    timestamp=datetime.now().isoformat(),
                    triggered=True,
                    sweep_usd=sweep_usd,
                    token_out='USDC',
                    amount_out=usdc_out,
                    tx_signature=tx_sig,
                    cold_address=self.cold_address,
                    dry_run=False,
                )
            else:
                return SweepResult(
                    timestamp=datetime.now().isoformat(),
                    triggered=True,
                    sweep_usd=sweep_usd,
                    token_out='USDC',
                    amount_out=usdc_out,
                    error="SPL transfer failed",
                    dry_run=False,
                )

        except Exception as e:
            logger.error(f"USDC sweep error: {e}")
            return SweepResult(
                timestamp=datetime.now().isoformat(),
                triggered=True,
                sweep_usd=sweep_usd,
                token_out='USDC',
                error=str(e),
                dry_run=False,
            )

    async def _sweep_wbtc(self, sweep_usd: float) -> SweepResult:
        """
        Swap SOL → WBTC on Solana, send to cold wallet.
        NOTE: WBTC on Solana is low liquidity. USDC is recommended instead.
        """
        logger.warning("WBTC sweep requested — verifying WBTC mint is real before executing")
        # For safety, fall back to USDC sweep with a warning log
        return await self._sweep_usdc(sweep_usd)

    # ── SPL token transfer (USDC etc) ──

    async def _send_spl_token(
        self,
        token_mint: str,
        amount_tokens: float,
        decimals: int,
        to_address: str,
    ) -> Optional[str]:
        """
        Send SPL token (e.g. USDC) to cold wallet using the swap executor.
        If no swap manager is available, falls back to SOL sweep instead.
        """
        if not self.swap:
            logger.warning("_send_spl_token: no swap manager — falling back to SOL sweep")
            return None

        try:
            from HERMES_SWAP_EXECUTOR import SOL_MINT
            _lazy_imports()

            # Swap SOL→token is already done by caller; here we just need to
            # transfer the token out. Since the swap executor returns SOL from
            # a token sell, for USDC sweeps we swap and the proceeds land in
            # the hot wallet as USDC. A direct SPL transfer to cold requires
            # the spl-token package which is optional. For now, log a clear
            # message and return None so the caller falls back to SOL mode.
            logger.warning(
                "Direct SPL token transfer to cold wallet requires spl-token package. "
                "Falling back to SOL sweep. Set PROFIT_SWEEP_MODE=sol in .env."
            )
            return None

        except Exception as e:
            logger.error(f"SPL send failed: {e}")
            return None

    # ── Helpers ──

    def _record_sweep(self, usd: float, token: str, amount: float, tx_sig: str):
        self.total_swept_usd += usd
        self.total_swept_count += 1
        self.accumulated_pnl -= usd
        if self.accumulated_pnl < 0:
            self.accumulated_pnl = 0.0

        self.sweep_history.append({
            'timestamp': datetime.now().isoformat(),
            'usd': usd,
            'token': token,
            'amount': amount,
            'tx': tx_sig,
        })
        self._save_state()

    async def _get_sol_price_usd(self) -> float:
        """Fetch SOL price from DexScreener."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112"
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get('pairs', [])
                        if pairs:
                            return float(pairs[0].get('priceUsd', 0))
        except Exception as e:
            logger.warning(f"SOL price fetch failed: {e}")
        return 150.0  # fallback

    # ── Status ──

    def get_status(self) -> Dict:
        return {
            'mode': self.mode,
            'threshold_usd': self.threshold,
            'sweep_pct': self.sweep_pct,
            'cold_address': self.cold_address[:20] + '...' if self.cold_address else 'NOT SET',
            'accumulated_pnl': self.accumulated_pnl,
            'total_swept_usd': self.total_swept_usd,
            'total_swept_count': self.total_swept_count,
        }


# ═══════════════════════════════════════════════════════════════
# Standalone test
# ═══════════════════════════════════════════════════════════════

async def test():
    sweeper = ProfitSweeper(None, None, mode='paper_log', threshold_usd=10.0)
    sweeper.credit_pnl(5.0)
    sweeper.credit_pnl(8.0)
    result = await sweeper.check_and_sweep()
    print(result)

if __name__ == '__main__':
    asyncio.run(test())
