"""
Narrative Scanner — Cultural Events + Trending Topics → Token Matcher
Author: Hermes | May 2026

STRATEGY:
- Monitor cultural events (sports, elections, tech releases, viral moments)
- Detect when "Simpsons predicted this" goes viral
- Find tokens that match the narrative BEFORE they pump
- Buy early, sell when mainstream catches on

DATA SOURCES:
- DexScreener trending (real-time)
- DexScreener new pairs (< 1 hour old)
- CoinGecko trending
- Manual event calendar (World Cup, elections, etc.)

NARRATIVE ENGINE:
- Event matching: token name/symbol matches event keyword
- Momentum scoring: volume spike + price action
- Age bonus: newer tokens pump harder
- Liquidity filter: must be able to exit
"""

import asyncio
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

try:
    import hermes_brain
    _BRAIN_OK = True
except ImportError:
    _BRAIN_OK = False
from typing import Dict, List, Optional, Set, Tuple

import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('NarrativeScanner')

# ── EVENT CALENDAR (Upcoming cultural moments) ──
# Format: (date, keyword, narrative, confidence)
# These are KNOWN events that will generate hype
EVENT_CALENDAR = [
    # 2026 World Cup (June 11 - July 19, 2026)
    ('2026-06-11', 'worldcup', 'World Cup 2026 - biggest sporting event', 0.9),
    ('2026-06-11', 'world cup', 'World Cup 2026', 0.9),
    ('2026-06-11', 'fifa', 'FIFA World Cup', 0.85),
    ('2026-06-11', 'soccer', 'Soccer/football hype', 0.7),
    ('2026-06-11', 'portugal', 'Portugal team hype (Simpsons predicted)', 0.6),
    ('2026-06-11', 'mexico', 'Mexico host nation hype', 0.5),
    
    # US Midterm Elections (Nov 2026)
    ('2026-11-03', 'election', 'US Midterm Elections', 0.9),
    ('2026-11-03', 'trump', 'Trump-related political hype', 0.7),
    ('2026-11-03', 'biden', 'Biden-related political hype', 0.5),
    ('2026-11-03', 'vote', 'Voting/election narrative', 0.6),
    
    # Summer 2026 (general)
    ('2026-06-01', 'summer', 'Summer 2026 hype', 0.4),
    ('2026-06-01', 'hot', 'Summer heat/drama', 0.3),
    
    # Tech/AI (ongoing)
    ('2026-05-15', 'ai', 'AI breakthrough hype', 0.6),
    ('2026-05-15', 'gpt', 'OpenAI/GPT release hype', 0.5),
    ('2026-05-15', 'nvidia', 'NVIDIA/tech earnings', 0.5),
    
    # Bitcoin Halving aftermath (2024 halving → 2025-26 effects)
    ('2026-05-15', 'halving', 'Bitcoin halving narrative', 0.4),
    ('2026-05-15', 'bitcoin', 'BTC price action', 0.5),
    
    # Meme culture (ongoing)
    ('2026-05-15', 'simpsons', 'Simpsons prediction goes viral', 0.7),
    ('2026-05-15', 'prediction', 'Prediction narrative', 0.5),
    ('2026-05-15', 'cartoon', 'Cartoon/animation trend', 0.3),
]

# ── NARRATIVE PATTERNS (token name matching) ──
NARRATIVE_KEYWORDS = {
    'worldcup': ['worldcup', 'world cup', 'fifa', 'soccer', 'football', 'qatar', 'portugal', 'mexico', 'messi', 'ronaldo'],
    'election': ['trump', 'biden', 'election', 'vote', 'president', 'political', 'democrat', 'republican', 'maga'],
    'ai': ['ai', 'gpt', 'openai', 'claude', 'llm', 'neural', 'brain', 'robot', 'android'],
    'meme': ['doge', 'pepe', 'wojak', 'gigachad', 'chad', 'sigma', 'based', 'mog', 'troll', 'bonk', 'shib'],
    'simpsons': ['simpsons', 'simpson', 'homer', 'bart', 'marge', 'lisa', 'maggie', 'frink', 'crundle', 'doh', 'prediction'],
    'money': ['rich', 'wealth', 'lambo', 'moon', '1000x', 'millionaire', 'billionaire', 'make it', 'wagmi'],
    'sports': ['nba', 'nfl', 'superbowl', 'olympics', 'champion', 'mvp', 'sports', 'athlete'],
}

class EventCalendar:
    """Tracks upcoming cultural events and their narrative potential."""
    
    def __init__(self):
        self.events = EVENT_CALENDAR
        
    def get_active_events(self, days_ahead: int = 30) -> List[Tuple[str, str, str, float]]:
        """Get events happening within days_ahead."""
        now = datetime.now()
        active = []
        
        for date_str, keyword, narrative, confidence in self.events:
            event_date = datetime.strptime(date_str, '%Y-%m-%d')
            days_until = (event_date - now).days
            
            if -7 <= days_until <= days_ahead:  # Event happening now or soon
                # Boost confidence as event approaches
                proximity_boost = 1.0
                if days_until <= 7:
                    proximity_boost = 1.5
                elif days_until <= 14:
                    proximity_boost = 1.2
                
                active.append((
                    keyword,
                    narrative,
                    min(1.0, confidence * proximity_boost),
                    days_until
                ))
        
        return sorted(active, key=lambda x: x[2], reverse=True)
    
    def get_narrative_score(self, token_symbol: str, token_name: str = '') -> Tuple[float, str]:
        """Score how well a token matches active narratives."""
        text = f"{token_symbol} {token_name}".lower()
        active = self.get_active_events()
        
        best_score = 0
        best_narrative = 'none'
        
        for keyword, narrative, confidence, days_until in active:
            if keyword in text:
                # Score based on confidence and proximity
                score = confidence * 100
                if days_until <= 0:  # Event happening NOW
                    score *= 1.5  # Boost for current events
                
                if score > best_score:
                    best_score = score
                    best_narrative = narrative
        
        # Also check general narrative keywords
        for category, keywords in NARRATIVE_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in text)
            if matches > 0:
                score = matches * 15  # 15 points per keyword match
                if score > best_score:
                    best_score = score
                    best_narrative = category
        
        return best_score, best_narrative


class DexScreenerAPI:
    """DexScreener API wrapper."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_trending(self, limit: int = 100) -> List[Dict]:
        """Get trending tokens on Solana."""
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = []
                    for t in data:
                        if t.get('chainId') == 'solana':
                            tokens.append({
                                'symbol': t.get('symbol', '?'),
                                'name': t.get('name', ''),
                                'address': t.get('tokenAddress', ''),
                                'icon': t.get('icon', ''),
                                'url': t.get('url', ''),
                                'source': 'dexscreener_trending',
                            })
                    return tokens[:limit]
        except Exception as e:
            logger.warning(f"DexScreener trending error: {e}")
        return []
    
    async def get_new_pairs(self, limit: int = 50) -> List[Dict]:
        """Get new token pairs."""
        # DexScreener doesn't have a direct "new pairs" API
        # We use the latest profiles as a proxy
        return await self.get_trending(limit)
    
    async def get_token_data(self, token_address: str) -> Optional[Dict]:
        """Get detailed token data from DexScreener."""
        url = f"https://api.dexscreener.com/tokens/v1/solana/{token_address}"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and len(data) > 0:
                        pair = data[0]
                        return {
                            'price': float(pair.get('priceUsd', 0)),
                            'liquidity': pair.get('liquidity', {}).get('usd', 0),
                            'volume_24h': pair.get('volume', {}).get('h24', 0),
                            'change_1h': pair.get('priceChange', {}).get('h1', 0),
                            'change_24h': pair.get('priceChange', {}).get('h24', 0),
                            'txns_24h': pair.get('txns', {}).get('h24', {}).get('buys', 0) + pair.get('txns', {}).get('h24', {}).get('sells', 0),
                            'holders': pair.get('holders', 0),
                        }
        except Exception as e:
            logger.warning(f"Token data error: {e}")
        return None


class GeckoTerminalAPI:
    """GeckoTerminal API for trending pools."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_trending_pools(self, limit: int = 100) -> List[Dict]:
        """Get trending pools on Solana."""
        url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1"
        headers = {"Accept": "application/json"}
        
        try:
            async with self._session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pools = []
                    for p in data.get('data', []):
                        attr = p.get('attributes', {})
                        rel = p.get('relationships', {})
                        
                        # Extract token info
                        base_token = rel.get('base_token', {}).get('data', {})
                        token_id = base_token.get('id', '')
                        # ID format: solana_<mint_address>
                        mint = token_id.split('_')[-1] if '_' in token_id else token_id
                        
                        pools.append({
                            'symbol': attr.get('symbol', '?'),
                            'name': attr.get('name', ''),
                            'address': mint,
                            'price_usd': float(attr.get('base_token_price_usd', 0)),
                            'liquidity_usd': float(attr.get('reserve_in_usd', 0)),
                            'volume_24h': float(attr.get('volume_usd', {}).get('h24', 0)),
                            'change_1h': float(attr.get('price_change_percentage', {}).get('h1', 0)),
                            'change_24h': float(attr.get('price_change_percentage', {}).get('h24', 0)),
                            'source': 'geckoterminal',
                        })
                    return pools[:limit]
        except Exception as e:
            logger.warning(f"GeckoTerminal error: {e}")
        return []


class NarrativeTokenScorer:
    """Score tokens based on narrative fit + market data."""
    
    def __init__(self):
        self.calendar = EventCalendar()
    
    def score_token(self, token: Dict) -> Dict:
        """Score a token. Returns enriched token dict."""
        sym = token.get('symbol', '')
        name = token.get('name', '')
        
        # Narrative score
        narrative_score, narrative = self.calendar.get_narrative_score(sym, name)
        
        # Market data
        price = token.get('price_usd', 0)
        liq = token.get('liquidity_usd', 0)
        vol = token.get('volume_24h', 0)
        ch1h = token.get('change_1h', 0)
        ch24h = token.get('change_24h', 0)
        
        # Market score
        market_score = 0
        market_score += min(20, (liq / 100_000) * 2)
        market_score += min(20, (vol / 300_000) * 2)
        market_score += min(15, abs(ch1h) * 2)
        market_score += min(15, ch24h * 0.2)
        
        # Liquidity quality
        if liq > 500_000:
            market_score += 10
        elif liq > 100_000:
            market_score += 5
        
        # Combined score
        total_score = narrative_score * 0.6 + market_score * 0.4
        
        return {
            **token,
            'narrative': narrative,
            'narrative_score': narrative_score,
            'market_score': market_score,
            'total_score': total_score,
            'recommendation': self._get_recommendation(total_score, liq, vol),
        }
    
    def _get_recommendation(self, score: float, liq: float, vol: float) -> str:
        if score >= 70 and liq >= 50_000 and vol >= 100_000:
            return 'STRONG_BUY'
        elif score >= 50 and liq >= 30_000:
            return 'BUY'
        elif score >= 30:
            return 'WATCH'
        else:
            return 'SKIP'


class NarrativeBot:
    """Main narrative trading bot."""
    
    def __init__(self):
        self.dex = DexScreenerAPI()
        self.gecko = GeckoTerminalAPI()
        self.scorer = NarrativeTokenScorer()
        self.seen: Set[str] = set()
        self.positions: Dict[str, Dict] = {}
        
    async def initialize(self):
        await self.dex.initialize()
        await self.gecko.initialize()
    
    async def close(self):
        await self.dex.close()
        await self.gecko.close()
    
    async def scan(self) -> List[Dict]:
        """Full narrative scan."""
        logger.info("🔍 Scanning for narrative opportunities...")
        
        # Get active events
        active_events = self.scorer.calendar.get_active_events()
        logger.info(f"Active narratives: {len(active_events)}")
        for kw, narrative, conf, days in active_events[:5]:
            logger.info(f"  {narrative} (confidence: {conf:.1%}, days: {days})")
        
        # Get tokens from multiple sources
        tokens = []
        
        # Source 1: GeckoTerminal trending
        gecko_tokens = await self.gecko.get_trending_pools(limit=100)
        tokens.extend(gecko_tokens)
        logger.info(f"GeckoTerminal: {len(gecko_tokens)} tokens")
        
        # Remove duplicates
        seen_symbols = set()
        unique_tokens = []
        for t in tokens:
            sym = t.get('symbol', '')
            if sym and sym not in seen_symbols:
                seen_symbols.add(sym)
                unique_tokens.append(t)
        
        # Score tokens
        scored = []
        for t in unique_tokens:
            result = self.scorer.score_token(t)
            if result['total_score'] > 20:  # Only keep decent scores
                scored.append(result)

        # Sort by total score
        scored.sort(key=lambda x: x['total_score'], reverse=True)

        # Optional LLM augmentation — only top candidates, parallel calls.
        # Disabled by default (USE_LLM_BRAIN=false) so behavior is unchanged.
        if _BRAIN_OK and hermes_brain.ENABLED and scored:
            scored = await self._augment_with_llm(scored[:25]) + scored[25:]
            scored.sort(key=lambda x: x['total_score'], reverse=True)

        return scored

    async def _augment_with_llm(self, tokens: List[Dict]) -> List[Dict]:
        """Blend an LLM narrative score (15%) into total_score for the top N tokens.

        Calls run in parallel; any failure leaves the token's score unchanged.
        """
        async def _one(t: Dict) -> Dict:
            sym = t.get('symbol', '')
            name = t.get('name', '')
            if not sym:
                return t
            try:
                v = await hermes_brain.score_narrative(sym, name)
            except Exception:
                return t
            llm_score = float(v.get('score', 50.0))
            t['llm_narrative_score'] = llm_score
            t['llm_theme'] = v.get('theme', '')
            # 85% original, 15% LLM — shifts ranking without dominating.
            t['total_score'] = t['total_score'] * 0.85 + llm_score * 0.15
            return t

        return await asyncio.gather(*(_one(t) for t in tokens))
    
    async def run(self):
        """Main scan loop."""
        await self.initialize()
        
        logger.info("=" * 60)
        logger.info("NARRATIVE BOT — Cultural Events → Crypto Scanner")
        logger.info("=" * 60)
        
        # Single scan (for testing)
        results = await self.scan()
        
        print(f"\n{'=' * 60}")
        print(f"TOP NARRATIVE OPPORTUNITIES: {len(results)} found")
        print(f"{'=' * 60}")
        
        for i, t in enumerate(results[:10], 1):
            print(f"\n{i}. {t['symbol']} | Score: {t['total_score']:.0f} | {t['recommendation']}")
            print(f"   Narrative: {t['narrative']} (fit: {t['narrative_score']:.0f})")
            print(f"   Price: ${t['price_usd']:.6f}")
            print(f"   Liq: ${t['liquidity_usd']:,.0f} | Vol: ${t['volume_24h']:,.0f}")
            print(f"   1h: {t['change_1h']:+.1f}% | 24h: {t['change_24h']:+.1f}%")
            
            if t['recommendation'] in ['STRONG_BUY', 'BUY']:
                print(f"   🎯 NARRATIVE MATCH — Event-driven opportunity")
        
        await self.close()
        return results


if __name__ == '__main__':
    bot = NarrativeBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
