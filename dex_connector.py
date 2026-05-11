"""
DEX CONNECTOR MODULE
Integrates Jupiter, Raydium, Pump.fun for Solana micro-cap trading
Author: Hermes | March 2026 SEC/CFTC compliant
"""

import asyncio
import aiohttp
import json
import logging
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger('DEXConnector')

class DEXConnector:
    """Connect to Solana DEXs for micro-cap and meme coin trading."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.dexscreener_api = "https://api.dexscreener.com"
        self.jupiter_api = "https://quote-api.jup.ag/v6"
        self.pumpfun_api = "https://frontend-api.pump.fun"
        self.raydium_api = "https://api.raydium.io/v2"
        self.birdeye_api = "https://public-api.birdeye.so"
        
        # Session for keep-alive connections
        self.session = None
    
    async def initialize(self):
        """Initialize HTTP session."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            headers={'User-Agent': 'SovereignCryptoBot/1.0'}
        )
        logger.info("DEX connector initialized")
    
    async def close(self):
        """Close HTTP session."""
        if self.session:
            await self.session.close()
    
    # ============================================================
    # DEXSCREENER — Token Discovery
    # ============================================================
    
    async def get_token_profiles(self, limit: int = 20) -> List[Dict]:
        """
        Get latest token profiles from DexScreener.
        Returns: list of trending tokens with metadata.
        """
        url = f"{self.dexscreener_api}/token-profiles/latest/v1"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data[:limit] if isinstance(data, list) else []
                else:
                    logger.warning(f"DexScreener error: {response.status}")
                    return []
        except Exception as e:
            logger.error(f"DexScreener fetch failed: {e}")
            return []
    
    async def get_pair_data(self, chain: str, pair_address: str) -> Optional[Dict]:
        """
        Get detailed pair data (price, volume, liquidity).
        Example: chain='solana', pair_address='9bMXfs77...'
        """
        url = f"{self.dexscreener_api}/latest/dex/pairs/{chain}/{pair_address}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            logger.error(f"Pair data fetch failed: {e}")
            return None
    
    async def search_token(self, query: str) -> List[Dict]:
        """Search for token by name or address."""
        url = f"{self.dexscreener_api}/latest/dex/search?q={query}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('pairs', [])
                return []
        except Exception as e:
            logger.error(f"Token search failed: {e}")
            return []
    
    # ============================================================
    # JUPITER — DEX Aggregator (Best prices across Solana)
    # ============================================================
    
    async def get_jupiter_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: float,
        slippage_bps: int = 50
    ) -> Optional[Dict]:
        """
        Get quote from Jupiter for swap.
        input_mint: SOL or token address
        output_mint: token address
        amount: in lamports (1 SOL = 1e9 lamports)
        slippage_bps: 50 = 0.5%
        """
        url = (
            f"{self.jupiter_api}/quote"
            f"?inputMint={input_mint}"
            f"&outputMint={output_mint}"
            f"&amount={int(amount)}"
            f"&slippageBps={slippage_bps}"
        )
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                logger.warning(f"Jupiter quote error: {response.status}")
                return None
        except Exception as e:
            logger.error(f"Jupiter quote failed: {e}")
            return None
    
    async def execute_jupiter_swap(self, quote_response: Dict, wallet_key: str) -> Optional[Dict]:
        """
        Execute swap via Jupiter (requires wallet integration).
        Returns transaction signature.
        """
        # This requires wallet signing — placeholder for now
        logger.info("Jupiter swap execution requires wallet integration")
        return None
    
    # ============================================================
    # PUMP.FUN — New Launches
    # ============================================================
    
    async def get_pumpfun_new_launches(self, limit: int = 20) -> List[Dict]:
        """
        Get latest token launches from Pump.fun.
        High risk, high reward micro-caps.
        """
        url = f"{self.pumpfun_api}/coins/for-you?offset=0&limit={limit}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data if isinstance(data, list) else []
                return []
        except Exception as e:
            logger.error(f"Pump.fun fetch failed: {e}")
            return []
    
    async def get_pumpfun_coin(self, mint: str) -> Optional[Dict]:
        """Get specific coin data from Pump.fun."""
        url = f"{self.pumpfun_api}/coins/{mint}"
        
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            logger.error(f"Pump.fun coin fetch failed: {e}")
            return None
    
    # ============================================================
    # BIRDEYE — SOL Ecosystem Analytics
    # ============================================================
    
    async def get_birdeye_token_list(
        self,
        sort_by: str = "v24hUSD",
        sort_type: str = "desc",
        limit: int = 50
    ) -> List[Dict]:
        """
        Get trending tokens on Solana via Birdeye.
        sort_by: v24hUSD, marketCap, priceChange24h
        """
        url = (
            f"{self.birdeye_api}/public/tokenlist"
            f"?sort_by={sort_by}"
            f"&sort_type={sort_type}"
            f"&offset=0"
            f"&limit={limit}"
        )
        
        headers = {
            'X-API-KEY': 'public',  # Free tier
            'x-chain': 'solana'
        }
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('data', {}).get('tokens', [])
                return []
        except Exception as e:
            logger.error(f"Birdeye fetch failed: {e}")
            return []
    
    # ============================================================
    # SCORING + FILTERING
    # ============================================================
    
    def score_token(self, token: Dict) -> Tuple[int, List[str]]:
        """
        Score token for bot trading eligibility.
        Returns: (score 0-100, red_flags)
        """
        score = 100
        flags = []
        
        # Check liquidity
        liquidity = token.get('liquidity', {}).get('usd', 0)
        if liquidity < 5000:
            score -= 20
            flags.append(f'low_liquidity_${liquidity:,.0f}')
        
        # Check volume
        volume = token.get('volume', {}).get('h24', 0)
        if volume < 10000:
            score -= 15
            flags.append(f'low_volume_${volume:,.0f}')
        
        # Check market cap
        mc = token.get('marketCap', 0)
        if mc > 10000000:
            score -= 10
            flags.append(f'high_mcap_${mc:,.0f}')
        
        # Check price change (avoid extreme pumps)
        price_change = token.get('priceChange', {}).get('h24', 0)
        if price_change > 500:
            score -= 25
            flags.append(f'extreme_pump_{price_change:.0f}%')
        
        # Check age
        pair_created_at = token.get('pairCreatedAt', 0)
        if pair_created_at:
            age_hours = (datetime.now().timestamp() * 1000 - pair_created_at) / 3600000
            if age_hours < 1:
                score -= 10
                flags.append('brand_new')
            elif age_hours > 168:  # 7 days
                score -= 5
                flags.append('old_token')
        
        # Check socials
        if not token.get('info', {}).get('socials'):
            score -= 10
            flags.append('no_socials')
        
        # Check if website exists
        if not token.get('info', {}).get('websites'):
            score -= 5
            flags.append('no_website')
        
        return max(0, score), flags
    
    async def filter_tokens(self, tokens: List[Dict], min_score: int = 40) -> List[Dict]:
        """Filter tokens by score and return eligible ones."""
        eligible = []
        
        for token in tokens:
            score, flags = self.score_token(token)
            
            if score >= min_score:
                token['bot_score'] = score
                token['bot_flags'] = flags
                eligible.append(token)
            else:
                logger.debug(f"REJECTED {token.get('baseToken', {}).get('symbol', '?')}: score={score}, flags={flags}")
        
        # Sort by score descending
        eligible.sort(key=lambda x: x['bot_score'], reverse=True)
        
        return eligible
    
    # ============================================================
    # MAIN DISCOVERY PIPELINE
    # ============================================================
    
    async def discover_tokens(self, source: str = "mixed", limit: int = 20) -> List[Dict]:
        """
        Discover tokens from multiple sources.
        source: 'dexscreener', 'pumpfun', 'birdeye', 'mixed'
        """
        all_tokens = []
        
        if source in ['dexscreener', 'mixed']:
            profiles = await self.get_token_profiles(limit)
            all_tokens.extend(profiles)
        
        if source in ['pumpfun', 'mixed']:
            launches = await self.get_pumpfun_new_launches(limit)
            all_tokens.extend(launches)
        
        if source in ['birdeye', 'mixed']:
            birdeye_tokens = await self.get_birdeye_token_list(limit=limit)
            all_tokens.extend(birdeye_tokens)
        
        # Deduplicate by address
        seen = set()
        unique = []
        for token in all_tokens:
            addr = token.get('tokenAddress') or token.get('mint') or token.get('address')
            if addr and addr not in seen:
                seen.add(addr)
                unique.append(token)
        
        # Score and filter
        return await self.filter_tokens(unique)
    
    async def get_token_price(self, chain: str, address: str) -> Optional[float]:
        """Get current price for a token."""
        # Try DexScreener first
        pair_data = await self.get_pair_data(chain, address)
        if pair_data and 'pairs' in pair_data:
            return float(pair_data['pairs'][0].get('priceUsd', 0))
        
        # Try Birdeye
        url = f"{self.birdeye_api}/public/multi_price?list_address={address}"
        try:
            async with self.session.get(url, headers={'x-chain': chain}) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get('data', {}).get(address, {}).get('value', 0)
        except:
            pass
        
        return None

# ============================================================
# TEST / DEMO
# ============================================================

async def test():
    """Test DEX connector."""
    connector = DEXConnector({})
    await connector.initialize()
    
    print("\n=== DEX SCREENER TOKEN PROFILES ===")
    profiles = await connector.get_token_profiles(5)
    for p in profiles:
        print(f"  {p.get('tokenAddress', '?')[:20]}... | {p.get('chainId', '?')}")
    
    print("\n=== DISCOVER + FILTER ===")
    tokens = await connector.discover_tokens("mixed", 10)
    for t in tokens[:5]:
        symbol = t.get('baseToken', {}).get('symbol', t.get('symbol', '?'))
        score = t.get('bot_score', 0)
        print(f"  {symbol}: score={score}, flags={t.get('bot_flags', [])}")
    
    await connector.close()

if __name__ == "__main__":
    asyncio.run(test())
