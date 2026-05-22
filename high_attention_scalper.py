"""
High-Attention Micro Coin Scalper
Focus: ATTENTION (52xfJnaHZzxAddm74SVyxmdyLJ6qrrW8WN2U3SjmxaVB) + similar high-attention tokens
Strategy: Volume spike detection → quick scalp with tight profit targets
Author: Hermes | May 2026
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

import aiohttp

# Import symbol filter for whitelist checking
from symbol_filter import SYMBOL_WHITELIST, SYMBOL_BLACKLIST

logger = logging.getLogger('HighAttention')

HERE = os.path.dirname(os.path.abspath(__file__))

# ── CONFIG ──
HIGH_ATTENTION_CONFIG = {
    'position_pct': 0.03,      # 3% per trade
    'target_pct': 0.05,        # +5% take profit
    'stop_loss_pct': 0.20,     # -20% stop loss
    'time_stop_hours': 48,
    'max_positions': 20,
    'min_liquidity': 10_000,   # $10K minimum for micros
    'min_volume_24h': 5_000,
    'min_holders': 20,
    'max_token_age_days': 30,
    'auto_discover': True,
    'volume_spike_threshold': 3.0,  # 3x average volume
    'reentry_block_hours': 2,
}

# Default watchlist — Operator can override
DEFAULT_WATCHLIST = [
    {
        'symbol': 'ATTENTION',
        'mint': '52xfJnaHZzxAddm74SVyxmdyLJ6qrrW8WN2U3SjmxaVB',
        'chain': 'solana',
        'priority': 1,  # Highest
    },
]


class DexScreenerAPI:
    """Fetch token data from DexScreener."""
    
    BASE_URL = "https://api.dexscreener.com"
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        
    async def initialize(self):
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={"Accept": "application/json"}
        )
        
    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def get_token_data(self, mint: str) -> Optional[Dict[str, Any]]:
        """Get real-time data for a specific token by mint.
        
        Tries tokens endpoint first, falls back to search by pair address.
        """
        # Method 1: Direct token endpoint
        url = f"{self.BASE_URL}/tokens/v1/solana/{mint}"
        try:
            async with self._session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data if isinstance(data, list) else data.get('pairs', [])
                    if pairs:
                        best = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                        return self._normalize_token(best, mint)
        except Exception:
            pass
        
        # Method 2: Search by mint address (DexScreener search)
        search_url = f"{self.BASE_URL}/latest/dex/search?q={mint}"
        try:
            async with self._session.get(search_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get('pairs', [])
                    if pairs:
                        # Filter for Solana pairs with this mint as base
                        matching = [p for p in pairs if p.get('chainId') == 'solana']
                        if matching:
                            best = max(matching, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                            return self._normalize_token(best, mint)
        except Exception:
            pass
        
        # Method 3: Search by token symbol name
        try:
            # Try to get symbol from watchlist or known tokens
            symbol = None
            for w in self._watchlist_fallback():
                if w.get('mint') == mint:
                    symbol = w.get('symbol')
                    break
            
            if symbol:
                search_url2 = f"{self.BASE_URL}/latest/dex/search?q={symbol}"
                async with self._session.get(search_url2, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get('pairs', [])
                        if pairs:
                            # Find pair with matching base token
                            for p in pairs:
                                base = p.get('baseToken', {}).get('address', '')
                                if base == mint:
                                    return self._normalize_token(p, mint)
                            # Fallback: just use first Solana pair
                            sol_pairs = [p for p in pairs if p.get('chainId') == 'solana']
                            if sol_pairs:
                                best = max(sol_pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                                return self._normalize_token(best, mint)
        except Exception:
            pass
        
        logger.warning(f"DexScreener: No data found for mint {mint}")
        return None
    
    def _watchlist_fallback(self):
        """Fallback watchlist for symbol lookup."""
        return [
            {'symbol': 'ATTENTION', 'mint': '52xfJnaHZzxAddm74SVyxmdyLJ6qrrW8WN2U3SjmxaVB'},
        ]
    
    async def get_trending_tokens(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get trending Solana tokens."""
        url = f"{self.BASE_URL}/token-profiles/latest/v1"
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
                                'priority': 5,  # Lower than manual watchlist
                            }
                            tokens.append(token)
                    return tokens[:limit]
        except Exception as e:
            logger.warning(f"DexScreener trending error: {e}")
        return []
    
    def _normalize_token(self, pair: Dict, mint: str) -> Dict[str, Any]:
        """Normalize DexScreener pair data to our format."""
        attr = pair.get('attributes', pair)  # Handle both formats
        return {
            'symbol': attr.get('baseToken', {}).get('symbol') or attr.get('symbol', 'UNKNOWN'),
            'mint': mint,
            'price_usd': float(attr.get('priceUsd', 0) or attr.get('priceNative', 0)),
            'liquidity_usd': float(attr.get('liquidity', {}).get('usd', 0) or 0),
            'volume_24h': float(attr.get('volume', {}).get('h24', 0) or 0),
            'volume_1h': float(attr.get('volume', {}).get('h1', 0) or 0),
            'change_1h': float(attr.get('priceChange', {}).get('h1', 0) or 0),
            'change_24h': float(attr.get('priceChange', {}).get('h24', 0) or 0),
            'buys_24h': int(attr.get('txns', {}).get('h24', {}).get('buys', 0) or 0),
            'sells_24h': int(attr.get('txns', {}).get('h24', {}).get('sells', 0) or 0),
            'holder_count': int(attr.get('holders', 0) or 0),
            'source': 'dexscreener',
            'priority': 1,
        }


class HighAttentionEngine:
    """Engine for high-attention micro coin trading."""
    
    def __init__(self, config: Dict = None):
        self.config = config or HIGH_ATTENTION_CONFIG
        self.watchlist = self._load_watchlist()
        self.api = DexScreenerAPI()
        self.recently_bought: Dict[str, datetime] = {}
        self.volume_history: Dict[str, List[float]] = {}  # Track volume for spike detection
        
    def _load_watchlist(self) -> List[Dict]:
        """Load watchlist from config or use default."""
        watchlist_path = os.path.join(HERE, 'state', 'high_attention_watchlist.json')
        if os.path.exists(watchlist_path):
            try:
                with open(watchlist_path) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load watchlist: {e}")
        return DEFAULT_WATCHLIST.copy()
    
    def save_watchlist(self):
        """Persist watchlist to disk."""
        watchlist_path = os.path.join(HERE, 'state', 'high_attention_watchlist.json')
        os.makedirs(os.path.dirname(watchlist_path), exist_ok=True)
        with open(watchlist_path, 'w') as f:
            json.dump(self.watchlist, f, indent=2)
    
    def add_token(self, symbol: str, mint: str, priority: int = 5):
        """Add a token to the watchlist."""
        # Check if already exists
        for t in self.watchlist:
            if t['mint'] == mint:
                t['priority'] = min(t['priority'], priority)
                return
        self.watchlist.append({
            'symbol': symbol,
            'mint': mint,
            'chain': 'solana',
            'priority': priority,
        })
        self.save_watchlist()
        logger.info(f"Added {symbol} to high-attention watchlist")
    
    async def initialize(self):
        await self.api.initialize()
        logger.info("🔥 High-Attention engine initialized")
    
    async def close(self):
        await self.api.close()
    
    def is_reentry_blocked(self, symbol: str) -> bool:
        """Check if symbol is in cooldown."""
        ts = self.recently_bought.get(symbol.upper())
        if ts:
            hours = self.config.get('reentry_block_hours', 2)
            if datetime.now() - ts < timedelta(hours=hours):
                return True
            # Clean up expired entry
            del self.recently_bought[symbol.upper()]
        return False
    
    def block_reentry(self, symbol: str):
        """Block re-entry for configured hours."""
        self.recently_bought[symbol.upper()] = datetime.now()
    
    def detect_volume_spike(self, symbol: str, volume_1h: float) -> bool:
        """Detect if volume is spiking above threshold."""
        history = self.volume_history.get(symbol, [])
        history.append(volume_1h)
        # Keep last 24 samples (24h of hourly volume)
        if len(history) > 24:
            history = history[-24:]
        self.volume_history[symbol] = history
        
        if len(history) < 6:  # Need at least 6 hours of data
            return False
        
        avg_volume = sum(history[:-1]) / len(history[:-1])  # Exclude current
        threshold = self.config.get('volume_spike_threshold', 3.0)
        spike = volume_1h > (avg_volume * threshold)
        
        if spike:
            logger.info(f"📊 {symbol} volume spike: {volume_1h:.0f} vs avg {avg_volume:.0f} ({volume_1h/avg_volume:.1f}x)")
        
        return spike
    
    def evaluate_entry(self, token: Dict, balance: float) -> Optional[Dict]:
        """
        Evaluate if a token meets high-attention entry criteria.
        Handles both watchlist tokens AND Pump.fun early launches.
        Returns position dict or None.
        """
        symbol = token.get('symbol', 'UNKNOWN')
        
        # Skip if in cooldown
        if self.is_reentry_blocked(symbol):
            logger.debug(f"⏱️ {symbol} — reentry blocked")
            return None
        
        # ── PUMP.FUN EARLY ENTRY LOGIC ──
        is_pumpfun = token.get('is_pumpfun', False)
        if is_pumpfun:
            return self._evaluate_pumpfun_entry(token, balance)
        
        # ── REGULAR HIGH-ATTENTION LOGIC (DexScreener/GeckoTerminal) ──
        return self._evaluate_standard_entry(token, balance)
    
    def _evaluate_pumpfun_entry(self, token: Dict, balance: float) -> Optional[Dict]:
        """
        EVALUATE PUMP.FUN LAUNCH — Ultra-early entry.
        Different criteria because these are minutes old.
        """
        symbol = token.get('symbol', 'UNKNOWN')
        age_min = token.get('age_minutes', 999)
        
        # STRICT: Must be <15 minutes old (not 30)
        if age_min > 15:
            logger.debug(f"🕐 {symbol} too old: {age_min:.0f}m (max 15m)")
            return None
        
        # Market cap filter (avoid already-pumped)
        mcap = token.get('market_cap', 0)
        if mcap > 50_000:  # TIGHTER: Skip if already >$50K mcap
            logger.debug(f"📈 {symbol} mcap too high: ${mcap:.0f}")
            return None
        if mcap < 5_000:
            logger.debug(f"📉 {symbol} mcap too low: ${mcap:.0f}")
            return None
        
        # Social engagement ( replies = hype )
        replies = token.get('reply_count', 0)
        if replies < 10:  # TIGHTER: At least 10 replies
            logger.debug(f"💬 {symbol} not enough replies: {replies}")
            return None
        
        # Position sizing for Pump.fun — ALWAYS $2.50 max
        size = 2.50  # Fixed small size — Pump.fun is gambling
        logger.info(f"📊 {symbol} PUMP.FUN position: $2.50 (gamble mode)")
        
        # FINAL MINIMUM CHECK
        if size < 2.50:
            logger.warning(f"💰 {symbol} Pump.fun position ${size:.2f} below $2.50 minimum — SKIPPED")
            return None
        
        entry_price = token.get('price_usd', 0)
        if entry_price <= 0:
            return None
        
        quantity = size / entry_price
        
        # TIGHTER stops for Pump.fun — 35% to match catastrophe floor
        stop_pct = 0.35  # 35% stop (was 50%, now matches system-wide floor)
        stop_price = entry_price * (1 - stop_pct)
        
        position = {
            'token': symbol,  # Bot compatibility
            'symbol': symbol,
            'address': token.get('mint', ''),  # Bot compatibility
            'token_address': token.get('mint', ''),
            'entry': entry_price,
            'invested': size,
            'quantity': quantity,
            # CRITICAL: These are required by monitor/check_exit
            'highest_price': entry_price,
            'last_price': entry_price,
            'stop': stop_price,
            'stop_type': 'fixed',
            'opened_at': datetime.now(timezone.utc).isoformat(),
            'source': 'pumpfun',
            'mode_at_entry': 'HIGH_ATTENTION',
            'momentum_score': token.get('reply_count', 0),  # Use replies as momentum
            'liquidity': token.get('liquidity_usd', 0),
            'volume_1h': token.get('volume_1h', 0),
            'volume_24h': token.get('volume_24h', 0),
            'change_1h': token.get('change_1h', 0),
            'change_24h': token.get('change_24h', 0),
            'holders': token.get('holder_count', 0),
            'age_minutes': age_min,
            'market_cap': token.get('market_cap', 0),
            'is_pumpfun': True,
            # Risk params — TIGHTER for early launches
            'stop_loss_pct': stop_pct,
            'take_profit_pct': 1.00,  # +100% take profit (moon or die)
            'time_stop_hours': 1,     # 1 hour max hold (dump fast)
            'tier_exits': {'1': False, '2': False, '3': False},
            'take_profit_r': 2.0,     # 2R target (100% gain)
            'risk_pct': stop_pct,
        }
        
        logger.info(
            f"🚀 PUMP.FUN ENTRY: {symbol} | "
            f"Age: {age_min:.0f}m | Price: ${entry_price:.8f} | "
            f"Size: ${size:.2f} | Stop: ${stop_price:.8f} | "
            f"MC: ${mcap:.0f} | Replies: {replies}"
        )
        
        return position
    
    def _evaluate_standard_entry(self, token: Dict, balance: float) -> Optional[Dict]:
        """
        EVALUATE STANDARD HIGH-ATTENTION TOKEN (DexScreener/GeckoTerminal).
        Uses the original logic.
        """
        symbol = token.get('symbol', 'UNKNOWN')
        sym_upper = symbol.upper()
        
        # Check if whitelist symbol — relaxed criteria
        is_whitelist = sym_upper in SYMBOL_WHITELIST
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # HARD FILTERS — NO EXCEPTIONS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        # 1. LIQUIDITY — must have $10K+ (prevents rug pulls)
        liquidity = token.get('liquidity_usd', 0)
        if liquidity < 10_000:
            logger.debug(f"💧 {symbol} liquidity too low: ${liquidity:.0f} < $10K")
            return None
        
        # 2. VOLUME — must have $5K+ 24h (proves activity)
        volume_24h = token.get('volume_24h', 0)
        if volume_24h < 5_000:
            logger.debug(f"📉 {symbol} 24h volume too low: ${volume_24h:.0f} < $5K")
            return None
        
        # 3. VOLUME SPIKE — must be spiking NOW (catches early moves)
        volume_1h = token.get('volume_1h', 0)
        has_spike = self.detect_volume_spike(symbol, volume_1h)
        if not has_spike:
            logger.debug(f"📊 {symbol} NO VOLUME SPIKE — SKIPPED")
            return None
        logger.info(f"📊 {symbol} volume spike confirmed: ${volume_1h:.0f}")
        
        # 4. PRICE ACTION — must be green 1h (momentum)
        change_1h = token.get('change_1h', 0)
        if change_1h < 2.0:  # At least +2% in last hour
            logger.debug(f"📉 {symbol} 1h change too low: {change_1h:.1f}% < +2%")
            return None
        
        # 5. MAX 1H — don't chase if already up 50%+ in 1h (peaked)
        if change_1h > 50.0 and not is_whitelist:
            logger.debug(f"🚀 {symbol} already up {change_1h:.0f}% in 1h — peaked")
            return None
        
        # 6. MAX 24H — don't chase if already up 200%+ (pump is done)
        change_24h = token.get('change_24h', 0)
        max_24h = 100 if is_whitelist else 50  # TIGHT: 50% for unknown, 100% for whitelist
        if change_24h > max_24h:
            logger.debug(f"🚀 {symbol} already up {change_24h:.0f}% in 24h — chasing (max {max_24h}%)")
            return None
        
        # 7. NO DUMPERS — skip if down more than 10% recently
        if change_24h < -10:
            logger.debug(f"📉 {symbol} dumping: {change_24h:.1f}% < -10%")
            return None
        
        # 8. AGE — skip tokens >30 minutes old (pump already happened)
        age_min = token.get('age_minutes', 999)
        if age_min > 30 and not is_whitelist:
            logger.debug(f"🕐 {symbol} too old: {age_min:.0f}m > 30m (pump done)")
            return None
        
        # 9. MOMENTUM SCORE — must have strong recent activity
        momentum = token.get('momentum_score', change_1h)
        if momentum < 5.0 and not is_whitelist:
            logger.debug(f"📊 {symbol} momentum too weak: {momentum:.1f} < 5")
            return None
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # POSITION SIZING — SMALL ON UNKNOWN, BIGGER ON PROVEN
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if is_whitelist:
            from symbol_filter import get_position_size_pct
            position_pct = get_position_size_pct(sym_upper, self.config['position_pct'])
            logger.info(f"📊 {symbol} WHITELIST position: {position_pct*100:.1f}% of balance")
            size = balance * position_pct
            size = min(size, 15.0)  # Whitelist max $15
        else:
            # UNKNOWN tokens: MAX $2.50 (minimum viable)
            size = 2.50
            logger.info(f"📊 {symbol} DISCOVERY position: $2.50 (unknown token)")
        
        size = max(size, 2.50)  # Floor
        size = min(size, 50.0)   # Hard ceiling
        
        # ACCOUNT FOR SWAP FEES: reduce gross size so net position ≥ $2.50
        from fee_calculator import apply_entry_cost, estimate_swap_cost
        net_size = apply_entry_cost(size)
        if net_size < size:
            fee_info = estimate_swap_cost(size)
            logger.info(f"💸 {symbol} fees: ${fee_info['entry_cost_usd']:.2f} entry, ${fee_info['roundtrip_cost_usd']:.2f} roundtrip")
            size = net_size
        
        size = max(size, 2.50)  # Your $2.50 minimum (net, after fees)
        size = min(size, 50.0)  # Cap at $50
        
        # Slippage estimate — can reduce size below $2.50
        if liquidity > 0:
            slippage = (size / liquidity) * 100
            if slippage > 2.0:
                size = liquidity * 0.02
                logger.info(f"📐 {symbol} position capped at ${size:.2f} due to slippage")
        
        # FINAL MINIMUM CHECK — after ALL sizing logic (including fees)
        if size < 2.50:
            logger.warning(f"💰 {symbol} position ${size:.2f} below $2.50 minimum (after fees) — SKIPPED")
            return None
        
        # Hard cap at $50
        size = min(size, 50.0)
        
        entry_price = token.get('price_usd', 0)
        if entry_price <= 0:
            return None
        
        quantity = size / entry_price
        
        # Calculate stop loss (using bot's calc_stop logic)
        stop_pct = self.config['stop_loss_pct']  # 20% default
        stop_price = entry_price * (1 - stop_pct)
        
        position = {
            'token': symbol,  # Bot compatibility
            'symbol': symbol,
            'address': token.get('mint', ''),  # Bot compatibility
            'token_address': token.get('mint', ''),
            'entry': entry_price,
            'invested': size,
            'quantity': quantity,
            # CRITICAL: These are required by monitor/check_exit
            'highest_price': entry_price,
            'last_price': entry_price,
            'stop': stop_price,
            'stop_type': 'fixed',
            'opened_at': datetime.now(timezone.utc).isoformat(),
            'source': 'high_attention',
            'mode_at_entry': 'HIGH_ATTENTION',
            'momentum_score': change_1h,
            'liquidity': liquidity,
            'volume_1h': volume_1h,
            'volume_24h': volume_24h,
            'change_1h': change_1h,
            'change_24h': change_24h,
            'holders': holders,
            'stop_loss_pct': self.config['stop_loss_pct'],
            'take_profit_pct': self.config['target_pct'],
            'time_stop_hours': self.config['time_stop_hours'],
            'tier_exits': {'1': False, '2': False, '3': False},
            'take_profit_r': 4.0,
            'risk_pct': self.config['stop_loss_pct'],
        }
        
        logger.info(
            f"🔥 HIGH-ATTENTION ENTRY: {symbol} | "
            f"Price: ${entry_price:.6f} | Size: ${size:.2f} | "
            f"Stop: ${stop_price:.6f} ({stop_pct*100:.0f}%) | "
            f"1h: {change_1h:+.1f}% | Vol: ${volume_1h:.0f} | "
            f"Liq: ${liquidity:.0f} | {'WHITELIST' if is_whitelist else 'DISCOVERED'}"
        )
        
        return position
    
    async def scan_watchlist(self) -> List[Dict]:
        """Scan all tokens in watchlist for entry opportunities."""
        opportunities = []
        
        for token_info in sorted(self.watchlist, key=lambda t: t.get('priority', 5)):
            mint = token_info.get('mint')
            if not mint:
                continue
            
            data = await self.api.get_token_data(mint)
            if data:
                data['priority'] = token_info.get('priority', 5)
                data['watchlist_reason'] = token_info.get('reason', 'manual')
                opportunities.append(data)
        
        return opportunities
    
    async def discover_new_tokens(self) -> List[Dict]:
        """Auto-discover high-attention tokens from trending + Pump.fun sources."""
        if not self.config.get('auto_discover', True):
            return []
        
        discovered = []
        
        # 1. PUMP.FUN — EARLIEST POSSIBLE ENTRIES
        try:
            pump_tokens = await self._scan_pumpfun()
            if pump_tokens:
                discovered.extend(pump_tokens)
                logger.info(f"🚀 Pump.fun: {len(pump_tokens)} fresh launches")
        except Exception as e:
            logger.warning(f"Pump.fun scan error: {e}")
        
        # 2. DexScreener NEW pairs (often Pump.fun tokens that just migrated)
        if len(discovered) == 0:  # Only if Pump.fun failed
            try:
                new_pairs = await self._scan_dexscreener_new()
                if new_pairs:
                    discovered.extend(new_pairs)
                    logger.info(f"🔍 DexScreener new: {len(new_pairs)} fresh pairs")
            except Exception as e:
                logger.warning(f"DexScreener new scan error: {e}")
        
        # 3. DexScreener trending
        try:
            trending = await self.api.get_trending_tokens(limit=30)
            for t in trending:
                if any(w['mint'] == t['mint'] for w in self.watchlist):
                    continue
                if t.get('mint'):
                    data = await self.api.get_token_data(t['mint'])
                    if data:
                        discovered.append(data)
        except Exception as e:
            logger.warning(f"DexScreener discovery error: {e}")
        
        return discovered
    
    async def _scan_dexscreener_new(self) -> List[Dict]:
        """
        Scan DexScreener for newly created pairs (alternative to Pump.fun).
        Targets: <1 hour old, low market cap, high volume.
        """
        from datetime import datetime, timezone
        
        new_tokens = []
        
        # DexScreener token profiles latest (new listings)
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data:
                            if item.get('chainId') != 'solana':
                                continue
                            
                            # Get full pair data
                            token_address = item.get('tokenAddress', '')
                            if not token_address:
                                continue
                            
                            # Get token/pair details
                            detail_url = f"https://api.dexscreener.com/tokens/v1/solana/{token_address}"
                            try:
                                async with session.get(detail_url, timeout=10) as dresp:
                                    if dresp.status == 200:
                                        pair_data = await dresp.json()
                                        pairs = pair_data if isinstance(pair_data, list) else pair_data.get('pairs', [])
                                        if pairs:
                                            best = max(pairs, key=lambda p: float(p.get('liquidity', {}).get('usd', 0) or 0))
                                            
                                            # Check age (pairCreatedAt)
                                            created_at = best.get('pairCreatedAt', 0)
                                            if created_at:
                                                now_ms = datetime.now(timezone.utc).timestamp() * 1000
                                                age_min = (now_ms - created_at) / 60000
                                            else:
                                                age_min = 999
                                            
                                            # Only <60 min old
                                            if age_min > 60:
                                                continue
                                            
                                            liq = float(best.get('liquidity', {}).get('usd', 0) or 0)
                                            vol_24h = float(best.get('volume', {}).get('h24', 0) or 0)
                                            mcap = float(best.get('marketCap', 0) or 0)
                                            ch_1h = float(best.get('priceChange', {}).get('h1', 0) or 0)
                                            
                                            # Filters
                                            if liq < 5000 or vol_24h < 1000:
                                                continue
                                            if mcap > 500_000:  # Skip if too big
                                                continue
                                            
                                            token = {
                                                'symbol': best.get('baseToken', {}).get('symbol', 'UNKNOWN'),
                                                'mint': token_address,
                                                'chain': 'solana',
                                                'price_usd': float(best.get('priceUsd', 0) or 0),
                                                'market_cap': mcap,
                                                'liquidity_usd': liq,
                                                'volume_24h': vol_24h,
                                                'volume_1h': float(best.get('volume', {}).get('h1', 0) or 0),
                                                'change_1h': ch_1h,
                                                'change_24h': float(best.get('priceChange', {}).get('h24', 0) or 0),
                                                'holder_count': int(best.get('holders', 0) or 0),
                                                'age_minutes': age_min,
                                                'source': 'dexscreener_new',
                                                'is_pumpfun': False,  # Treat as early micro-cap
                                            }
                                            
                                            new_tokens.append(token)
                                            logger.info(f"🔍 NEW PAIR: {token['symbol']} | Age: {age_min:.0f}m | MC: ${mcap:.0f} | 1h: {ch_1h:.1f}%")
                            except Exception:
                                continue
        except Exception as e:
            logger.warning(f"DexScreener new scan failed: {e}")
        
        return new_tokens
    
    async def _scan_pumpfun(self) -> List[Dict]:
        """
        Scan Pump.fun for the freshest launches.
        Targets: <30 minutes old, early momentum, not yet migrated to Raydium.
        """
        from datetime import datetime, timezone
        
        pump_tokens = []
        
        # Pump.fun API: latest launches
        url = "https://frontend-api.pump.fun/coins?offset=0&limit=50"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        coins = data if isinstance(data, list) else []
                        
                        for coin in coins:
                            # Extract key data
                            mint = coin.get('mint', '')
                            symbol = coin.get('symbol', 'UNKNOWN')
                            name = coin.get('name', '')
                            created = coin.get('created_timestamp', 0)  # Unix timestamp ms
                            
                            if not mint:
                                continue
                            
                            # Calculate age in minutes
                            now_ms = datetime.now(timezone.utc).timestamp() * 1000
                            age_min = (now_ms - created) / 60000 if created else 999
                            
                            # STRICT: Only <30 min old
                            if age_min > 30:
                                continue
                            
                            # Get detailed coin data
                            detail_url = f"https://frontend-api.pump.fun/coins/{mint}"
                            try:
                                async with session.get(detail_url, timeout=10) as dresp:
                                    if dresp.status == 200:
                                        detail = await dresp.json()
                                    else:
                                        detail = coin
                            except:
                                detail = coin
                            
                            # Normalize to our format
                            token = {
                                'symbol': symbol,
                                'mint': mint,
                                'name': name,
                                'chain': 'solana',
                                'price_usd': float(detail.get('usd_market_cap', 0)) / max(float(detail.get('total_supply', 1)), 1),
                                'market_cap': float(detail.get('usd_market_cap', 0)),
                                'liquidity_usd': float(detail.get('usd_market_cap', 0)) * 0.15,  # Estimate 15% of mcap
                                'volume_24h': float(detail.get('volume_24h', 0)),
                                'volume_1h': float(detail.get('volume_24h', 0)) / 24,  # Estimate
                                'change_1h': float(detail.get('price_change_24h', 0)),
                                'change_24h': float(detail.get('price_change_24h', 0)),
                                'holder_count': int(detail.get('holder_count', 0)),
                                'reply_count': int(detail.get('reply_count', 0)),
                                'age_minutes': age_min,
                                'source': 'pumpfun',
                                'is_pumpfun': True,
                                'pumpfun_data': detail,
                            }
                            
                            pump_tokens.append(token)
                            logger.info(f"🚀 PUMP.FUN: {symbol} | Age: {age_min:.0f}m | MC: ${token['market_cap']:.0f}")
        
        except Exception as e:
            logger.warning(f"Pump.fun fetch failed: {e}")
        
        return pump_tokens


# ── Convenience exports ──

_engine: Optional[HighAttentionEngine] = None


async def init_high_attention() -> HighAttentionEngine:
    """Initialize the high-attention engine."""
    global _engine
    _engine = HighAttentionEngine()
    await _engine.initialize()
    return _engine


def get_engine() -> Optional[HighAttentionEngine]:
    """Get the initialized engine."""
    return _engine


async def evaluate_high_attention(token: Dict, balance: float) -> Optional[Dict]:
    """Evaluate a token for high-attention entry."""
    if _engine is None:
        return None
    return _engine.evaluate_entry(token, balance)


async def scan_high_attention() -> List[Dict]:
    """Scan watchlist for opportunities."""
    if _engine is None:
        return []
    return await _engine.scan_watchlist()


async def discover_high_attention() -> List[Dict]:
    """Discover new high-attention tokens."""
    if _engine is None:
        return []
    return await _engine.discover_new_tokens()
