"""
Narrative Scanner — Hype + Community + Trending Events
Author: Hermes | May 2026

Strategy:
- Scan Twitter/X, news, Reddit for trending topics
- Detect crypto/narrative overlap (elections, sports, viral memes, tech)
- Find Solana tokens that match the narrative
- Buy BEFORE the pump (narrative forming, not peaked)
- Sell when narrative hits mainstream (peak awareness)

Narrative scoring:
- Event probability (is this happening?)
- Community growth (holders increasing?)
- Social velocity (mentions accelerating?)
- Token age (new tokens pump harder)
- Liquidity (can we exit?)
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('NarrativeScanner')

# ── NARRATIVE PATTERNS ──
NARRATIVE_PATTERNS = {
    'election': ['trump', 'biden', 'election', 'vote', 'president', 'political'],
    'sports': ['world cup', 'super bowl', 'olympics', 'nba', 'fifa', 'champion'],
    'tech': ['ai', 'gpt', 'openai', 'apple', 'nvidia', 'bitcoin etf', 'halving'],
    'meme': ['doge', 'pepe', 'wojak', 'gigachad', 'sigma', 'based', 'mog'],
    'money': ['rich', 'wealth', 'lambo', 'moon', '1000x', 'make it'],
    'fear': ['crash', 'recession', 'bear', 'dump', 'rug'],
    'hype': ['viral', 'trending', 'explode', 'next big', 'don't miss'],
}

class TwitterScanner:
    """Scan Twitter/X for trending topics and crypto mentions."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self.bearer_token = os.getenv('TWITTER_BEARER_TOKEN', '')
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def search_recent(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search recent tweets. Returns tweets or empty list."""
        if not self.bearer_token:
            logger.debug("No Twitter bearer token")
            return []
        
        url = "https://api.twitter.com/2/tweets/search/recent"
        headers = {"Authorization": f"Bearer {self.bearer_token}"}
        params = {
            "query": f"{query} -is:retweet",
            "max_results": max_results,
            "tweet.fields": "public_metrics,created_at",
        }
        
        try:
            async with self._session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('data', [])
                logger.warning(f"Twitter API {resp.status}")
        except Exception as e:
            logger.warning(f"Twitter error: {e}")
        return []
    
    async def scan_narratives(self) -> Dict[str, Dict]:
        """Scan for all narrative patterns. Returns narrative scores."""
        narratives = {}
        
        for name, keywords in NARRATIVE_PATTERNS.items():
            total_mentions = 0
            total_engagement = 0
            
            for kw in keywords[:3]:  # Top 3 keywords per narrative
                tweets = await self.search_recent(kw, max_results=10)
                for t in tweets:
                    metrics = t.get('public_metrics', {})
                    engagement = metrics.get('like_count', 0) + metrics.get('retweet_count', 0)
                    total_mentions += 1
                    total_engagement += engagement
            
            narratives[name] = {
                'mentions': total_mentions,
                'engagement': total_engagement,
                'score': min(100, total_mentions * 5 + total_engagement * 0.1),
            }
        
        return narratives


class DexScreenerNewPairs:
    """Monitor new token pairs on DexScreener."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
    
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_new_pairs(self, limit: int = 50) -> List[Dict]:
        """Get recently created token pairs on Solana."""
        url = f"https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = []
                    for p in data:
                        if p.get('chainId') == 'solana':
                            pairs.append({
                                'symbol': p.get('symbol', '?'),
                                'name': p.get('name', ''),
                                'address': p.get('tokenAddress', ''),
                                'url': p.get('url', ''),
                                'icon': p.get('icon', ''),
                                'created_at': p.get('createdAt', ''),
                                'source': 'dexscreener',
                            })
                    return pairs[:limit]
        except Exception as e:
            logger.warning(f"DexScreener error: {e}")
        return []


class NarrativeMatcher:
    """Match tokens to narratives based on name/symbol/description."""
    
    def __init__(self):
        self.narrative_keywords = NARRATIVE_PATTERNS
    
    def match_token(self, token: Dict) -> Dict:
        """
        Check if a token matches any narrative.
        Returns: {'matches': bool, 'narrative': str, 'strength': float}
        """
        text = f"{token.get('symbol', '')} {token.get('name', '')}".lower()
        
        best_match = None
        best_strength = 0
        
        for narrative, keywords in self.narrative_keywords.items():
            matches = sum(1 for kw in keywords if kw in text)
            if matches > 0:
                strength = matches / len(keywords)
                if strength > best_strength:
                    best_strength = strength
                    best_match = narrative
        
        return {
            'matches': best_match is not None,
            'narrative': best_match,
            'strength': best_strength,
        }
    
    def score_token(self, token: Dict, narrative_data: Dict) -> float:
        """Score a token based on narrative fit + market data."""
        match = self.match_token(token)
        if not match['matches']:
            return 0
        
        score = 0
        
        # Narrative strength (how well it fits)
        score += match['strength'] * 30
        
        # Narrative momentum (is this narrative trending?)
        if match['narrative'] in narrative_data:
            narrative_score = narrative_data[match['narrative']]['score']
            score += min(30, narrative_score * 0.3)
        
        # Token metrics
        liq = token.get('liquidity_usd', 0)
        vol = token.get('volume_24h', 0)
        ch1h = token.get('change_1h', 0)
        ch24h = token.get('change_24h', 0)
        
        score += min(15, (liq / 100_000) * 2)
        score += min(15, (vol / 300_000) * 2)
        score += min(10, abs(ch1h) * 2)
        
        # Age bonus (newer tokens pump harder)
        age_hours = token.get('age_hours', 24)
        if age_hours < 1:
            score += 20  # Brand new
        elif age_hours < 6:
            score += 15
        elif age_hours < 24:
            score += 10
        
        return score


class NarrativePositionManager:
    """Manage narrative-based positions."""
    
    def __init__(self):
        self.positions: Dict[str, Dict] = {}
        self.history: List[Dict] = []
        self.balance = 83.0
        self.load()
    
    def load(self):
        try:
            with open('data/narrative_positions.json') as f:
                data = json.load(f)
                self.positions = data.get('positions', {})
                self.history = data.get('history', [])
        except FileNotFoundError:
            pass
    
    def save(self):
        os.makedirs('data', exist_ok=True)
        with open('data/narrative_positions.json', 'w') as f:
            json.dump({
                'positions': self.positions,
                'history': self.history[-50:],
                'updated_at': datetime.now().isoformat(),
            }, f, indent=2, default=str)
    
    def open(self, token: Dict, narrative: str):
        sym = token['symbol']
        if sym in self.positions:
            return
        
        if len(self.positions) >= 5:
            return
        
        size = min(self.balance * 0.15, 15.0)  # 15% max per narrative trade
        if size < 5:
            return
        
        self.positions[sym] = {
            'symbol': sym,
            'narrative': narrative,
            'entry': token.get('price_usd', 0),
            'size': size,
            'opened_at': datetime.now().isoformat(),
            'highest_price': token.get('price_usd', 0),
            'status': 'open',
        }
        self.balance -= size
        self.save()
        logger.info(f"📈 NARRATIVE OPEN {sym}: ${size:.2f} | Narrative: {narrative}")
    
    def check_exits(self, current_prices: Dict[str, float]):
        """Check narrative positions for exits."""
        to_close = []
        now = datetime.now()
        
        for sym, pos in self.positions.items():
            current = current_prices.get(sym)
            if not current:
                continue
            
            entry = pos['entry']
            pnl_pct = (current - entry) / entry
            
            # Update highest
            if current > pos['highest_price']:
                pos['highest_price'] = current
            
            # Take profit tiers
            if pnl_pct >= 1.0:  # +100%
                to_close.append((sym, 'tp_100', pnl_pct))
            elif pnl_pct >= 0.5:  # +50%
                to_close.append((sym, 'tp_50', pnl_pct))
            elif pnl_pct >= 0.3:  # +30%
                to_close.append((sym, 'tp_30', pnl_pct))
            
            # Trailing stop after +20%
            if pnl_pct > 0.2:
                trail = pos['highest_price'] * 0.8
                if current < trail:
                    to_close.append((sym, 'trailing_stop', pnl_pct))
            
            # Time stop (72h for narrative plays)
            opened = datetime.fromisoformat(pos['opened_at'])
            if (now - opened).total_seconds() > 72 * 3600:
                to_close.append((sym, 'time_stop', pnl_pct))
        
        return to_close
    
    def close(self, sym: str, reason: str, pnl_pct: float):
        if sym not in self.positions:
            return
        
        pos = self.positions[sym]
        size = pos['size']
        pnl = size * pnl_pct
        
        self.balance += size + pnl
        self.history.append({
            'symbol': sym,
            'narrative': pos['narrative'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'opened_at': pos['opened_at'],
            'closed_at': datetime.now().isoformat(),
        })
        
        del self.positions[sym]
        self.save()
        
        if pnl > 0:
            logger.info(f"✅ NARRATIVE CLOSE {sym}: ${pnl:+.2f} ({pnl_pct:+.1%}) | {reason}")
        else:
            logger.info(f"❌ NARRATIVE CLOSE {sym}: ${pnl:+.2f} ({pnl_pct:+.1%}) | {reason}")


class NarrativeBot:
    """Main narrative trading bot."""
    
    def __init__(self):
        self.twitter = TwitterScanner()
        self.dex = DexScreenerNewPairs()
        self.matcher = NarrativeMatcher()
        self.positions = NarrativePositionManager()
        self.seen_tokens: set = set()
    
    async def initialize(self):
        await self.twitter.initialize()
        await self.dex.initialize()
    
    async def close(self):
        await self.twitter.close()
        await self.dex.close()
    
    async def scan(self) -> List[Dict]:
        """Full narrative scan."""
        logger.info("🔍 Scanning narratives...")
        
        # 1. Get trending narratives
        narratives = await self.twitter.scan_narratives()
        active_narratives = {k: v for k, v in narratives.items() if v['score'] > 10}
        
        if not active_narratives:
            logger.info("No active narratives detected")
            return []
        
        logger.info(f"Active narratives: {', '.join(active_narratives.keys())}")
        
        # 2. Get new tokens
        new_tokens = await self.dex.get_new_pairs(limit=50)
        
        # 3. Match tokens to narratives
        candidates = []
        for token in new_tokens:
            if token['symbol'] in self.seen_tokens:
                continue
            
            score = self.matcher.score_token(token, active_narratives)
            if score > 40:
                token['narrative_score'] = score
                token['narrative'] = self.matcher.match_token(token)['narrative']
                candidates.append(token)
                self.seen_tokens.add(token['symbol'])
        
        # 4. Also check trending pools for narrative fit
        # (Would integrate with GeckoTerminal here)
        
        candidates.sort(key=lambda x: x['narrative_score'], reverse=True)
        return candidates
    
    async def run(self):
        await self.initialize()
        
        logger.info("=" * 60)
        logger.info("NARRATIVE BOT STARTED")
        logger.info("Strategy: Ride hype waves before they peak")
        logger.info("=" * 60)
        
        while True:
            try:
                # Scan for opportunities
                candidates = await self.scan()
                
                # Open positions
                for token in candidates[:3]:
                    narrative = token.get('narrative', 'unknown')
                    logger.info(f"🎯 NARRATIVE MATCH: {token['symbol']} | {narrative} | Score: {token['narrative_score']:.0f}")
                    self.positions.open(token, narrative)
                
                # Check exits
                # (Would need real-time price feed here)
                
                # Report
                logger.info(f"📊 Balance: ${self.positions.balance:.2f} | Positions: {len(self.positions.positions)}")
                
            except Exception as e:
                logger.error(f"Loop error: {e}")
            
            await asyncio.sleep(600)  # 10 min scan interval


if __name__ == '__main__':
    bot = NarrativeBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped")
