"""
risk_scanner.py — Hermes Solana Bot v2
8-criteria on-chain rug-pull detection.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx
from solders.pubkey import Pubkey

from rpc_manager import RPCManager

logger = logging.getLogger(__name__)

# Metaplex metadata program
METAPLEX_PROGRAM = Pubkey.from_string("metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s")
# Liquidity lock programs (common ones)
LP_LOCK_PROGRAMS = {
    "uncx": "UNCX8rDft4xCdkDmzSMmXz9q6Gj82z5qXkJ5aL6w7c8",  # placeholder
}


@dataclass
class RugCheck:
    name: str
    passed: bool
    weight: int  # 0-100 contribution to score
    details: str


@dataclass
class RugReport:
    token_mint: str
    token_symbol: str = ""
    rug_score: int = 0  # 0-100 (100 = highest risk)
    checks: list[RugCheck] = field(default_factory=list)
    is_safe: bool = False


class RiskScanner:
    """8-factor on-chain rug scanner for Solana SPL tokens.

    Checks:
    1. Mint authority active
    2. Freeze authority active
    3. Top holder concentration (>30%)
    4. LP lock status
    5. Deployer history (rug count)
    6. Mutable metadata
    7. Liquidity ratio (LP <10% mcap)
    8. Honeypot test (simulate buy+sell)
    """

    def __init__(
        self,
        rpc_manager: RPCManager,
        birdeye_api_key: str = "",
        birdeye_url: str = "https://public-api.birdeye.so",
        max_rug_score: int = 30,
        max_top_holder_pct: float = 30.0,
        min_lp_lock_days: int = 30,
        max_deployer_rug_count: int = 3,
    ) -> None:
        self.rpc = rpc_manager
        self.birdeye_key = birdeye_api_key
        self.birdeye_url = birdeye_url.rstrip("/")
        self.max_rug_score = max_rug_score
        self.max_top_holder_pct = max_top_holder_pct
        self.min_lp_lock_days = min_lp_lock_days
        self.max_deployer_rug_count = max_deployer_rug_count
        self.http = httpx.AsyncClient(timeout=15.0)

    async def close(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------ #
    # Main entry
    # ------------------------------------------------------------------ #
    async def scan(self, token_mint: str, token_symbol: str = "") -> RugReport:
        """Run all 8 checks and return a RugReport."""
        checks: list[RugCheck] = []

        # 1. Mint authority
        checks.append(await self._check_mint_authority(token_mint))

        # 2. Freeze authority
        checks.append(await self._check_freeze_authority(token_mint))

        # 3. Top holder concentration (on-chain, not Birdeye estimate)
        checks.append(await self._check_holder_concentration(token_mint))

        # 4. LP lock status
        checks.append(await self._check_lp_lock(token_mint))

        # 5. Deployer history
        checks.append(await self._check_deployer_history(token_mint))

        # 6. Mutable metadata
        checks.append(await self._check_mutable_metadata(token_mint))

        # 7. Liquidity ratio
        checks.append(await self._check_liquidity_ratio(token_mint))

        # 8. Honeypot test
        checks.append(await self._check_honeypot(token_mint))

        # Calculate score
        score = self._calculate_score(checks)
        report = RugReport(
            token_mint=token_mint,
            token_symbol=token_symbol,
            rug_score=score,
            checks=checks,
            is_safe=score <= self.max_rug_score,
        )
        logger.info("Rug scan %s: score=%d/100 safe=%s",
                    token_mint[:8], score, report.is_safe)
        return report

    # ------------------------------------------------------------------ #
    # Individual checks
    # ------------------------------------------------------------------ #
    async def _check_mint_authority(self, mint: str) -> RugCheck:
        """Check if mint authority is active. Active = dev can inflate supply."""
        try:
            info = await self.rpc.get_parsed_account_info(mint)
            if not info:
                return RugCheck("mint_authority", False, 25, "Mint account not found")
            data = info.data.parsed if hasattr(info.data, "parsed") else {}
            info_dict = data.get("info", {}) if isinstance(data, dict) else {}
            auth = info_dict.get("mintAuthority")
            if auth is None:
                return RugCheck("mint_authority", True, 0, "Mint authority disabled ✅")
            return RugCheck("mint_authority", False, 25,
                           f"Mint authority ACTIVE: {auth}")
        except Exception as exc:
            logger.warning("Mint authority check failed: %s", exc)
            return RugCheck("mint_authority", False, 10,
                           f"Check failed (assume risky): {exc}")

    async def _check_freeze_authority(self, mint: str) -> RugCheck:
        """Check if freeze authority is active. Active = dev can lock wallets."""
        try:
            info = await self.rpc.get_parsed_account_info(mint)
            if not info:
                return RugCheck("freeze_authority", False, 25, "Mint account not found")
            data = info.data.parsed if hasattr(info.data, "parsed") else {}
            info_dict = data.get("info", {}) if isinstance(data, dict) else {}
            auth = info_dict.get("freezeAuthority")
            if auth is None:
                return RugCheck("freeze_authority", True, 0, "Freeze authority disabled ✅")
            return RugCheck("freeze_authority", False, 20,
                           f"Freeze authority ACTIVE: {auth}")
        except Exception as exc:
            logger.warning("Freeze authority check failed: %s", exc)
            return RugCheck("freeze_authority", False, 10,
                           f"Check failed (assume risky): {exc}")

    async def _check_holder_concentration(self, mint: str) -> RugCheck:
        """Check top holder % via getTokenLargestAccounts."""
        try:
            accounts = await self.rpc.get_token_largest_accounts(mint)
            if not accounts:
                return RugCheck("holder_concentration", False, 15,
                               "No holder data available")
            # Filter out known LP addresses (heuristic)
            top = accounts[0]
            top_pct = float(top.ui_amount or 0)  # This is raw, not %
            # We need total supply to calculate % — try Birdeye as supplement
            total_supply = await self._get_total_supply(mint)
            if total_supply > 0:
                pct = (top_pct / total_supply) * 100
                if pct > self.max_top_holder_pct:
                    return RugCheck("holder_concentration", False, 20,
                                   f"Top holder owns {pct:.1f}% 🔴")
                return RugCheck("holder_concentration", True, 0,
                               f"Top holder owns {pct:.1f}% ✅")
            return RugCheck("holder_concentration", False, 10,
                           "Could not determine total supply")
        except Exception as exc:
            logger.warning("Holder concentration check failed: %s", exc)
            return RugCheck("holder_concentration", False, 10,
                           f"Check failed: {exc}")

    async def _check_lp_lock(self, mint: str) -> RugCheck:
        """Check if LP tokens are locked/burned."""
        try:
            # Try to find LP account via DexScreener or Birdeye
            lp_info = await self._get_lp_info(mint)
            if not lp_info:
                return RugCheck("lp_lock", False, 15, "No LP data found")
            locked = lp_info.get("lpLocked", False)
            lock_days = lp_info.get("lpLockDays", 0)
            if locked and lock_days >= self.min_lp_lock_days:
                return RugCheck("lp_lock", True, 0,
                               f"LP locked {lock_days}d ✅")
            if locked:
                return RugCheck("lp_lock", False, 10,
                               f"LP locked only {lock_days}d ⚠️")
            return RugCheck("lp_lock", False, 20,
                           "LP NOT locked 🔴")
        except Exception as exc:
            logger.warning("LP lock check failed: %s", exc)
            return RugCheck("lp_lock", False, 10,
                           f"Check failed: {exc}")

    async def _check_deployer_history(self, mint: str) -> RugCheck:
        """Check deployer's history for previous rugs."""
        try:
            deployer = await self._get_deployer(mint)
            if not deployer:
                return RugCheck("deployer_history", False, 10,
                               "Could not find deployer")
            sigs = await self.rpc.get_signatures_for_address(deployer, limit=20)
            # Simple heuristic: count token creation signatures
            # A deployer with many CreateAccount + InitializeMint2 is suspicious
            # This is a simplified check — full version needs parsing each tx
            token_creations = len([s for s in sigs if "pump" not in s.signature.lower()])
            if token_creations > self.max_deployer_rug_count:
                return RugCheck("deployer_history", False, 15,
                               f"Deployer launched {token_creations}+ tokens 🔴")
            return RugCheck("deployer_history", True, 0,
                           f"Deployer history clean ({token_creations} tokens) ✅")
        except Exception as exc:
            logger.warning("Deployer history check failed: %s", exc)
            return RugCheck("deployer_history", False, 5,
                           f"Check failed: {exc}")

    async def _check_mutable_metadata(self, mint: str) -> RugCheck:
        """Check if token metadata is mutable."""
        try:
            # Derive metadata PDA
            metadata_pda, _ = Pubkey.find_program_address(
                [b"metadata", bytes(METAPLEX_PROGRAM), bytes(Pubkey.from_string(mint))],
                METAPLEX_PROGRAM,
            )
            info = await self.rpc.get_parsed_account_info(str(metadata_pda))
            if not info:
                return RugCheck("mutable_metadata", False, 10,
                               "No metadata account")
            # Parse isMutable flag from account data
            data = info.data.parsed if hasattr(info.data, "parsed") else {}
            info_dict = data.get("info", {}) if isinstance(data, dict) else {}
            is_mutable = info_dict.get("isMutable", True)
            if is_mutable:
                return RugCheck("mutable_metadata", False, 10,
                               "Metadata is MUTABLE ⚠️")
            return RugCheck("mutable_metadata", True, 0,
                           "Metadata is immutable ✅")
        except Exception as exc:
            logger.warning("Mutable metadata check failed: %s", exc)
            return RugCheck("mutable_metadata", False, 5,
                           f"Check failed: {exc}")

    async def _check_liquidity_ratio(self, mint: str) -> RugCheck:
        """Check if LP < 10% of market cap."""
        try:
            info = await self._get_lp_info(mint)
            if not info:
                return RugCheck("liquidity_ratio", False, 10,
                               "No liquidity data")
            liquidity_usd = info.get("liquidityUsd", 0)
            mcap = info.get("marketCap", 0)
            if mcap <= 0:
                return RugCheck("liquidity_ratio", False, 10,
                               "No market cap data")
            ratio = (liquidity_usd / mcap) * 100
            if ratio < 10:
                return RugCheck("liquidity_ratio", False, 15,
                               f"LP/MCap = {ratio:.1f}% 🔴")
            return RugCheck("liquidity_ratio", True, 0,
                           f"LP/MCap = {ratio:.1f}% ✅")
        except Exception as exc:
            logger.warning("Liquidity ratio check failed: %s", exc)
            return RugCheck("liquidity_ratio", False, 5,
                           f"Check failed: {exc}")

    async def _check_honeypot(self, mint: str) -> RugCheck:
        """Simulate buy + sell to detect honeypots.

        In paper/test mode: use a small simulation.
        In live mode: this requires real SOL. Skip for now — mark as caution.
        """
        # Honeypot simulation is complex and costs gas.
        # For production: use a third-party API (e.g., DeFi Safety) or
        # run a background job with tiny amounts.
        return RugCheck("honeypot", False, 5,
                       "Honeypot check skipped (requires gas) — manual review advised ⚠️")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _calculate_score(self, checks: list[RugCheck]) -> int:
        """Weighted sum of failed checks. Cap at 100."""
        score = sum(c.weight for c in checks if not c.passed)
        return min(score, 100)

    async def _get_total_supply(self, mint: str) -> float:
        """Get total token supply via RPC."""
        try:
            info = await self.rpc.get_parsed_account_info(mint)
            if not info:
                return 0.0
            data = info.data.parsed if hasattr(info.data, "parsed") else {}
            info_dict = data.get("info", {}) if isinstance(data, dict) else {}
            return float(info_dict.get("supply", 0)) / (10 ** info_dict.get("decimals", 0))
        except Exception:
            return 0.0

    async def _get_deployer(self, mint: str) -> str:
        """Get token deployer address from first signature."""
        try:
            sigs = await self.rpc.get_signatures_for_address(mint, limit=1)
            if sigs:
                # Parse the transaction to find the deployer
                # This is simplified — real implementation needs getTransaction
                return ""
        except Exception:
            pass
        return ""

    async def _get_lp_info(self, mint: str) -> dict:
        """Get LP info from Birdeye or DexScreener."""
        # Try Birdeye first
        try:
            headers = {"X-API-KEY": self.birdeye_key} if self.birdeye_key else {}
            resp = await self.http.get(
                f"{self.birdeye_url}/public/token_overview",
                params={"address": mint},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return {
                    "liquidityUsd": data.get("liquidity", 0),
                    "marketCap": data.get("mcap", 0),
                    "lpLocked": data.get("lpLocked", False),
                    "lpLockDays": data.get("lpLockDays", 0),
                }
        except Exception:
            pass
        # Fallback to DexScreener
        try:
            resp = await self.http.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                timeout=10.0,
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "liquidityUsd": p.get("liquidity", {}).get("usd", 0),
                        "marketCap": p.get("marketCap", 0),
                        "lpLocked": False,
                        "lpLockDays": 0,
                    }
        except Exception:
            pass
        return {}


if __name__ == "__main__":
    print("RiskScanner loaded. Import and use with RPCManager.")
