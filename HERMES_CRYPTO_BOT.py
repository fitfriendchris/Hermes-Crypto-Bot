#!/usr/bin/env python3
"""
CRYPTO BOT — Unified Production Runner
Paper trading by default. Set LIVE_MODE=true for real execution.
Author: Hermes | May 2026
"""

import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp
import yaml
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── Optional module imports ──
try:
    from launch_sniper import evaluate_launch, init_launch_sniper
    SNIPER_OK = True
except ImportError as e:
    SNIPER_OK = False
    print(f"[WARN] Launch sniper unavailable: {e}")

try:
    from anti_rug_suite import run_full_rug_check, init_anti_rug
    ANTIRUG_OK = True
except ImportError as e:
    ANTIRUG_OK = False
    print(f"[WARN] Anti-rug suite unavailable: {e}")

try:
    from momentum_scanner import evaluate_momentum, init_momentum_scanner
    MOMENTUM_OK = True
except ImportError as e:
    MOMENTUM_OK = False
    print(f"[WARN] Momentum scanner unavailable: {e}")

try:
    from copy_trader import evaluate_copy_signal, init_copy_trader, scan_whale_wallets
    COPY_OK = True
except ImportError as e:
    COPY_OK = False
    print(f"[WARN] Copy trader unavailable: {e}")

try:
    from HERMES_dex_connector import DEXConnector
    DEX_OK = True
except ImportError as e:
    DEX_OK = False
    print(f"[WARN] DEX connector unavailable: {e}")

# Telegram alerts v2 — direct httpx
TELEGRAM_AVAILABLE = True
try:
    from HERMES_telegram_alerts import TelegramAlertManager as _TAM
except ImportError as _e:
    TELEGRAM_AVAILABLE = False
    print(f"[WARN] Telegram alerts unavailable: {_e}")

try:
    from HERMES_profit_protection import ProfitProtection
    PP_OK = True
except ImportError:
    PP_OK = False

try:
    from HERMES_wallet_integration import WalletManager
    WALLET_OK = True
except ImportError:
    WALLET_OK = False

try:
    from HERMES_swap_executor import SwapManager
    SWAP_OK = True
except ImportError:
    SWAP_OK = False

# ── CONFIG ──
CONFIG_PATH = os.path.join(_HERE, 'config', 'HERMES_CRYPTO_CONFIG.yaml')
with open(CONFIG_PATH, 'r') as f:
    CONFIG = yaml.safe_load(f)

load_dotenv()

LIVE_MODE = os.getenv('LIVE_MODE', 'false').lower() == 'true'
if LIVE_MODE:
    print("⚠️  LIVE MODE ENABLED — REAL TRADES WILL EXECUTE")

# ── LOGGING ──
logging.basicConfig(
    level=getattr(logging, CONFIG['logging']['level'].upper(), logging.INFO),
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_HERE, 'logs', 'HERMES_CRYPTO_BOT.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('CryptoBot')

# ── PERSISTENT STATE ──
STATE_PATH = os.path.join(_HERE, 'state', 'HERMES_CRYPTO_STATE.json')

class BotState:
    """In-memory paper trading state with disk persistence."""

    def __init__(self):
        self.balance = CONFIG['account']['starting_balance_usd']
        self.positions: Dict[str, Dict] = {}
        self.history: List[Dict] = []
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.started_at = datetime.now()
        self.max_balance = self.balance
        self.max_drawdown = 0.0
        self.total_protected = 0.0

    def portfolio_value(self, prices: Dict[str, float]) -> float:
        val = self.balance
        for sym, pos in self.positions.items():
            price = prices.get(sym, pos.get('last_price', pos['entry']))
            val += pos['quantity'] * price
        return val

    def update_drawdown(self, current_value: float):
        if current_value > self.max_balance:
            self.max_balance = current_value
        dd = (self.max_balance - current_value) / self.max_balance if self.max_balance > 0 else 0
        if dd > self.max_drawdown:
            self.max_drawdown = dd

    def load(self):
        if os.path.exists(STATE_PATH):
            try:
                with open(STATE_PATH, 'r') as f:
                    data = json.load(f)
                self.balance = data.get('balance', self.balance)
                self.positions = data.get('positions', {})
                self.history = data.get('history', [])
                self.daily_pnl = data.get('daily_pnl', 0)
                self.trades_today = data.get('trades_today', 0)
                self.consecutive_losses = data.get('consecutive_losses', 0)
                self.max_drawdown = data.get('max_drawdown', 0)
                self.total_protected = data.get('total_protected', 0)
                logger.info(f"State loaded: ${self.balance:.2f} | {len(self.positions)} positions")
            except Exception as e:
                logger.error(f"State load failed: {e}")

    def save(self):
        try:
            data = {
                'balance': self.balance,
                'positions': self.positions,
                'history': self.history[-100:],  # Keep last 100
                'daily_pnl': self.daily_pnl,
                'trades_today': self.trades_today,
                'consecutive_losses': self.consecutive_losses,
                'max_drawdown': self.max_drawdown,
                'total_protected': self.total_protected,
                'timestamp': datetime.now().isoformat(),
            }
            with open(STATE_PATH, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"State save failed: {e}")

state = BotState()
state.load()

# ── MODULE INSTANCES ──
dex = DEXConnector(CONFIG) if DEX_OK else None

alerts = None
if TELEGRAM_AVAILABLE:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
    if token and chat_id:
        alerts = _TAM(token=token, chat_id=chat_id)
    else:
        logger.warning("Telegram credentials missing - alerts disabled")

profit_guard = ProfitProtection(CONFIG.get('profit_protection', {})) if PP_OK else None
wallet = WalletManager() if WALLET_OK else None
swap_manager = SwapManager(wallet) if SWAP_OK and wallet else None
wallet_ready = False

# ── WALLET INIT ──
async def init_wallet():
    global wallet_ready
    if not wallet:
        return

    # Priority: Exodus → Phantom → MetaMask
    key = os.getenv('EXODUS_PRIVATE_KEY') or os.getenv('EXODUS_SEED_PHRASE')
    preferred = 'exodus'
    chain = 'solana'

    if not key:
        key = os.getenv('PHANTOM_PRIVATE_KEY')
        preferred = 'phantom'

    if not key:
        key = os.getenv('METAMASK_PRIVATE_KEY')
        preferred = 'metamask'
        chain = 'ethereum'

    if not key:
        logger.info("No wallet keys in .env — running without wallet (paper only)")
        return

    try:
        wallet_ready = await wallet.initialize(chain=chain, wallet=preferred)
        if wallet_ready:
            bal = await wallet.get_balance()
            logger.info(f"Wallet ready: {preferred} | {wallet.get_address()[:20]}... | {bal:.4f} {chain.upper()}")
            if swap_manager:
                await swap_manager.initialize()
                logger.info("Swap manager initialized (Jupiter + Raydium)")
    except Exception as e:
        logger.warning(f"Wallet init failed: {e}")

# ── STOP LOSS CALCULATION ──
# ── PERSISTENT SYMBOL COOLDOWN & CHURN TRACKING ──
COOLDOWN_FILE = os.path.join(_HERE, 'state', 'symbol_cooldowns.json')
symbol_cooldown: Dict[str, datetime] = {}
symbol_stop_history: Dict[str, List[datetime]] = defaultdict(list)

def _load_cooldowns():
    global symbol_cooldown, symbol_stop_history
    try:
        with open(COOLDOWN_FILE) as f:
            data = json.load(f)
        for sym, ts in data.get('cooldowns', {}).items():
            symbol_cooldown[sym] = datetime.fromisoformat(ts)
        for sym, times in data.get('stop_history', {}).items():
            symbol_stop_history[sym] = [datetime.fromisoformat(t) for t in times]
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def _save_cooldowns():
    os.makedirs(os.path.dirname(COOLDOWN_FILE), exist_ok=True)
    data = {
        'cooldowns': {sym: dt.isoformat() for sym, dt in symbol_cooldown.items()},
        'stop_history': {sym: [t.isoformat() for t in times] for sym, times in symbol_stop_history.items()}
    }
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(data, f, default=str)

def is_symbol_cooled(sym: str) -> bool:
    _load_cooldowns()
    if sym in symbol_cooldown:
        if datetime.now() < symbol_cooldown[sym]:
            return True
        del symbol_cooldown[sym]
        _save_cooldowns()
    return False

def set_symbol_cooldown(sym: str, hours: float = 4.0):
    symbol_cooldown[sym] = datetime.now() + timedelta(hours=hours)
    _save_cooldowns()

# ── SYMBOL LIFETIME TRACKING ──
# Prevent re-trading micro-cap tokens
LIFETIME_FILE = os.path.join(_HERE, 'state', 'symbol_lifetime.json')
symbol_lifetime: Dict[str, Dict] = {}

def _load_lifetime():
    global symbol_lifetime
    try:
        with open(LIFETIME_FILE) as f:
            symbol_lifetime = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        symbol_lifetime = {}

def _save_lifetime():
    os.makedirs(os.path.dirname(LIFETIME_FILE), exist_ok=True)
    with open(LIFETIME_FILE, 'w') as f:
        json.dump(symbol_lifetime, f, default=str)

def get_symbol_lifetime(sym: str) -> Dict:
    _load_lifetime()
    return symbol_lifetime.get(sym, {'trades': 0, 'pnl': 0.0, 'last_trade': None})

def record_symbol_trade(sym: str, pnl: float):
    _load_lifetime()
    if sym not in symbol_lifetime:
        symbol_lifetime[sym] = {'trades': 0, 'pnl': 0.0, 'last_trade': None}
    symbol_lifetime[sym]['trades'] += 1
    symbol_lifetime[sym]['pnl'] += pnl
    symbol_lifetime[sym]['last_trade'] = datetime.now().isoformat()
    _save_lifetime()

def can_trade_symbol(sym: str) -> bool:
    """
    Check if symbol can be traded based on lifetime rules:
    - Permanent blacklist: NEVER trade
    - One-time tokens: max 1 trade
    - Dynamic blacklist: 7-day cooldown after 2 losses
    - Profit cooldown: 72h after profit
    """
    sym_upper = sym.upper()
    
    # 1. Permanent blacklist
    permanent = CONFIG.get('symbol_filter', {}).get('permanent_blacklist', [])
    if sym_upper in [s.upper() for s in permanent]:
        logger.debug(f"🚫 {sym} — PERMANENT BLACKLIST")
        return False
    
    # 2. Check lifetime trades
    lifetime = get_symbol_lifetime(sym_upper)
    max_trades = CONFIG.get('symbol_filter', {}).get('one_time_max_trades', 1)
    
    if lifetime['trades'] >= max_trades:
        # Only allow re-trade if it's a long-term symbol (institutional grade)
        # For now, assume NO micro-cap is long-term
        logger.warning(f"🚫 {sym} — Already traded {lifetime['trades']} times (max: {max_trades})")
        return False
    
    # 3. Profit cooldown (72h)
    if lifetime['last_trade']:
        last = datetime.fromisoformat(lifetime['last_trade'])
        profit_cooldown = CONFIG.get('symbol_filter', {}).get('profit_cooldown_hours', 72)
        if datetime.now() - last < timedelta(hours=profit_cooldown) and lifetime['pnl'] > 0:
            logger.debug(f"🚫 {sym} — Profit cooldown ({profit_cooldown}h)")
            return False
    
    # 4. Dynamic blacklist after 2 losses
    if lifetime['trades'] >= 2 and lifetime['pnl'] < 0:
        ttl = CONFIG.get('symbol_filter', {}).get('dynamic_blacklist_ttl_hours', 168)
        if lifetime['last_trade']:
            last = datetime.fromisoformat(lifetime['last_trade'])
            if datetime.now() - last < timedelta(hours=ttl):
                logger.debug(f"🚫 {sym} — Dynamic blacklist ({ttl}h after 2 losses)")
                return False
    
    return True

def is_symbol_churning(sym: str, window_hours: float = 2.0, max_stops: int = 3) -> bool:
    _load_cooldowns()
    cutoff = datetime.now() - timedelta(hours=window_hours)
    symbol_stop_history[sym] = [t for t in symbol_stop_history[sym] if t > cutoff]
    return len(symbol_stop_history[sym]) >= max_stops

def record_stop_loss(sym: str):
    symbol_stop_history[sym].append(datetime.now())
    _save_cooldowns()

def calc_stop(entry: float, token_data: Dict) -> tuple:
    # Micro-caps need wider stops. Base 25%, never below 20%.
    max_risk = max(CONFIG['stop_loss']['fixed_pct'], 0.25)
    fixed_stop = entry * (1 - max_risk)

    # Volatility-based override — wider stop for volatile micro-caps
    change_24h = token_data.get('priceChange', {}).get('h24', 0)
    if change_24h is not None and abs(change_24h) < 500:  # Allow up to 500% (micro-cap reality)
        vol_stop = entry * (1 - min(abs(change_24h) * 0.015, 0.35))  # Cap at 35% max
        vol_stop = max(entry * 0.65, vol_stop)  # NEVER below 35% of entry
        if vol_stop > fixed_stop:
            return vol_stop, 'volatility'

    return fixed_stop, 'fixed'

# ── ENTRY EVALUATION ──
async def evaluate_entry(token: Dict) -> Optional[Dict]:
    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    price = float(token.get('priceUsd', 0) or token.get('price', 0))

    if price <= 0:
        return None

    # ── CHURN PROTECTION ──
    if is_symbol_churning(sym):
        logger.warning(f"🚫 {sym} blacklisted — 3+ stop-losses in 2h")
        return None
    if is_symbol_cooled(sym):
        return None

    # Safety circuits
    if len(state.positions) >= CONFIG['account']['max_open_positions']:
        return None
    if state.daily_pnl <= -CONFIG['safety']['max_daily_loss_usd']:
        return None
    if state.consecutive_losses >= CONFIG['safety']['max_consecutive_losses']:
        return None
        
    # ── SYMBOL LIFETIME FILTER ──
    # Prevent re-trading micro-cap tokens (one-and-done rule)
    sym_upper = sym.upper()
    if not can_trade_symbol(sym_upper):
        return None

    # Position sizing
    risk_mult = 2.0 if CONFIG['aggressive_mode']['enabled'] else 1.0
    size = state.balance * CONFIG['account']['max_risk_per_trade'] * risk_mult
    size = max(size, CONFIG['account']['min_trade_size_usd'])
    if size > state.balance * 0.95:
        size = state.balance * 0.95

    # Stop loss
    stop, stop_type = calc_stop(price, token)
    risk = (price - stop) / price

    if risk > CONFIG['stop_loss']['fixed_pct']:
        size = size * (CONFIG['stop_loss']['fixed_pct'] / risk)

    quantity = size / price if price > 0 else 0

    return {
        'token': sym.upper(),
        'address': token.get('tokenAddress') or token.get('mint') or token.get('address'),
        'chain': token.get('chainId', 'solana'),
        'entry': price,
        'quantity': quantity,
        'invested': size,
        'stop': stop,
        'stop_type': stop_type,
        'risk_pct': risk,
        'opened_at': datetime.now().isoformat(),
        'tier_exits': {'1': False, '2': False, '3': False, '4': False},
        'highest_price': price,
        'last_price': price,
        'score': token.get('bot_score', 0),
        'flags': token.get('bot_flags', []),
        'pyramid_count': 0,
    }

# ── EXIT CHECK ──
def check_exit(sym: str, price: float) -> Optional[str]:
    pos = state.positions.get(sym)
    if not pos:
        return None

    if price > pos['highest_price']:
        pos['highest_price'] = price
    pos['last_price'] = price

    # Stop loss — ALWAYS check first (safety)
    if price <= pos['stop']:
        return f"stop_loss_{pos['stop_type']}"

    # Time stop
    opened = datetime.fromisoformat(pos['opened_at'])
    hold_hours = CONFIG['stop_loss']['time_stop_hours']
    if datetime.now() - opened > timedelta(hours=hold_hours):
        return "time_stop"

    # R multiple
    risk = pos['entry'] - pos['stop']
    if risk <= 0:
        return None
    r = (price - pos['entry']) / risk
    unrealized_pct = (price - pos['entry']) / pos['entry']

    # ── NEVER LOSE AFTER POSITIVE: Profit Floor System ──
    # Phase 0: Minimum profit lock (once +10% unrealized, floor at +5% profit)
    # CRITICAL FIX: Only lock after 30 minutes to avoid early chop
    hold_minutes = (datetime.now() - opened).total_seconds() / 60
    if hold_minutes >= 30 and unrealized_pct >= 0.10 and pos['stop_type'] not in ('profit_floor', 'breakeven', 'trailing'):
        floor_price = pos['entry'] * 1.05
        if floor_price > pos['stop']:
            pos['stop'] = floor_price
            pos['stop_type'] = 'profit_floor'
            logger.info(f"🔒 {sym} profit floor locked at +5% (${floor_price:.6f}) after {hold_minutes:.0f}m")

    # Phase 1: Breakeven at 2R (only if no tier has been hit yet)
    if r >= 2.0 and pos['stop_type'] not in ('breakeven', 'trailing', 'profit_floor') and not any(pos['tier_exits'].values()):
        be_price = pos['entry'] * 1.005
        if be_price > pos['stop']:
            pos['stop'] = be_price
            pos['stop_type'] = 'breakeven'
            logger.info(f"🛡️ {sym} moved to breakeven (${be_price:.6f})")

    # Phase 2: Trailing profit stop (once +50% unrealized, trail at 50% of peak gains)
    if unrealized_pct >= 0.50:
        trail_pct = 0.50  # allow 50% retracement of gains
        trail_price = pos['highest_price'] * (1 - trail_pct * unrealized_pct)
        min_trail = pos['entry'] * 1.10  # never below +10%
        trail_price = max(trail_price, min_trail)
        if trail_price > pos['stop']:
            pos['stop'] = trail_price
            pos['stop_type'] = 'trailing_profit'
            logger.info(f"🎯 {sym} trailing profit stop @ ${trail_price:.6f}")

    # Tiered exits — sequential, once each
    tiers = CONFIG['take_profit']
    if not pos['tier_exits']['1'] and r >= tiers['tier_1_r']:
        pos['tier_exits']['1'] = True
        return f"tier_1_{tiers['tier_1_r']}R"
    if not pos['tier_exits']['2'] and r >= tiers['tier_2_r']:
        pos['tier_exits']['2'] = True
        return f"tier_2_{tiers['tier_2_r']}R"
    if not pos['tier_exits']['3'] and r >= tiers['tier_3_r']:
        pos['tier_exits']['3'] = True
        return f"tier_3_{tiers['tier_3_r']}R"
    if not pos['tier_exits']['4'] and r >= tiers['tier_4_r']:
        pos['tier_exits']['4'] = True
        return f"tier_4_{tiers['tier_4_r']}R"

    # Trailing stop after all tiers hit
    if pos['tier_exits']['4']:
        trail = pos['highest_price'] * (1 - tiers['final_trail_pct'])
        if price <= trail:
            return "trailing_stop"

    return None

# ── PAPER TRADE EXECUTION ──
async def paper_buy(position: Dict):
    sym = position['token']
    invested = position['invested']

    if invested > state.balance:
        invested = state.balance * 0.95
        position['invested'] = invested
        position['quantity'] = invested / position['entry'] if position['entry'] > 0 else 0

    state.balance -= invested
    state.positions[sym] = position
    state.trades_today += 1

    logger.info(f"PAPER BUY  {sym}: ${invested:.2f} @ ${position['entry']:.6f} | qty={position['quantity']:.4f}")

    # ── Telegram alerts ──
    if alerts:
        try:
            await alerts.send_position_opened(position)
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── True-balance snapshot after every trade ──
    await _send_trade_snapshot()

async def paper_sell(sym: str, price: float, reason: str):
    pos = state.positions.get(sym)
    if not pos:
        return

    # Determine exit portion based on tier
    tier_portions = {
        'tier_1': 0.25,
        'tier_2': 0.25,
        'tier_3': 0.25,
        'tier_4': 0.15,
    }
    portion = 1.0
    for tier_key, tier_pct in tier_portions.items():
        if tier_key in reason:
            portion = tier_pct
            break

    sell_qty = pos['quantity'] * portion
    proceeds = sell_qty * price
    cost_basis = pos['invested'] * portion
    pnl = proceeds - cost_basis

    state.balance += proceeds
    state.daily_pnl += pnl

    if pnl < 0:
        state.consecutive_losses += 1
        # ── COOLDOWN + CHURN TRACKING ──
        record_stop_loss(sym)
        set_symbol_cooldown(sym, hours=4.0)
        logger.warning(f"🚫 {sym} cooldown 4h — stop-loss at {pnl/cost_basis:+.2%}")
    else:
        state.consecutive_losses = 0
        if profit_guard:
            profit_guard.add_profit(pnl)

    result = {
        'token': sym,
        'entry': pos['entry'],
        'exit': price,
        'invested': cost_basis,
        'proceeds': proceeds,
        'pnl': pnl,
        'pnl_pct': pnl / cost_basis if cost_basis > 0 else 0,
        'reason': reason,
        'hold_time_hours': round((datetime.now() - datetime.fromisoformat(pos['opened_at'])).total_seconds() / 3600, 1),
        'highest_price': pos['highest_price'],
        'portion': portion,
    }
    state.history.append(result)

    # Reduce position or close fully
    if portion >= 0.99:
        del state.positions[sym]
        logger.info(f"PAPER SELL {sym}: FULL ${pnl:+.2f} ({pnl/cost_basis:+.2%}) | {reason}")
    else:
        pos['quantity'] -= sell_qty
        pos['invested'] -= cost_basis
        logger.info(f"PAPER SELL {sym}: PARTIAL {portion:.0%} ${pnl:+.2f} ({pnl/cost_basis:+.2%}) | {reason} | remaining qty={pos['quantity']:.4f}")

    # ── RECORD SYMBOL TRADE FOR LIFETIME TRACKING ──
    record_symbol_trade(sym, pnl)

    if alerts:
        try:
            await alerts.send_position_closed({
                'token': sym, 'pnl_pct': result['pnl_pct'], 'pnl_usd': pnl, 'reason': reason, 'portion': portion
            })
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── True-balance snapshot after every trade ──
    await _send_trade_snapshot()

async def _send_trade_snapshot():
    """Build and send a full portfolio snapshot to Telegram."""
    if not alerts:
        return
    try:
        pos_count = len(state.positions)
        unrealized = 0.0
        for sym, pos in state.positions.items():
            entry = pos.get('entry', 0)
            last = pos.get('last_price', entry)
            invested = pos.get('invested', 0)
            if entry > 0:
                unrealized += invested * ((last - entry) / entry)

        total = state.balance + unrealized
        start = float(CONFIG['account'].get('starting_balance_usd', 100.0))
        total_return = (total - start) / start if start > 0 else 0

        snap = {
            'balance': state.balance,
            'total_value': total,
            'starting_balance': start,
            'unrealized_pnl_usd': unrealized,
            'daily_pnl': state.daily_pnl,
            'max_drawdown': state.max_drawdown,
            'consecutive_losses': state.consecutive_losses,
            'positions': state.positions,
            'history': state.history,
        }
        await alerts.send_trade_summary(snap)
    except Exception as e:
        logger.warning(f"Trade snapshot failed: {e}")

# ── LIVE TRADE PLACEHOLDERS ──
async def live_buy(position: Dict):
    if not wallet_ready:
        logger.error("Wallet not ready for live trading")
        return
    logger.warning(f"LIVE BUY {position['token']} — DEX execution not yet implemented")

async def live_sell(sym: str, price: float, reason: str):
    if not wallet_ready:
        return
    logger.warning(f"LIVE SELL {sym} — DEX execution not yet implemented")

# ── PRICE LOOKUP ──
async def get_position_price(symbol: str, pos: Dict) -> Optional[float]:
    """Get current price for a position."""
    if not dex:
        return pos.get('last_price', pos['entry'])

    # Try DexScreener token endpoint (not pair endpoint)
    addr = pos.get('address')
    chain = pos.get('chain', 'solana')
    if addr:
        try:
            price = await dex.get_token_price(symbol, addr)
            if price and price > 0:
                pos['last_price'] = price  # update cached price
                return price
        except Exception:
            pass

    # Fallback to search by symbol
    try:
        results = await dex.search_token(symbol)
        if results:
            p = float(results[0].get('priceUsd', 0))
            if p > 0:
                pos['last_price'] = p
                return p
    except Exception:
        pass

    # FINAL fallback: use cached last price (NEVER decay it)
    return pos.get('last_price', pos['entry'])

# ── MAIN LOOPS ──

paused = False

async def discovery_loop():
    """Scan DEXs for micro-cap opportunities and enter positions."""
    global paused
    while True:
        try:
            if paused:
                await asyncio.sleep(60)
                continue

            if not dex:
                logger.warning("DEX unavailable — skipping discovery")
                await asyncio.sleep(60)
                continue

            # ── MOMENTUM SCAN ──
            logger.info("🔍 Scanning DEX for opportunities...")
            tokens = await dex.discover_tokens("mixed", limit=50)  # Get more for filtering
            logger.info(f"Found {len(tokens)} eligible tokens")

            for token in tokens[:10]:  # Check top 10
                sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')

                if sym in state.positions:
                    continue

                # ── MOMENTUM FILTER ──
                if MOMENTUM_OK:
                    enhanced = await evaluate_momentum(token)
                    if not enhanced:
                        continue
                    token = enhanced
                    logger.info(f"📈 MOMENTUM: {sym} | Score: {token.get('momentum_score', 0)}")

                pos = await evaluate_entry(token)
                if pos:
                    # Tag source
                    pos['source'] = 'momentum' if MOMENTUM_OK else 'scanner'
                    pos['momentum_score'] = token.get('momentum_score', 0)

                    if LIVE_MODE and wallet_ready:
                        await live_buy(pos)
                    else:
                        await paper_buy(pos)

                    if alerts:
                        try:
                            await alerts.send_info(
                                f"🔍 DEX Discovery: {sym}\n"
                                f"Entry: ${pos['entry']:.6f} | Size: ${pos['invested']:.2f} | Score: {pos.get('score', 0)}"
                            )
                        except Exception as e:
                            logger.warning(f"Telegram alert failed: {e}")

            # ── COPY TRADER SCAN ──
            if COPY_OK:
                try:
                    signals = await scan_whale_wallets()
                    for signal in signals:
                        from copy_trader import evaluate_copy_signal
                        result = await evaluate_copy_signal(signal, state.balance)
                        if result:
                            sym = result['token']
                            if sym in state.positions:
                                continue

                            # Build position dict
                            pos = await evaluate_entry({'symbol': sym, 'priceUsd': result['entry']})
                            if pos:
                                pos['invested'] = result['invested']
                                pos['quantity'] = result['quantity']
                                pos['source'] = 'copy_trader'
                                pos['wallet_id'] = result.get('wallet_id')
                                pos['wallet_name'] = result.get('wallet_name')

                                if LIVE_MODE and wallet_ready:
                                    await live_buy(pos)
                                else:
                                    await paper_buy(pos)

                                logger.info(
                                    f"🐋 COPY: {sym} via {result.get('wallet_name', 'unknown')} "
                                    f"| Size: ${result['invested']:.2f} ({result.get('our_allocation', 0)*100:.1f}% of portfolio)"
                                )
                except Exception as e:
                    logger.error(f"Copy trader scan error: {e}")

            # ── LAUNCH SNIPER ──
            if SNIPER_OK:
                try:
                    from launch_sniper import evaluate_launch
                    snipe_result = await evaluate_launch({'symbol': 'SCAN', 'address': ''})
                    # Actually we need to pass the token from the sniper
                    # For now, let the sniper loop handle it separately
                    pass
                except Exception as e:
                    logger.error(f"Launch sniper error: {e}")

        except Exception as e:
            logger.error(f"Discovery error: {e}")

        await asyncio.sleep(60)

async def sniper_loop():
    """Dedicated sniper loop for launch monitoring."""
    global paused
    while True:
        try:
            if paused:
                await asyncio.sleep(60)
                continue
            
            if not SNIPER_OK:
                await asyncio.sleep(300)
                continue
            
            from launch_sniper import evaluate_launch
            
            # Check for new launches
            async with aiohttp.ClientSession() as session:
                from launch_sniper import fetch_pumpfun_launches, fetch_raydium_new_pools
                
                pump_launches = await fetch_pumpfun_launches(session)
                raydium_launches = await fetch_raydium_new_pools(session)
                
                all_launches = pump_launches + raydium_launches
                
                for launch in all_launches[:5]:
                    # Anti-rug check first
                    addr = launch.get('mint') or launch.get('address')
                    if addr and ANTIRUG_OK:
                        from anti_rug_suite import run_full_rug_check
                        rug_result = await run_full_rug_check(addr)
                        if not rug_result['safe']:
                            logger.warning(f"🚫 Launch blocked by anti-rug: {rug_result['flags']}")
                            continue
                    
                    # Evaluate snipe
                    result = await evaluate_launch(launch)
                    if result:
                        sym = result['token']
                        if sym in state.positions:
                            continue
                        
                        # Calculate position size
                        size = state.balance * SNIPER_CONFIG.get('position_size_pct', 0.05)
                        size = min(size, state.balance * 0.95)
                        result['invested'] = size
                        result['quantity'] = size / result['entry'] if result['entry'] > 0 else 0
                        
                        # Set auto-exit at 2R
                        result['tier_exits'] = {'1': False}
                        result['take_profit_r'] = SNIPER_CONFIG.get('auto_sell_r', 2.0)
                        
                        if LIVE_MODE and wallet_ready:
                            await live_buy(result)
                        else:
                            await paper_buy(result)
                        
                        logger.info(
                            f"🎯 SNIPED: {sym} @ ${result['entry']:.6f} | "
                            f"Size: ${size:.2f} | Target: {result['take_profit_r']}R"
                        )
                        
                        if alerts:
                            try:
                                await alerts.send_info(
                                    f"🎯 SNIPED: {sym}\n"
                                    f"Entry: ${result['entry']:.6f}\n"
                                    f"Size: ${size:.2f}\n"
                                    f"Target: {result['take_profit_r']}R\n"
                                    f"Stop: {result['risk_pct']*100:.0f}%"
                                )
                            except Exception as e:
                                logger.warning(f"Telegram alert failed: {e}")
                        
                        return  # One snipe per cycle
                        
        except Exception as e:
            logger.error(f"Sniper loop error: {e}")
        
        await asyncio.sleep(10)  # Check every 10 seconds

async def monitor_loop():
    """Monitor open positions for exits."""
    global paused
    while True:
        try:
            for sym, pos in list(state.positions.items()):
                price = await get_position_price(sym, pos)
                if not price:
                    continue

                reason = check_exit(sym, price)
                if reason:
                    if LIVE_MODE and wallet_ready:
                        await live_sell(sym, price, reason)
                    else:
                        await paper_sell(sym, price, reason)

        except Exception as e:
            logger.error(f"Monitor error: {e}")

        await asyncio.sleep(10)

async def report_loop():
    """Periodic portfolio reports."""
    interval = CONFIG['logging'].get('pnl_update_interval', 300)
    while True:
        await asyncio.sleep(interval)

        try:
            prices = {sym: pos['last_price'] for sym, pos in state.positions.items()}
            val = state.portfolio_value(prices)
            state.update_drawdown(val)

            # Max drawdown circuit breaker
            if state.max_drawdown > CONFIG['account']['max_drawdown_pct']:
                logger.warning(f"🚨 MAX DRAWDOWN HIT: {state.max_drawdown:.1%} — pausing new entries")
                global paused
                paused = True

            logger.info(
                f"📊 Portfolio: ${val:.2f} | Cash: ${state.balance:.2f} | "
                f"Positions: {len(state.positions)} | DD: {state.max_drawdown:.1%} | "
                f"Daily PnL: ${state.daily_pnl:+.2f}"
            )

        except Exception as e:
            logger.error(f"Report error: {e}")

async def save_loop():
    """Persist state every 60 seconds."""
    while True:
        await asyncio.sleep(60)
        state.save()

async def daily_report_loop():
    """Send daily summary at midnight."""
    while True:
        now = datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        await asyncio.sleep((next_midnight - now).total_seconds())

        try:
            prices = {sym: pos['last_price'] for sym, pos in state.positions.items()}
            val = state.portfolio_value(prices)
            wins = sum(1 for t in state.history if t['pnl'] > 0)
            total = len(state.history)
            win_rate = wins / total if total > 0 else 0

            if alerts:
                await alerts.send_daily_report(val, state.daily_pnl, state.trades_today, len(state.positions), win_rate)

            logger.info(f"📈 DAILY: PnL ${state.daily_pnl:+.2f} | Trades {state.trades_today} | WinRate {win_rate:.0%}")

            # Reset daily counters
            state.daily_pnl = 0.0
            state.trades_today = 0

        except Exception as e:
            logger.error(f"Daily report error: {e}")

# ── MAIN ──
async def main():
    logger.info("=" * 50)
    logger.info(f"🚀 CRYPTO BOT v2.0 | Mode: {'LIVE' if LIVE_MODE else 'PAPER'}")
    logger.info(f"💰 Capital: ${state.balance:.2f}")
    logger.info(f"📡 DEX: {'OK' if DEX_OK else 'MISSING'}")
    logger.info(f"📨 Telegram: {'OK' if TELEGRAM_AVAILABLE else 'MISSING'}")
    logger.info(f"🔒 Profit Guard: {'OK' if PP_OK else 'MISSING'}")
    logger.info(f"👛 Wallet: {'OK' if WALLET_OK else 'MISSING'}")
    logger.info("=" * 50)

    if dex:
        await dex.initialize()

    # Initialize anti-rug suite
    if ANTIRUG_OK:
        await init_anti_rug()
        logger.info("🛡️ Anti-rug suite: ACTIVE")

    # Initialize launch sniper
    if SNIPER_OK:
        await init_launch_sniper()
        logger.info("🎯 Launch sniper: ACTIVE")

    # Initialize momentum scanner
    if MOMENTUM_OK:
        await init_momentum_scanner()
        logger.info("📈 Momentum scanner: ACTIVE")

    # Initialize copy trader
    if COPY_OK:
        await init_copy_trader()
        logger.info("🐋 Copy trader: ACTIVE")

    await init_wallet()

    if alerts:
        try:
            await alerts.send_startup()
        except Exception as e:
            logger.warning(f"Startup alert failed: {e}")

    tasks = [
        asyncio.create_task(discovery_loop()),
        asyncio.create_task(sniper_loop()),
        asyncio.create_task(monitor_loop()),
        asyncio.create_task(report_loop()),
        asyncio.create_task(save_loop()),
        asyncio.create_task(daily_report_loop()),
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        state.save()
        if dex:
            await dex.close()
        logger.info("Bot stopped. State saved.")

if __name__ == "__main__":
    asyncio.run(main())
