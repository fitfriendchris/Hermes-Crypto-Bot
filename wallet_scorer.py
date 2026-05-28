#!/usr/bin/env python3
"""
WALLET SCORER — On-chain performance analysis for Solana wallets
Fetches trade history, calculates PnL, Sharpe, win rate, max drawdown.
Uses Solscan public API (no key needed for basic calls).

Author: Hermes | May 2026
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger('CryptoBot')

# Solscan public API (no auth, rate-limited to ~5 req/sec)
SOLSCAN_API = "https://public-api.solscan.io"

# Helius RPC (free tier, 1M requests/month)
HELIUS_API = "https://mainnet.helius-rpc.com/?api-key=1b648949-7c0e-4167-aaf2-3f7ad6d90e15"

# Jupiter price API (for USD valuation)
JUPITER_PRICE_URL = "https://api.jup.ag/price/v2"


class WalletScorer:
    """Score a Solana wallet's trading performance."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._price_cache: Dict[str, Tuple[float, datetime]] = {}

    async def initialize(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"Accept": "application/json"}
        )
        logger.info("Wallet scorer initialized")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── CORE: FETCH TRANSACTIONS ──

    async def fetch_transactions(self, wallet: str, days: int = 90, limit: int = 200) -> List[Dict]:
        """Fetch token swap transactions for a wallet."""
        txs = []
        offset = 0
        batch = 50

        while len(txs) < limit and offset < limit:
            url = f"{SOLSCAN_API}/account/transactions?account={wallet}&limit={batch}&offset={offset}"
            try:
                async with self._session.get(url, timeout=10) as resp:
                    if resp.status != 200:
                        logger.debug(f"Solscan {resp.status} for {wallet[:20]}...")
                        break
                    data = await resp.json()
                    batch_txs = data if isinstance(data, list) else []
                    if not batch_txs:
                        break

                    for tx in batch_txs:
                        # Filter for Jupiter/Raydium swap transactions
                        if self._is_swap_tx(tx):
                            txs.append(tx)

                    offset += len(batch_txs)
                    await asyncio.sleep(0.3)  # Rate limit respect
            except Exception as e:
                logger.warning(f"Solscan fetch error: {e}")
                break

        return txs[:limit]

    def _is_swap_tx(self, tx: Dict) -> bool:
        """Check if transaction is a DEX swap (Jupiter/Raydium/PumpSwap)."""
        # Check program IDs in instructions
        progs = set()
        for ix in tx.get("parsedInstruction", []) or []:
            prog = ix.get("programId", "")
            if prog:
                progs.add(prog)

        # Known DEX programs
        dex_programs = {
            "JUP6LkbZbjS1jKKwapdHNyMrzcTRT5VqkmzV3GowrN5",  # Jupiter v6
            "JUP4Fb2cqiRUcaFhDHkTVvH2jVGLu4K1J6bF9Q4XJ3",   # Jupiter v4
            "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUtqMp2",  # Raydium AMM
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.fun
            "pAMMBayHd5GJK9DHN7h8p5x3qGmwCcrkN9ZdY5K3L6",   # PumpSwap
        }
        return bool(progs & dex_programs)

    # ── CORE: PARSE TRADE FROM TX ──

    def _extract_trades(self, txs: List[Dict], wallet: str) -> List[Dict]:
        """Extract buy/sell entries from swap transactions."""
        trades = []
        for tx in txs:
            tx_hash = tx.get("txHash", "")
            block_time = tx.get("blockTime", 0)
            ts = datetime.fromtimestamp(block_time, tz=timezone.utc) if block_time else None

            # Look for token transfers
            token_changes = self._extract_token_changes(tx, wallet)
            sol_change = self._extract_sol_change(tx, wallet)

            if not token_changes or sol_change == 0:
                continue

            # Determine direction: SOL out = buy, SOL in = sell
            for change in token_changes:
                trade = {
                    "tx": tx_hash,
                    "timestamp": ts.isoformat() if ts else None,
                    "token_mint": change["mint"],
                    "token_symbol": change.get("symbol", "UNKNOWN"),
                    "token_amount": abs(change["amount"]),
                    "sol_amount": abs(sol_change),
                    "direction": "buy" if sol_change < 0 else "sell",
                }
                trades.append(trade)

        return trades

    def _extract_token_changes(self, tx: Dict, wallet: str) -> List[Dict]:
        """Extract SPL token balance changes for the wallet."""
        changes = []
        for token in tx.get("tokenBalanes", []) or []:
            if token.get("owner") == wallet:
                pre = float(token.get("preBalance", 0))
                post = float(token.get("postBalance", 0))
                delta = post - pre
                if delta != 0:
                    changes.append({
                        "mint": token.get("tokenAddress", ""),
                        "symbol": token.get("tokenName", "UNKNOWN"),
                        "amount": delta,
                    })
        return changes

    def _extract_sol_change(self, tx: Dict, wallet: str) -> float:
        """Extract SOL balance change for the wallet (in lamports)."""
        for acc in tx.get("parsedInstruction", []) or []:
            # Look for system program transfers
            if acc.get("programId") == "11111111111111111111111111111111":
                # This is approximate — real parsing needs more work
                pass

        # Fallback: use lamport balance change from meta
        pre = tx.get("lamportBalance", {}).get("preBalance", 0)
        post = tx.get("lamportBalance", {}).get("postBalance", 0)
        return post - pre

    # ── CORE: CALCULATE METRICS ──

    def calculate_metrics(self, trades: List[Dict], wallet: str) -> Dict:
        """Calculate PnL, Sharpe, win rate, max drawdown from trades."""
        if not trades:
            return {"error": "No trades found"}

        # Group by token to match buys with sells
        token_trades: Dict[str, List[Dict]] = {}
        for t in trades:
            mint = t["token_mint"]
            if mint not in token_trades:
                token_trades[mint] = []
            token_trades[mint].append(t)

        # Calculate per-token PnL
        pnl_list = []
        winning_tokens = 0
        losing_tokens = 0

        for mint, tlist in token_trades.items():
            buys = [t for t in tlist if t["direction"] == "buy"]
            sells = [t for t in tlist if t["direction"] == "sell"]

            if not buys or not sells:
                continue  # Unclosed position — don't count

            total_buy_sol = sum(t["sol_amount"] for t in buys)
            total_sell_sol = sum(t["sol_amount"] for t in sells)
            token_pnl = total_sell_sol - total_buy_sol
            pnl_list.append(token_pnl)

            if token_pnl > 0:
                winning_tokens += 1
            else:
                losing_tokens += 1

        if not pnl_list:
            return {"error": "No completed round-trips found"}

        # Metrics
        total_pnl_lamports = sum(pnl_list)
        total_pnl_sol = total_pnl_lamports / 1e9

        # Win rate
        total_round_trips = winning_tokens + losing_tokens
        win_rate = winning_tokens / total_round_trips if total_round_trips > 0 else 0

        # Sharpe-ish: mean return / std dev (annualized, crude)
        import statistics
        if len(pnl_list) > 1:
            mean_pnl = statistics.mean(pnl_list)
            std_pnl = statistics.stdev(pnl_list)
            sharpe = (mean_pnl / std_pnl) * (len(pnl_list) ** 0.5) if std_pnl > 0 else 0
        else:
            sharpe = 0

        # Max drawdown (running PnL)
        cumulative = 0
        peak = 0
        max_dd = 0
        for pnl in pnl_list:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd

        return {
            "wallet": wallet,
            "total_trades": len(trades),
            "round_trips": total_round_trips,
            "winning_round_trips": winning_tokens,
            "losing_round_trips": losing_tokens,
            "win_rate": round(win_rate, 3),
            "total_pnl_sol": round(total_pnl_sol, 4),
            "total_pnl_lamports": total_pnl_lamports,
            "avg_pnl_per_trade_lamports": round(total_pnl_lamports / len(pnl_list), 0) if pnl_list else 0,
            "sharpe": round(sharpe, 2),
            "max_drawdown_lamports": max_dd,
            "max_drawdown_sol": round(max_dd / 1e9, 4),
            "trades_per_day": round(len(trades) / 90, 1),  # Assuming 90d lookback
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    # ── PUBLIC: SCORE WALLET ──

    async def score_wallet(self, wallet: str, days: int = 60) -> Dict:
        """Full pipeline: fetch → parse → calculate → return scorecard."""
        logger.info(f"🔍 Scoring wallet: {wallet[:20]}...")

        txs = await self.fetch_transactions(wallet, days=days)
        if not txs:
            return {"wallet": wallet, "error": "No transactions found", "score": 0}

        trades = self._extract_trades(txs, wallet)
        metrics = self.calculate_metrics(trades, wallet)

        # Composite score: 0-100
        score = 0
        if "error" not in metrics:
            # Win rate weight: 40%
            score += metrics["win_rate"] * 40
            # PnL weight: 30% (normalized by trade count)
            pnl_score = min(max(metrics["total_pnl_sol"] / max(metrics["round_trips"], 1), -5), 5) / 5
            score += pnl_score * 30
            # Sharpe weight: 20%
            sharpe_score = min(max(metrics["sharpe"], -2), 2) / 2
            score += sharpe_score * 20
            # Drawdown penalty: 10%
            dd_penalty = min(metrics["max_drawdown_sol"] / max(abs(metrics["total_pnl_sol"]), 1), 1)
            score -= dd_penalty * 10

        score = max(0, min(100, score))

        metrics["composite_score"] = round(score, 1)
        metrics["tier"] = self._tier(score)
        metrics["recommendation"] = self._recommendation(score, metrics)

        logger.info(
            f"📊 Wallet {wallet[:20]}... | Score: {score:.1f} | "
            f"WinRate: {metrics.get('win_rate', 0):.0%} | "
            f"PnL: {metrics.get('total_pnl_sol', 0):+.4f} SOL | "
            f"Trades: {metrics.get('round_trips', 0)} | "
            f"Tier: {metrics.get('tier', '?')}"
        )

        return metrics

    def _tier(self, score: float) -> str:
        if score >= 80: return "A+"
        if score >= 70: return "A"
        if score >= 60: return "B"
        if score >= 50: return "C"
        if score >= 40: return "D"
        return "F"

    def _recommendation(self, score: float, metrics: Dict) -> str:
        if score >= 70 and metrics.get("round_trips", 0) >= 10:
            return "MIRROR"
        if score >= 60 and metrics.get("round_trips", 0) >= 5:
            return "WATCH"
        return "SKIP"


# ── BACKWARD COMPATIBLE INIT ──
async def init_wallet_scorer():
    logger.info("Wallet scorer module ready")


# ── TEST ──
async def test():
    scorer = WalletScorer()
    await scorer.initialize()

    # Test with a known profitable wallet (Stratium's public example)
    test_wallet = "7n6..."  # Replace with real wallet
    result = await scorer.score_wallet(test_wallet)
    print(json.dumps(result, indent=2, default=str))

    await scorer.close()


if __name__ == "__main__":
    asyncio.run(test())
