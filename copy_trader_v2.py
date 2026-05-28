#!/usr/bin/env python3
"""
COPY TRADER v2 — Mirror profitable wallets with smart sizing
Replaces the old skeleton copy_trader.py with a real system.

Flow:
1. Wallet scorer ranks wallets by PnL/Sharpe/win rate
2. Copy engine monitors top N wallets for new buys
3. On buy detection: mirror with proportional sizing
4. Exits: copy wallet's sells OR apply our own stops

Author: Hermes | May 2026
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger('CryptoBot')

# Config
COPY_CONFIG = {
    "max_mirrored_wallets": 3,      # Mirror top 3 wallets
    "min_wallet_score": 60,         # Only mirror A/B tier
    "position_size_usd": 2.50,       # Fixed $2.50 per copy trade
    "max_position_pct": 0.05,        # Max 5% of balance
    "slippage_bps": 300,            # 3% slippage
    "jupiter_quote_url": "https://api.jup.ag/swap/v1/quote",
    "jupiter_swap_url": "https://api.jup.ag/swap/v1/swap",
    "sol_mint": "So11111111111111111111111111111111111111112",
}

# File to persist copy state
COPY_STATE_PATH = os.path.join(os.path.dirname(__file__), "state", "copy_trader_state.json")


class CopyEngine:
    """Monitor top wallets and mirror their trades."""

    def __init__(self, wallet_manager=None, swap_manager=None):
        self.wallet = wallet_manager
        self.swap = swap_manager
        self._session: Optional[aiohttp.ClientSession] = None
        self.tracked_wallets: Dict[str, Dict] = {}  # wallet -> last known txs
        self.active_copies: Dict[str, Dict] = {}    # token -> copy position
        self.state: Dict = {}

    async def initialize(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        # Don't load stale state — leaderboard is source of truth
        self.tracked_wallets = {}
        self.active_copies = {}
        logger.info("Copy engine initialized (fresh start)")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _load_state(self):
        if os.path.exists(COPY_STATE_PATH):
            try:
                with open(COPY_STATE_PATH) as f:
                    self.state = json.load(f)
                self.tracked_wallets = self.state.get("tracked", {})
                self.active_copies = self.state.get("copies", {})
            except Exception as e:
                logger.warning(f"Copy state load failed: {e}")

    def _save_state(self):
        os.makedirs(os.path.dirname(COPY_STATE_PATH), exist_ok=True)
        self.state = {
            "tracked": self.tracked_wallets,
            "copies": self.active_copies,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(COPY_STATE_PATH, "w") as f:
                json.dump(self.state, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Copy state save failed: {e}")

    # ── WALLET MONITORING ──

    async def set_tracked_wallets(self, wallets: List[Dict]):
        """Set wallets to monitor (from wallet scorer/leaderboard)."""
        # Clear old and load fresh
        self.tracked_wallets = {}
        for w in wallets:
            addr = w.get("wallet", "")
            if addr:
                self.tracked_wallets[addr] = {
                    "score": w.get("composite_score", w.get("pnl_30d_sol", 0)),
                    "tier": w.get("tier", "?"),
                    "win_rate": w.get("win_rate", 0),
                    "name": w.get("name", "Unknown"),
                    "last_tx_count": 0,
                    "last_checked": None,
                }
                logger.info(f"🔭 Now tracking: {addr[:20]}... ({w.get('name', '?')} | {w.get('tier', '?')} | {w.get('pnl_30d_sol', 0):.1f} SOL)")

        self._save_state()

    async def scan_for_new_trades(self) -> List[Dict]:
        """Check all tracked wallets for new buy transactions."""
        new_signals = []

        for wallet, meta in list(self.tracked_wallets.items()):
            try:
                txs = await self._fetch_recent_txs(wallet, limit=10)
                if not txs:
                    continue

                # Detect new buys since last check
                for tx in txs:
                    signal = self._parse_buy_signal(tx, wallet)
                    if signal:
                        # Check if we already copied this token
                        token = signal.get("token_mint", "")
                        if token and token not in self.active_copies:
                            new_signals.append(signal)
                            logger.info(
                                f"🚨 COPY SIGNAL: {signal.get('token_symbol', '?')} "
                                f"from {wallet[:20]}... | "
                                f"Size: {signal.get('sol_amount', 0):.4f} SOL"
                            )

                self.tracked_wallets[wallet]["last_checked"] = datetime.now(timezone.utc).isoformat()
                await asyncio.sleep(0.3)  # Rate limit

            except Exception as e:
                logger.debug(f"Scan error for {wallet[:20]}...: {e}")

        return new_signals

    async def _fetch_recent_txs(self, wallet: str, limit: int = 10) -> List[Dict]:
        """Fetch recent transactions from Solscan."""
        url = f"https://public-api.solscan.io/account/transactions?account={wallet}&limit={limit}"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    def _parse_buy_signal(self, tx: Dict, wallet: str) -> Optional[Dict]:
        """Parse a buy signal from a transaction."""
        tx_hash = tx.get("txHash", "")
        block_time = tx.get("blockTime", 0)

        # Only consider transactions from last 5 minutes
        now = datetime.now(timezone.utc).timestamp()
        if now - block_time > 300:
            return None

        # Look for token balance changes
        for token in tx.get("tokenBalanes", []) or []:
            if token.get("owner") == wallet:
                pre = float(token.get("preBalance", 0))
                post = float(token.get("postBalance", 0))
                delta = post - pre

                if delta > 0:  # Token balance increased = BUY
                    # Get SOL spent
                    sol_delta = self._get_sol_delta(tx, wallet)
                    if sol_delta < 0:
                        return {
                            "wallet": wallet,
                            "tx": tx_hash,
                            "timestamp": block_time,
                            "token_mint": token.get("tokenAddress", ""),
                            "token_symbol": token.get("tokenName", "UNKNOWN"),
                            "token_amount": delta,
                            "sol_amount": abs(sol_delta),
                            "direction": "buy",
                        }

        return None

    def _get_sol_delta(self, tx: Dict, wallet: str) -> float:
        """Get SOL balance change for wallet."""
        # Approximate: use lamport balance change
        pre = tx.get("lamportBalance", {}).get("preBalance", 0)
        post = tx.get("lamportBalance", {}).get("postBalance", 0)
        return post - pre

    # ── EXECUTE COPY ──

    async def execute_copy(self, signal: Dict, balance: float) -> Optional[Dict]:
        """Execute a mirrored buy based on signal."""
        token = signal.get("token_mint", "")
        symbol = signal.get("token_symbol", "UNKNOWN")

        if not token:
            return None

        # Check if already copying this token
        if token in self.active_copies:
            logger.info(f"⏭️ Already copying {symbol} — skipping duplicate")
            return None

        # Position sizing: $2.50 or 3% of balance, whichever is smaller
        size_usd = min(COPY_CONFIG["position_size_usd"], balance * COPY_CONFIG["max_position_pct"])
        if size_usd < 1.00:
            logger.warning(f"💰 Balance too low for copy: ${balance:.2f}")
            return None

        # Get SOL price for lamport conversion
        sol_price = await self._get_sol_price()
        if sol_price <= 0:
            logger.warning("SOL price unavailable — skipping copy")
            return None

        lamports = int((size_usd / sol_price) * 1e9)
        if lamports < 100_000:
            logger.warning(f"Copy size too small: {lamports} lamports")
            return None

        # Execute via Jupiter (if swap manager available)
        if self.swap:
            try:
                result = await self.swap.execute_swap(
                    input_mint=COPY_CONFIG["sol_mint"],
                    output_mint=token,
                    amount_in=lamports,
                    slippage_bps=COPY_CONFIG["slippage_bps"],
                )

                if result.success:
                    position = {
                        "token": symbol,
                        "token_mint": token,
                        "entry": result.output_amount > 0 and size_usd / result.output_amount or 0,
                        "invested": size_usd,
                        "quantity": result.output_amount,
                        "copied_from": signal["wallet"],
                        "source_tx": signal["tx"],
                        "opened_at": datetime.now(timezone.utc).isoformat(),
                        "stop": 0,  # Will be set by main bot
                        "highest_price": 0,
                        "mode_at_entry": "COPY",
                    }
                    self.active_copies[token] = position
                    self._save_state()

                    logger.info(
                        f"🐋 COPY EXECUTED: {symbol} | ${size_usd:.2f} | "
                        f"From: {signal['wallet'][:20]}... | Tx: {result.tx_signature[:20]}..."
                    )
                    return position
                else:
                    logger.error(f"Copy buy failed: {result.error}")
            except Exception as e:
                logger.error(f"Copy execution error: {e}")
        else:
            logger.warning("No swap manager — copy would be paper only")
            # Return paper position for tracking
            position = {
                "token": symbol,
                "token_mint": token,
                "entry": 0,
                "invested": size_usd,
                "quantity": 0,
                "copied_from": signal["wallet"],
                "source_tx": signal["tx"],
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "stop": 0,
                "highest_price": 0,
                "mode_at_entry": "COPY",
                "paper": True,
            }
            return position

        return None

    # ── MONITOR COPIED POSITIONS ──

    async def check_exits(self, price_func) -> List[Dict]:
        """Check if copied wallets sold their positions."""
        exits = []

        for token, position in list(self.active_copies.items()):
            wallet = position.get("copied_from", "")
            if not wallet:
                continue

            try:
                txs = await self._fetch_recent_txs(wallet, limit=5)
                for tx in txs:
                    exit_signal = self._parse_sell_signal(tx, wallet, token)
                    if exit_signal:
                        exits.append({
                            "token": token,
                            "symbol": position["token"],
                            "exit_tx": exit_signal["tx"],
                            "reason": "copied_wallet_sold",
                        })
                        break
            except Exception:
                pass

        return exits

    def _parse_sell_signal(self, tx: Dict, wallet: str, token_mint: str) -> Optional[Dict]:
        """Parse a sell signal for a specific token."""
        block_time = tx.get("blockTime", 0)
        now = datetime.now(timezone.utc).timestamp()
        if now - block_time > 300:
            return None

        for token in tx.get("tokenBalanes", []) or []:
            if token.get("owner") == wallet and token.get("tokenAddress") == token_mint:
                pre = float(token.get("preBalance", 0))
                post = float(token.get("postBalance", 0))
                if post < pre:  # Token balance decreased = SELL
                    return {
                        "wallet": wallet,
                        "tx": tx.get("txHash", ""),
                        "token_mint": token_mint,
                    }
        return None

    def remove_copy(self, token: str):
        """Remove a copied position (after sell or stop)."""
        if token in self.active_copies:
            del self.active_copies[token]
            self._save_state()

    # ── UTILS ──

    async def _get_sol_price(self) -> float:
        """Get SOL/USD price."""
        try:
            async with self._session.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd",
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("solana", {}).get("usd", 0))
        except Exception:
            pass
        return 82.0  # Fallback

    def get_status(self) -> Dict:
        """Return current copy trader status."""
        return {
            "tracked_wallets": len(self.tracked_wallets),
            "active_copies": len(self.active_copies),
            "tracked": [
                {"wallet": k[:20] + "...", "score": v.get("score", 0)}
                for k, v in self.tracked_wallets.items()
            ],
            "copies": [
                {"token": v["token"], "invested": v["invested"], "from": v.get("copied_from", "")[:20] + "..."}
                for v in self.active_copies.values()
            ],
        }


# ── BACKWARD COMPATIBLE INIT ──
async def init_copy_trader():
    logger.info("Copy trader v2 initialized")


async def scan_whale_wallets() -> List[Dict]:
    """Legacy compat — returns empty, real signals come from CopyEngine.scan_for_new_trades."""
    return []


async def evaluate_copy_signal(signal: Dict, balance: float) -> Optional[Dict]:
    """Legacy compat."""
    return None


# ── TEST ──
async def test():
    engine = CopyEngine()
    await engine.initialize()
    status = engine.get_status()
    print(json.dumps(status, indent=2))
    await engine.close()


if __name__ == "__main__":
    asyncio.run(test())
