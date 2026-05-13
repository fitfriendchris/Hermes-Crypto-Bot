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
    from dex_connector import DEXConnector
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

try:
    from HERMES_profit_sweeper import ProfitSweeper
    SWEEPER_OK = True
except ImportError:
    SWEEPER_OK = False

# ── CONFIG ──
CONFIG_PATH = os.path.join(_HERE, 'config', 'HERMES_CRYPTO_CONFIG.yaml')
with open(CONFIG_PATH, 'r') as f:
    CONFIG = yaml.safe_load(f)

load_dotenv()

LIVE_MODE = os.getenv('LIVE_MODE', 'false').lower() == 'true'
if LIVE_MODE:
    print("⚠️  LIVE MODE ENABLED — REAL TRADES WILL EXECUTE")

SNIPER_CONFIG = CONFIG.get('sniper', {'position_size_pct': 0.05, 'auto_sell_r': 2.0})

# ── LOGGING ──
log_file = os.path.join(_HERE, 'logs', 'HERMES_CRYPTO_BOT.log')
log_format = '%(asctime)s | %(levelname)s | %(message)s'

# Create logger with explicit handlers (prevent duplicate root logging)
logger = logging.getLogger('CryptoBot')
logger.setLevel(getattr(logging, CONFIG['logging']['level'].upper(), logging.INFO))
logger.propagate = False  # Don't send to root logger

# Clear any existing handlers to prevent duplicates
logger.handlers = []

# Add file handler only (console output causes duplicates with nohup)
file_handler = logging.FileHandler(log_file)
file_handler.setFormatter(logging.Formatter(log_format))
logger.addHandler(file_handler)

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

_pp_cfg = CONFIG.get('profit_protection', {})
_pp_cfg['cold_wallet_sol'] = os.getenv('COLD_WALLET_SOL', '') or _pp_cfg.get('cold_wallet_sol', '')
_pp_cfg['cold_wallet_eth'] = os.getenv('COLD_WALLET_ETH', '') or _pp_cfg.get('cold_wallet_eth', '')
profit_guard = ProfitProtection(_pp_cfg) if PP_OK else None
wallet = WalletManager() if WALLET_OK else None
swap_manager = SwapManager(wallet) if SWAP_OK and wallet else None
sweeper = ProfitSweeper(wallet, swap_manager) if SWEEPER_OK else None
wallet_ready = False

# ── WALLET INIT ──
async def init_wallet():
    global wallet_ready
    if not wallet:
        return

    # Priority: Exodus → Phantom → MetaMask
    exodus_key  = (os.getenv('EXODUS_PRIVATE_KEY') or '').strip()
    phantom_key = (os.getenv('PHANTOM_PRIVATE_KEY') or '').strip()

    if exodus_key and phantom_key:
        # Both configured — use whichever has more SOL (checked below)
        key, preferred = exodus_key, 'exodus'
    elif exodus_key:
        key, preferred = exodus_key, 'exodus'
    elif phantom_key:
        key, preferred = phantom_key, 'phantom'
    else:
        key = (os.getenv('METAMASK_PRIVATE_KEY') or '').strip()
        preferred = 'metamask'

    chain = 'ethereum' if preferred == 'metamask' else 'solana'

    if not key:
        logger.info("No wallet keys in .env — running without wallet (paper only)")
        return

    try:
        wallet_ready = await wallet.initialize(chain=chain, wallet=preferred)
        if wallet_ready:
            sol_balance = await wallet.get_balance()
            logger.info(f"Wallet ready: {preferred} | {wallet.get_address()[:20]}... | {sol_balance:.4f} {chain.upper()}")

            if swap_manager:
                await swap_manager.initialize()
                logger.info("Swap manager initialized (Jupiter + Raydium)")

            # ── Live balance sync: replace paper-inflated balance with real wallet value ──
            if LIVE_MODE and swap_manager:
                try:
                    sol_price   = await swap_manager.get_sol_price()
                    if sol_price <= 0:
                        logger.warning("Live sync skipped — SOL price unavailable")
                        raise ValueError("sol_price is 0")
                    wallet_usd  = sol_balance * sol_price
                    old_balance = state.balance

                    if wallet_usd < old_balance * 0.5:
                        # Paper balance far exceeds real wallet — this is a paper→live transition
                        state.balance = wallet_usd

                        # Drop paper positions that are on chains the hot wallet can't execute
                        eth_pos = {k: v for k, v in state.positions.items()
                                   if v.get('chain', 'solana') != 'solana'}
                        for sym in list(eth_pos.keys()):
                            del state.positions[sym]
                        state.save()

                        logger.info(
                            f"LIVE SYNC: balance ${old_balance:.2f} → ${wallet_usd:.2f} "
                            f"({sol_balance:.4f} SOL @ ${sol_price:.2f})"
                        )
                        if eth_pos:
                            logger.info(f"Cleared {len(eth_pos)} paper ETH positions: {list(eth_pos.keys())}")
                    else:
                        logger.info(f"Balance already near wallet value (${old_balance:.2f} vs ${wallet_usd:.2f} real) — no sync needed")
                except Exception as e:
                    logger.warning(f"Live balance sync failed: {e}")
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
    """
    Tighter stop for sharper cut-losses.
    Fixed: 15% max (was 25%). Volatility-adjusted: scale with 24h move but cap at 20%.
    Tighter stops = smaller losses, higher R multiples on winners.
    """
    fixed_pct  = 0.15                      # 15% hard fixed stop
    fixed_stop = entry * (1 - fixed_pct)

    change_24h = token_data.get('priceChange', {}).get('h24', 0) or 0
    if abs(change_24h) < 300:
        # Scale stop with volatility: 0.8% of 24h move, capped at 20%
        vol_pct  = min(abs(float(change_24h)) * 0.008, 0.20)
        vol_stop = entry * (1 - max(vol_pct, 0.10))   # never tighter than 10%
        if vol_stop > fixed_stop:
            return vol_stop, 'volatility'

    return fixed_stop, 'fixed'

# ── SOCIAL / RUG QUALITY GATE ──
# Tokens that have ZERO community presence AND zero social links are skipped
# unless they have very high liquidity ($100K+) as a substitute signal.
# This catches the garbage that passes volume/momentum but has no real community.

def _passes_quality_gate(token: Dict) -> bool:
    """
    Community and rug quality gate.
    A token needs at least ONE of:
      - Website URL set
      - Twitter/X URL set
      - Telegram URL set
      - Liquidity ≥ $100K (whales validate it with capital instead)
    Anything with NONE of these is a ghost token — skip.
    """
    info = token.get('info', {}) or {}
    socials = info.get('socials', []) or []
    websites = info.get('websites', []) or []
    liq = float(token.get('liquidity', {}).get('usd', 0))

    has_website  = bool(websites)
    has_social   = bool(socials)
    has_liquidity = liq >= 100_000

    if not has_website and not has_social and not has_liquidity:
        sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
        logger.debug(f"🚫 {sym} — no website, no socials, no big liquidity (ghost token)")
        return False
    return True


# ── ENTRY EVALUATION ──
async def evaluate_entry(token: Dict) -> Optional[Dict]:
    sym   = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    price = float(token.get('priceUsd', 0) or token.get('price', 0))

    if price <= 0:
        return None

    # ── COMMUNITY / RUG QUALITY GATE ──
    if not _passes_quality_gate(token):
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
    sym_upper = sym.upper()
    if not can_trade_symbol(sym_upper):
        return None

    # ── POSITION SIZING ──
    # Reduced multiplier (1.5× instead of 2×) — less size per trade, more staying power
    risk_mult = 1.5 if CONFIG['aggressive_mode']['enabled'] else 1.0
    size = state.balance * CONFIG['account']['max_risk_per_trade'] * risk_mult
    size = max(size, CONFIG['account']['min_trade_size_usd'])
    if size > state.balance * 0.20:        # never more than 20% of balance per trade
        size = state.balance * 0.20

    # ── STOP LOSS ──
    stop, stop_type = calc_stop(price, token)
    risk = (price - stop) / price
    if risk > 0.20:                        # hard cap: never risk more than 20% of entry
        size = size * (0.20 / risk)

    quantity = size / price if price > 0 else 0

    return {
        'token':        sym.upper(),
        'address':      token.get('tokenAddress') or token.get('mint') or token.get('address'),
        'chain':        token.get('chainId', 'solana'),
        'entry':        price,
        'quantity':     quantity,
        'invested':     size,
        'stop':         stop,
        'stop_type':    stop_type,
        'risk_pct':     risk,
        'opened_at':    datetime.now().isoformat(),
        'tier_exits':   {'1': False, '2': False, '3': False, '4': False},
        'highest_price': price,
        'last_price':   price,
        'score':        token.get('bot_score', token.get('momentum_score', 0)),
        'flags':        token.get('bot_flags', []),
        'pyramid_count': 0,
        'has_socials':  bool(token.get('info', {}).get('socials')),
        'has_website':  bool(token.get('info', {}).get('websites')),
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

    tp         = CONFIG['take_profit']
    exit1_pct  = float(tp.get('exit_1_pct', 0.20))
    trail_pct  = float(tp.get('trail_pct',  0.10))   # 10% trail (tighter than old 12%)

    # ── Exit 1: sell 50% at +20%, lock in profit and move stop to BE+2% ──
    if not pos['tier_exits']['1'] and unrealized_pct >= exit1_pct:
        pos['tier_exits']['1'] = True
        be_price = pos['entry'] * 1.02
        if be_price > pos['stop']:
            pos['stop']      = be_price
            pos['stop_type'] = 'breakeven'
            logger.info(f"🔒 {sym} stop → BE+2% (${be_price:.6f}) after Exit 1")
        return "tier_1_exit1"

    # ── Progressive trailing stop after Exit 1 ──
    # Tightens as gains grow — protects more of the run the higher it goes
    if pos['tier_exits']['1']:
        if unrealized_pct >= 1.0:
            effective_trail = 0.06      # +100%+: trail 6% — lock almost all gains
        elif unrealized_pct >= 0.50:
            effective_trail = 0.08      # +50%-100%: trail 8%
        else:
            effective_trail = trail_pct  # +20%-50%: trail 10%

        trail_price = pos['highest_price'] * (1 - effective_trail)
        floor       = pos['entry'] * 1.02
        trail_price = max(trail_price, floor)
        if trail_price > pos['stop']:
            pos['stop']      = trail_price
            pos['stop_type'] = 'trailing'
            logger.debug(f"📈 {sym} trail → ${trail_price:.6f} ({effective_trail*100:.0f}% from peak)")

    # ── Profit floor: lock +5% after 30m of being +10% up ──
    if hold_minutes >= 30 and unrealized_pct >= 0.10 and pos['stop_type'] not in ('profit_floor', 'breakeven', 'trailing'):
        floor_price = pos['entry'] * 1.05
        if floor_price > pos['stop']:
            pos['stop']      = floor_price
            pos['stop_type'] = 'profit_floor'
            logger.info(f"🛡️ {sym} profit floor +5% (${floor_price:.6f})")

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

    # Exit 1 → sell 50%, everything else → close full remaining position
    pyramid_this = False
    if 'tier_1_exit1' in reason:
        portion      = 0.50
        pyramid_this = (pos.get('pyramid_count', 0) == 0)  # only pyramid once
    else:
        portion = 1.0

    sell_qty = pos['quantity'] * portion
    proceeds = sell_qty * price
    cost_basis = pos['invested'] * portion
    pnl = proceeds - cost_basis

    state.balance += proceeds
    state.daily_pnl += pnl

    if pnl < 0:
        state.consecutive_losses += 1
        record_stop_loss(sym)
        _block_reentry(sym)            # immediate 15-min block on any loss
        set_symbol_cooldown(sym, hours=4.0)
        logger.warning(f"🚫 {sym} cooldown 4h — stop-loss at {pnl/cost_basis:+.2%}")
    else:
        state.consecutive_losses = 0
        _block_reentry(sym)            # also block re-entry after wins (let it breathe)
        if profit_guard:
            profit_guard.add_profit(pnl)

    # ── PROFIT SWEEPER ──
    if sweeper and pnl > 0:
        sweeper.credit_pnl(pnl)
        # Trigger immediate sweep check (async safe since paper_sell is async)
        try:
            await sweeper.check_and_sweep()
        except Exception as e:
            logger.warning(f"Sweeper check failed: {e}")

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

    # ── PYRAMID: add to confirmed winner after Exit 1 ──
    # Only fires when: Exit 1 hit (+20% proven), first pyramid, enough balance
    if pyramid_this and sym in state.positions:
        pyr_size = min(
            state.balance * CONFIG['account']['max_risk_per_trade'] * 1.5,
            state.balance * 0.10,
        )
        if pyr_size >= CONFIG['account']['min_trade_size_usd'] and state.balance > pyr_size * 1.5:
            live_pos = state.positions[sym]
            pyr_qty  = pyr_size / price if price > 0 else 0
            state.balance         -= pyr_size
            live_pos['quantity']  += pyr_qty
            live_pos['invested']  += pyr_size
            live_pos['pyramid_count'] = live_pos.get('pyramid_count', 0) + 1
            logger.info(f"🔺 PYRAMID {sym}: +${pyr_size:.2f} @ ${price:.6f} — riding confirmed winner")
            if alerts:
                try:
                    await alerts.send_info(
                        f"🔺 PYRAMID {sym}\n"
                        f"Added ${pyr_size:.2f} @ ${price:.6f} after Exit 1 confirmed\n"
                        f"Total exposure: ${live_pos['invested']:.2f}"
                    )
                except Exception:
                    pass

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

# ── LIVE TRADE EXECUTION (Jupiter v6 — Solana only) ──
async def live_buy(position: Dict):
    """Buy token on-chain via Jupiter. Falls back to paper on non-SOL chains."""
    if not wallet_ready or not swap_manager:
        logger.error("Live buy blocked — wallet or swap manager not ready")
        return

    chain  = position.get('chain', 'solana')
    sym    = position['token']
    addr   = position.get('address', '')

    if chain != 'solana' or not addr:
        logger.info(f"LIVE BUY {sym} on {chain} — Solana-only; executing as paper")
        await paper_buy(position)
        return

    invested_usd = position['invested']
    lamports = await swap_manager.usd_to_lamports(invested_usd)
    if lamports < 100_000:
        logger.warning(f"LIVE BUY {sym} — too small ({lamports} lamports); skipping")
        return

    from HERMES_SWAP_EXECUTOR import SwapManager as _SM
    result = await swap_manager.execute_swap(
        input_mint=_SM.SOL_MINT,
        output_mint=addr,
        amount_in=lamports,
        slippage_bps=300,
    )

    if result.success:
        sol_price = await swap_manager.get_sol_price()
        actual_usd = (lamports / 1e9) * sol_price
        position['invested']  = actual_usd
        position['quantity']  = result.output_amount
        position['entry']     = actual_usd / result.output_amount if result.output_amount > 0 else position['entry']
        position['tx_buy']    = result.tx_signature

        state.balance -= actual_usd
        state.positions[sym] = position
        state.trades_today   += 1
        logger.info(
            f"LIVE BUY {sym}: ${actual_usd:.2f} @ {position['entry']:.6f} "
            f"| qty={result.output_amount:.4f} | Tx: {result.tx_signature[:20]}..."
        )
        if alerts:
            try:
                await alerts.send_position_opened(position)
            except Exception as e:
                logger.warning(f"Alert failed: {e}")
        await _send_trade_snapshot()
    else:
        logger.error(f"LIVE BUY {sym} FAILED: {result.error} — falling back to paper")
        await paper_buy(position)


async def live_sell(sym: str, price: float, reason: str):
    """Sell token on-chain via Jupiter. Falls back to paper on non-SOL chains."""
    if not wallet_ready or not swap_manager:
        return

    pos   = state.positions.get(sym)
    if not pos:
        return

    chain = pos.get('chain', 'solana')
    addr  = pos.get('address', '')

    if chain != 'solana' or not addr:
        logger.info(f"LIVE SELL {sym} on {chain} — Solana-only; executing as paper")
        await paper_sell(sym, price, reason)
        return

    portion = 0.50 if 'tier_1_exit1' in reason else 1.0

    sell_qty      = pos['quantity'] * portion
    token_decimals = pos.get('decimals', 6)      # Pump.fun tokens default to 6
    raw_amount    = int(sell_qty * (10 ** token_decimals))

    if raw_amount < 1:
        logger.warning(f"LIVE SELL {sym} — sell amount rounds to 0 raw units; skipping")
        return

    from HERMES_SWAP_EXECUTOR import SwapManager as _SM
    result = await swap_manager.execute_swap(
        input_mint=addr,
        output_mint=_SM.SOL_MINT,
        amount_in=raw_amount,
        slippage_bps=300,
    )

    if result.success:
        sol_price   = await swap_manager.get_sol_price()
        proceeds    = result.output_amount * sol_price
        cost_basis  = pos['invested'] * portion
        pnl         = proceeds - cost_basis

        state.balance    += proceeds
        state.daily_pnl  += pnl

        if pnl < 0:
            state.consecutive_losses += 1
            record_stop_loss(sym)
            _block_reentry(sym)
            set_symbol_cooldown(sym, hours=4.0)
        else:
            state.consecutive_losses = 0
            _block_reentry(sym)
            if profit_guard:
                profit_guard.add_profit(pnl)

        if sweeper and pnl > 0:
            sweeper.credit_pnl(pnl)
            try:
                await sweeper.check_and_sweep()
            except Exception as e:
                logger.warning(f"Sweeper check failed: {e}")

        rec = {
            'token':           sym,
            'entry':           pos['entry'],
            'exit':            price,
            'invested':        cost_basis,
            'proceeds':        proceeds,
            'pnl':             pnl,
            'pnl_pct':         pnl / cost_basis if cost_basis > 0 else 0,
            'reason':          reason,
            'hold_time_hours': round((datetime.now() - datetime.fromisoformat(pos['opened_at'])).total_seconds() / 3600, 1),
            'highest_price':   pos['highest_price'],
            'portion':         portion,
            'tx_sell':         result.tx_signature,
        }
        state.history.append(rec)

        if portion >= 0.99:
            del state.positions[sym]
            logger.info(f"LIVE SELL {sym}: FULL ${pnl:+.2f} ({pnl/cost_basis:+.2%}) | {reason} | Tx: {result.tx_signature[:20]}...")
        else:
            pos['quantity'] -= sell_qty
            pos['invested'] -= cost_basis
            logger.info(f"LIVE SELL {sym}: PARTIAL {portion:.0%} ${pnl:+.2f} | {reason} | Tx: {result.tx_signature[:20]}...")

        record_symbol_trade(sym, pnl)

        if alerts:
            try:
                await alerts.send_position_closed({
                    'token': sym, 'pnl_pct': rec['pnl_pct'],
                    'pnl_usd': pnl, 'reason': reason, 'portion': portion,
                })
            except Exception as e:
                logger.warning(f"Alert failed: {e}")
        await _send_trade_snapshot()

    else:
        logger.error(f"LIVE SELL {sym} FAILED: {result.error} — falling back to paper")
        await paper_sell(sym, price, reason)

# ── PRICE LOOKUP ──
_PRICE_SANITY_CAP = 15.0   # reject any single update that is >15× the entry price

async def get_position_price(symbol: str, pos: Dict) -> Optional[float]:
    """Get current price. Rejects updates that are physically impossible."""
    cached = pos.get('last_price', pos['entry'])
    entry  = pos.get('entry', 0)

    def _sanity(p: float) -> bool:
        if p <= 0:
            return False
        # Allow up to 15× from ENTRY (meme coins can genuinely 10x)
        if entry > 0 and p > entry * _PRICE_SANITY_CAP:
            logger.warning(
                f"🚨 {symbol} price ${p:.6f} is >{_PRICE_SANITY_CAP}× entry ${entry:.6f} "
                f"— likely bad data, using cached ${cached:.6f}"
            )
            return False
        # Also reject if price dropped to <0.1% of entry (contract migrated / dead)
        if entry > 0 and p < entry * 0.001:
            logger.warning(f"🚨 {symbol} price ${p:.6f} collapsed to <0.1% of entry — using cached")
            return False
        return True

    if not dex:
        return cached

    # Try DexScreener token endpoint
    addr = pos.get('address')
    if addr:
        try:
            price = await dex.get_token_price(symbol, addr)
            if price and _sanity(price):
                pos['last_price'] = price
                return price
        except Exception:
            pass

    # Fallback: search by symbol
    try:
        results = await dex.search_token(symbol)
        if results:
            p = float(results[0].get('priceUsd', 0))
            if _sanity(p):
                pos['last_price'] = p
                return p
    except Exception:
        pass

    return cached

# ── MAIN LOOPS ──

paused = False

# Recently-bought set: blocks re-entry for 15 min after any buy, regardless of lifetime tracking
_recently_bought: Dict[str, datetime] = {}
_REENTRY_BLOCK_MIN = 15

def _block_reentry(sym: str):
    _recently_bought[sym.upper()] = datetime.now()

def _is_reentry_blocked(sym: str) -> bool:
    ts = _recently_bought.get(sym.upper())
    if ts and datetime.now() - ts < timedelta(minutes=_REENTRY_BLOCK_MIN):
        return True
    if sym.upper() in _recently_bought:
        del _recently_bought[sym.upper()]
    return False

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

            for token in tokens[:20]:  # Check top 20 — wider net, stricter filters catch the bad ones
                sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')

                if sym in state.positions:
                    continue

                # ── FAST REENTRY BLOCK (15 min after any buy/stop) ──
                if _is_reentry_blocked(sym):
                    logger.debug(f"⏱️ {sym} — reentry blocked ({_REENTRY_BLOCK_MIN}m cooldown)")
                    continue

                # ── PRE-FILTER: hard minimums before expensive momentum check ──
                vol_24h = float(token.get('volume', {}).get('h24', 0))
                liq_usd = float(token.get('liquidity', {}).get('usd', 0))
                ch24h   = float(token.get('priceChange', {}).get('h24', 0))
                if vol_24h < 50_000 or (liq_usd > 0 and liq_usd < 20_000) or ch24h > 500:
                    continue

                # ── MOMENTUM FILTER ──
                if MOMENTUM_OK:
                    enhanced = await evaluate_momentum(token)
                    if not enhanced:
                        continue
                    token = enhanced
                    logger.info(f"📈 MOMENTUM: {sym} | Score: {token.get('momentum_score', 0):.0f}")

                pos = await evaluate_entry(token)
                if pos:
                    pos['source'] = 'momentum' if MOMENTUM_OK else 'scanner'
                    pos['momentum_score'] = token.get('momentum_score', 0)
                    _block_reentry(sym)   # ← lock out re-entry the moment we buy

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

        await asyncio.sleep(30)   # 30s cycle — 2× faster signal detection

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

async def sweep_loop():
    """Background loop: check profit sweeper every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        if sweeper:
            try:
                result = await sweeper.check_and_sweep()
                if result and result.triggered:
                    if result.dry_run:
                        logger.info(f"💸 [DRY-RUN] Would sweep ${result.sweep_usd:.2f} to cold storage")
                    elif result.tx_signature:
                        logger.info(f"💸 SWEEPED ${result.sweep_usd:.2f} ({result.token_out}) | Tx: {result.tx_signature[:20]}...")
            except Exception as e:
                logger.warning(f"Sweep loop error: {e}")


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

    # Initialize profit sweeper
    if sweeper:
        cold_status = f"cold={sweeper.cold_address[:15]}..." if sweeper.cold_address else "cold=NOT SET"
        logger.info(
            f"💸 Profit sweeper: ACTIVE | mode={sweeper.mode} | "
            f"threshold=${sweeper.threshold} | "
            f"{cold_status}"
        )
    else:
        logger.info("💸 Profit sweeper: NOT LOADED")

    tasks = [
        asyncio.create_task(discovery_loop()),
        asyncio.create_task(sniper_loop()),
        asyncio.create_task(monitor_loop()),
        asyncio.create_task(report_loop()),
        asyncio.create_task(save_loop()),
        asyncio.create_task(daily_report_loop()),
    ]

    if sweeper:
        tasks.append(asyncio.create_task(sweep_loop()))

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
