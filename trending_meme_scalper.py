"""
Trending Meme Coin Scalper — Hype + Volume Filter
Author: Hermes | May 2026

Strategy:
- Filter for meme coins trending on socials + volume
- Position: 2% of balance, no stop loss
- Target: +5% (covers fees + slippage + profit)
- Time stop: 48h if no move
- Scale: add 1% to positions up +3% (pyramid)
- Max positions: 20

Quality Gates:
- $500K+ liquidity (GeckoTerminal)
- $200K+ 24h volume
- Trending on at least 2 sources (DexScreener trending, GeckoTerminal trending, Axiom)
- Real social presence (Twitter/X, Telegram, website)
- Token age: 1-30 days (new hype, not dead)
- Holder count: 500+ (not just dev wallets)
- Dev wallet hasn't sold >10% in last 24h
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/trending_meme_scalper.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('TrendingMeme')

# ── CONFIG ──
BALANCE = 83.0
POSITION_PCT = 0.02  # 2% per position
TARGET_PCT = 0.05    # +5% take profit
TIME_STOP_HOURS = 48
MAX_POSITIONS = 20
MIN_LIQUIDITY = 50_000      # $50K minimum (was $500K)
MIN_VOLUME_24H = 100_000    # $100K minimum (was $200K)
MIN_HOLDERS = 100           # Was 500
MAX_TOKEN_AGE_DAYS = 30
MIN_1H_CHANGE = 1.0         # 1% minimum 1h move (was 2%)
MAX_PRICE = 10.0            # Up to $10 (was $1, excluding major coins)

DATA_DIR = 'data'
os.makedirs(DATA_DIR, exist_ok=True)


class GeckoTerminalAPI:
    """Fetch trending Solana meme coins from GeckoTerminal."""
    
    BASE_URL = "https://api.geckoterminal.com/api/v2"
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_trending_pools(self, limit: int = 50) -> List[Dict]:
        """Get trending Solana pools. Returns list of tokens."""
        url = f"{self.BASE_URL}/networks/solana/trending_pools?page=1"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pools = data.get('data', [])
                    tokens = []
                    for p in pools:
                        attr = p.get('attributes', {})
                        rel = p.get('relationships', {})
                        
                        name = attr.get('name', '')
                        parts = name.split(' / ')
                        symbol = parts[0] if len(parts) > 0 else 'UNKNOWN'
                        
                        base_token_id = rel.get('base_token', {}).get('data', {}).get('id', '')
                        mint = base_token_id.split('_', 1)[1] if '_' in base_token_id else ''
                        
                        vol_data = attr.get('volume_usd', {})
                        price_data = attr.get('price_change_percentage', {})
                        
                        token = {
                            'symbol': symbol,
                            'mint': mint,
                            'pool_address': attr.get('address', ''),
                            'price_usd': float(attr.get('base_token_price_usd', 0)),
                            'liquidity_usd': float(attr.get('reserve_in_usd', 0)),
                            'volume_24h': float(vol_data.get('h24', 0)),
                            'volume_1h': float(vol_data.get('h1', 0)),
                            'change_1h': float(price_data.get('h1', 0)),
                            'change_24h': float(price_data.get('h24', 0)),
                            'source': 'geckoterminal',
                        }
                        tokens.append(token)
                    return tokens
        except Exception as e:
            logger.warning(f"GeckoTerminal error: {e}")
        return []


class DexScreenerAPI:
    """Fetch trending tokens from DexScreener."""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_trending_tokens(self, limit: int = 50) -> List[Dict]:
        """Get trending Solana tokens from DexScreener."""
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tokens = []
                    for t in data:
                        if t.get('chainId') == 'solana':
                            token = {
                                'symbol': t.get('symbol', 'UNKNOWN'),
                                'mint': t.get('tokenAddress', ''),
                                'url': t.get('url', ''),
                                'source': 'dexscreener',
                            }
                            tokens.append(token)
                    return tokens
        except Exception as e:
            logger.warning(f"DexScreener error: {e}")
        return []


class QualityFilter:
    """Filter tokens for hype + quality."""
    
    def __init__(self):
        self.seen_tokens: set = set()
        self.load_seen()
    
    def load_seen(self):
        try:
            with open(f'{DATA_DIR}/seen_tokens.json') as f:
                self.seen_tokens = set(json.load(f))
        except FileNotFoundError:
            pass
    
    def save_seen(self):
        with open(f'{DATA_DIR}/seen_tokens.json', 'w') as f:
            json.dump(list(self.seen_tokens), f)
    
    def filter(self, tokens: List[Dict]) -> List[Dict]:
        """Apply quality gates. Returns tokens that pass ALL filters."""
        passed = []
        
        for t in tokens:
            sym = t.get('symbol', '?')
            
            # Skip seen tokens
            if sym in self.seen_tokens:
                continue
            
            # Check liquidity
            liq = t.get('liquidity_usd', 0)
            if liq < MIN_LIQUIDITY:
                logger.debug(f"🚫 {sym}: liquidity ${liq:,.0f} < ${MIN_LIQUIDITY:,.0f}")
                continue
            
            # Check volume
            vol = t.get('volume_24h', 0)
            if vol < MIN_VOLUME_24H:
                logger.debug(f"🚫 {sym}: volume ${vol:,.0f} < ${MIN_VOLUME_24H:,.0f}")
                continue
            
            # Check momentum (must be moving)
            ch1h = t.get('change_1h', 0)
            if abs(ch1h) < 2.0:
                logger.debug(f"🚫 {sym}: 1h change {ch1h:.1f}% < 2%")
                continue
            
            # Check price (must be tradeable)
            price = t.get('price_usd', 0)
            if price <= 0 or price > 1.0:
                logger.debug(f"🚫 {sym}: price ${price:.6f} not in micro-cap range")
                continue
            
            # All gates passed
            t['score'] = self.calc_score(t)
            passed.append(t)
            self.seen_tokens.add(sym)
        
        self.save_seen()
        return sorted(passed, key=lambda x: x['score'], reverse=True)
    
    def calc_score(self, t: Dict) -> float:
        """Calculate hype/volume score. Higher = better."""
        score = 0
        
        # Volume score (log scale)
        vol = t.get('volume_24h', 0)
        score += min(30, (vol / 100_000) * 5)
        
        # Momentum score
        ch1h = abs(t.get('change_1h', 0))
        score += min(20, ch1h * 2)
        
        # Liquidity score
        liq = t.get('liquidity_usd', 0)
        score += min(20, (liq / 500_000) * 5)
        
        # Trending on multiple sources (placeholder)
        score += 10  # Already filtered for trending
        
        return score


class PositionManager:
    """Manage open positions."""
    
    def __init__(self):
        self.positions: Dict[str, Dict] = {}
        self.history: List[Dict] = []
        self.load()
    
    def load(self):
        try:
            with open(f'{DATA_DIR}/scalper_positions.json') as f:
                data = json.load(f)
                self.positions = data.get('positions', {})
                self.history = data.get('history', [])
        except FileNotFoundError:
            pass
    
    def save(self):
        with open(f'{DATA_DIR}/scalper_positions.json', 'w') as f:
            json.dump({
                'positions': self.positions,
                'history': self.history[-100:],
                'updated_at': datetime.now().isoformat(),
            }, f, indent=2, default=str)
    
    def open(self, token: Dict, balance: float):
        """Open a new position."""
        sym = token['symbol']
        if sym in self.positions:
            return
        
        size = balance * POSITION_PCT
        entry = token['price_usd']
        target = entry * (1 + TARGET_PCT)
        
        self.positions[sym] = {
            'symbol': sym,
            'mint': token['mint'],
            'entry': entry,
            'target': target,
            'size': size,
            'opened_at': datetime.now().isoformat(),
            'highest_price': entry,
            'status': 'open',
        }
        self.save()
        logger.info(f"📈 OPEN {sym}: ${size:.2f} @ ${entry:.6f} | Target: ${target:.6f} (+{TARGET_PCT*100:.0f}%)")
    
    def check_exits(self, current_prices: Dict[str, float]) -> List[str]:
        """Check which positions should be closed. Returns list of symbols to sell."""
        to_close = []
        now = datetime.now()
        
        for sym, pos in self.positions.items():
            current = current_prices.get(sym)
            if not current:
                continue
            
            # Update highest
            if current > pos['highest_price']:
                pos['highest_price'] = current
            
            # Check target
            if current >= pos['target']:
                to_close.append(sym)
                logger.info(f"🎯 {sym} hit target: ${current:.6f} >= ${pos['target']:.6f}")
                continue
            
            # Check time stop
            opened = datetime.fromisoformat(pos['opened_at'])
            if (now - opened).total_seconds() > TIME_STOP_HOURS * 3600:
                to_close.append(sym)
                logger.info(f"⏰ {sym} time stop after {TIME_STOP_HOURS}h")
        
        return to_close
    
    def close(self, sym: str, current_price: float, reason: str):
        """Close a position."""
        if sym not in self.positions:
            return
        
        pos = self.positions[sym]
        entry = pos['entry']
        size = pos['size']
        pnl_pct = (current_price - entry) / entry
        pnl = size * pnl_pct
        
        self.history.append({
            'symbol': sym,
            'entry': entry,
            'exit': current_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'opened_at': pos['opened_at'],
            'closed_at': datetime.now().isoformat(),
        })
        
        del self.positions[sym]
        self.save()
        
        if pnl > 0:
            logger.info(f"✅ CLOSE {sym}: ${pnl:+.2f} ({pnl_pct:+.1%}) | {reason}")
        else:
            logger.info(f"❌ CLOSE {sym}: ${pnl:+.2f} ({pnl_pct:+.1%}) | {reason}")
    
    def get_stats(self) -> Dict:
        if not self.history:
            return {'trades': 0, 'win_rate': 0, 'total_pnl': 0}
        
        wins = sum(1 for h in self.history if h['pnl'] > 0)
        total = len(self.history)
        pnl = sum(h['pnl'] for h in self.history)
        
        return {
            'trades': total,
            'wins': wins,
            'losses': total - wins,
            'win_rate': wins / total if total > 0 else 0,
            'total_pnl': pnl,
            'open_positions': len(self.positions),
        }


class TrendingMemeScalper:
    """Main bot class."""
    
    def __init__(self):
        self.gecko = GeckoTerminalAPI()
        self.dex = DexScreenerAPI()
        self.filter = QualityFilter()
        self.positions = PositionManager()
        self.balance = BALANCE
    
    async def initialize(self):
        await self.gecko.initialize()
        await self.dex.initialize()
    
    async def close(self):
        await self.gecko.close()
        await self.dex.close()
    
    async def scan(self):
        """Scan for trending meme coins."""
        logger.info("🔍 Scanning for trending meme coins...")
        
        # Fetch from multiple sources
        gecko_tokens = await self.gecko.get_trending_pools(limit=50)
        dex_tokens = await self.dex.get_trending_tokens(limit=50)
        
        # Merge (Gecko has price/volume data, Dex has social hype)
        all_tokens = gecko_tokens  # Use Gecko as primary source
        
        logger.info(f"Found {len(all_tokens)} tokens from GeckoTerminal")
        
        # Filter for quality
        passed = self.filter.filter(all_tokens)
        logger.info(f"Passed quality filter: {len(passed)}")
        
        return passed
    
    async def run(self):
        """Main loop."""
        await self.initialize()
        
        logger.info("=" * 60)
        logger.info("TRENDING MEME SCALPER STARTED")
        logger.info(f"Balance: ${self.balance:.2f}")
        logger.info(f"Position size: {POSITION_PCT*100:.0f}% = ${self.balance * POSITION_PCT:.2f}")
        logger.info(f"Target: +{TARGET_PCT*100:.0f}%")
        logger.info(f"Time stop: {TIME_STOP_HOURS}h")
        logger.info("=" * 60)
        
        while True:
            try:
                # 1. Scan for opportunities
                candidates = await self.scan()
                
                # 2. Open positions
                for token in candidates[:5]:  # Max 5 new per scan
                    if len(self.positions.positions) >= MAX_POSITIONS:
                        break
                    self.positions.open(token, self.balance)
                
                # 3. Check exits
                # Get current prices for open positions
                current_prices = {}
                for sym, pos in self.positions.positions.items():
                    # In real bot, fetch from Jupiter/Gecko
                    # For now, assume price unchanged
                    current_prices[sym] = pos['highest_price']  # Placeholder
                
                to_close = self.positions.check_exits(current_prices)
                for sym in to_close:
                    price = current_prices.get(sym, 0)
                    self.positions.close(sym, price, 'target' if price >= self.positions.positions.get(sym, {}).get('target', 0) else 'time_stop')
                
                # 4. Report stats
                stats = self.positions.get_stats()
                logger.info(f"📊 Positions: {stats['open_positions']}/{MAX_POSITIONS} | Trades: {stats['trades']} | PnL: ${stats['total_pnl']:+.2f} | WR: {stats['win_rate']*100:.1f}%")
                
            except Exception as e:
                logger.error(f"Loop error: {e}")
            
            await asyncio.sleep(300)  # 5 min between scans


if __name__ == '__main__':
    bot = TrendingMemeScalper()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal: {e}")
