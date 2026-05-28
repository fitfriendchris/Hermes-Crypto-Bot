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
from datetime import datetime, timedelta, timezone
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
    from momentum_scanner import evaluate_momentum, evaluate_momentum_fast, init_momentum_scanner
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

# ── NEW: Wallet Scorer + Discovery + Copy Engine ──
try:
    from wallet_scorer import WalletScorer, init_wallet_scorer
    from wallet_discovery import WalletDiscovery, init_wallet_discovery
    from copy_trader_v2 import CopyEngine
    from wallet_leaderboard import get_mirrors
    WALLET_SCORER_OK = True
except ImportError as e:
    WALLET_SCORER_OK = False
    print(f"[WARN] Wallet system unavailable: {e}")

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
    from HERMES_SWAP_EXECUTOR import SwapManager
    SWAP_OK = True
except ImportError:
    SWAP_OK = False

try:
    from HERMES_profit_sweeper import ProfitSweeper
    SWEEPER_OK = True
except ImportError:
    SWEEPER_OK = False

try:
    import hermes_brain
    BRAIN_OK = True
except ImportError as e:
    BRAIN_OK = False
    print(f"[WARN] LLM brain unavailable: {e}")

try:
    from high_attention_scalper import (
        init_high_attention, scan_high_attention, discover_high_attention,
        evaluate_high_attention, HighAttentionEngine
    )
    HIGH_ATTENTION_OK = True
except ImportError as e:
    HIGH_ATTENTION_OK = False
    print(f"[WARN] High-attention scalper unavailable: {e}")

# Mode state machine — single source of truth for active strategy
from bot_mode import (
    get_mode, set_mode, is_active, can_enter,
    MODE_OFF, MODE_SNIPER, MODE_COPY, MODE_HIGH_ATTENTION,
)

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
        self.weekly_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.started_at = datetime.now()
        self.max_balance = self.balance
        self.max_drawdown = 0.0
        self.total_protected = 0.0
        # Circuit-breaker state: timestamp until which entries are blocked
        self.halt_entries_until: Optional[str] = None
        self.halt_reason: str = ""
        # Bankroll snapshot at start-of-day / start-of-week for pct circuits
        self.day_start_balance = self.balance
        self.week_start_balance = self.balance

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
                self.weekly_pnl = data.get('weekly_pnl', 0)
                self.trades_today = data.get('trades_today', 0)
                self.consecutive_losses = data.get('consecutive_losses', 0)
                self.max_drawdown = data.get('max_drawdown', 0)
                self.total_protected = data.get('total_protected', 0)
                self.halt_entries_until = data.get('halt_entries_until')
                self.halt_reason = data.get('halt_reason', '')
                self.day_start_balance = data.get('day_start_balance', self.balance)
                self.week_start_balance = data.get('week_start_balance', self.balance)
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
                'weekly_pnl': self.weekly_pnl,
                'trades_today': self.trades_today,
                'consecutive_losses': self.consecutive_losses,
                'max_drawdown': self.max_drawdown,
                'total_protected': self.total_protected,
                'halt_entries_until': self.halt_entries_until,
                'halt_reason': self.halt_reason,
                'day_start_balance': self.day_start_balance,
                'week_start_balance': self.week_start_balance,
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

# ── NEW: Wallet Scoring + Discovery + Copy Engine ──
scorer = WalletScorer() if WALLET_SCORER_OK else None
discovery = WalletDiscovery(scorer) if WALLET_SCORER_OK else None
copy_engine = CopyEngine(wallet, swap_manager) if WALLET_SCORER_OK else None

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
    Volatility-aware stop for the micro-cap / memecoin entry path.

    Why so wide: normal intraday range on this asset class is 20-50%. A tight
    10-15% stop bleeds on noise AND chops out winners that legitimately dip
    before running. Empirical rule (orcACR + multiple practitioner writeups):
    35-50% stop + small size + time-stop primary. The 72h time-stop, not the
    price stop, does most of the exiting on faded moves.

    Risk-per-trade math: position 1.5% × stop 35% = 0.5% bankroll per stop-out.
    With 20 concurrent slots and ~30% noise hit-rate, daily bleed ≈ 0.5% — far
    inside the 2% daily circuit breaker.

    Reads floor/cap from config so they can be tuned without code changes.
    For scalp / arb sleeves with candle data, use atr_stop_from_candles() instead.
    """
    sl_cfg = CONFIG.get('stop_loss', {})
    floor_pct = float(sl_cfg.get('floor_pct', 0.35))   # ULTRA: 35% floor (was 15%, proven 25% too tight)
    cap_pct = float(sl_cfg.get('cap_pct', 0.50))        # ULTRA: 50% cap for extreme volatility

    change_24h = token_data.get('priceChange', {}).get('h24', 0) or 0
    abs_chg = abs(float(change_24h))

    # Scale stop with 24h move magnitude. Coefficient 0.005 gives:
    #   20% 24h move → 10% computed → clamped up to 15% floor
    #   100% 24h move → 50% computed → capped at 35%
    #   300% 24h move → cap at 35%
    vol_pct = max(min(abs_chg * 0.005, cap_pct), floor_pct)
    vol_stop = entry * (1 - vol_pct)

    fixed_stop = entry * (1 - floor_pct)

    if vol_stop > fixed_stop:
        return fixed_stop, 'floor'
    return vol_stop, 'volatility'


def atr_stop_from_candles(entry: float, candles: list, multiplier: float = 2.5) -> tuple:
    """
    ATR(14)-based stop for strategies that have a candle feed (scalpers, arb).
    candles: list of dicts with 'high', 'low', 'close'. Most recent last.
    Returns (stop_price, 'atr'). Falls back to 8% if insufficient data.
    """
    if not candles or len(candles) < 15:
        return entry * (1 - 0.05), 'atr_fallback'
    trs = []
    prev_close = candles[-15]['close']
    for c in candles[-14:]:
        tr = max(
            c['high'] - c['low'],
            abs(c['high'] - prev_close),
            abs(c['low'] - prev_close),
        )
        trs.append(tr)
        prev_close = c['close']
    atr = sum(trs) / len(trs)
    stop = entry - (atr * multiplier)
    # Floor at -5% (avoid pathologically tight stops in very calm regimes)
    return max(stop, entry * 0.95), 'atr'

# ── SOCIAL / RUG QUALITY GATE ──
# Tokens that have ZERO community presence AND zero social links are skipped
# unless they have very high liquidity ($100K+) as a substitute signal.
# This catches the garbage that passes volume/momentum but has no real community.

def _passes_quality_gate(token: Dict) -> bool:
    """
    Quality gate for micro-cap meme coins.
    ULTRA v2: Lowered thresholds to actually catch moving tokens.
    Requires:
      - Liquidity ≥ $10K (micro-cap minimum)
      - 24h Volume ≥ $5K (proves activity)
      - Price change data available (not dead token)
      - Social signal OR whitelist status (relaxed for fresh pumps)
    """
    info = token.get('info', {}) or {}
    socials = info.get('socials', []) or []
    websites = info.get('websites', []) or []
    liq = float(token.get('liquidity', {}).get('usd', 0))
    vol_24h = float(token.get('volume', {}).get('h24', 0))
    price = float(token.get('priceUsd', 0))
    ch1h = float(token.get('priceChange', {}).get('h1', 0))

    # ULTRA: Micro-cap thresholds
    has_liquidity = liq >= 10_000
    has_volume = vol_24h >= 5_000
    has_price = price > 0
    has_momentum = abs(ch1h) > 0

    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', '?')
    sym_upper = sym.upper()
    
    # Whitelist symbols bypass social requirement
    from symbol_filter import SYMBOL_WHITELIST
    is_whitelist = sym_upper in SYMBOL_WHITELIST
    has_social = bool(websites or socials) or is_whitelist

    if not has_liquidity:
        logger.info(f"🚫 {sym} — liquidity ${liq:,.0f} < $10K")
        return False
    if not has_volume:
        logger.info(f"🚫 {sym} — volume ${vol_24h:,.0f} < $5K")
        return False
    if not has_price:
        logger.info(f"🚫 {sym} — no price data")
        return False
    if not has_momentum:
        logger.info(f"🚫 {sym} — no recent price movement")
        return False
    if not has_social:
        logger.info(f"🚫 {sym} — no social presence")
        return False
    
    return True


# ── SAFETY CIRCUITS ──
def _circuits_pass() -> bool:
    """
    Returns True if entries are allowed. Halts when:
      - mode == OFF (master switch)
      - explicit timed halt window active
      - daily-loss pct, weekly-loss pct, or consecutive-loss caps breached
    Percent thresholds scale with account size; absolute keys retained as legacy floor.
    Monitor / exit loops are NOT gated by this — they always run.
    """
    if not can_enter():
        return False

    safety = CONFIG.get('safety', {})

    # Explicit halt window
    if state.halt_entries_until:
        try:
            until = datetime.fromisoformat(state.halt_entries_until)
            if datetime.now() < until:
                return False
            else:
                # Window expired — clear and continue
                state.halt_entries_until = None
                state.halt_reason = ""
        except Exception:
            state.halt_entries_until = None

    # Daily-loss pct circuit
    daily_pct_cap = float(safety.get('max_daily_loss_pct', 0.02))
    if state.day_start_balance > 0:
        daily_dd = (state.day_start_balance + state.daily_pnl) / state.day_start_balance - 1
        if daily_dd <= -daily_pct_cap:
            cool = float(safety.get('cooldown_hours', 24))
            state.halt_entries_until = (datetime.now() + timedelta(hours=cool)).isoformat()
            state.halt_reason = f"daily_loss_circuit ({daily_dd:.2%})"
            logger.warning(f"⛔ CIRCUIT FIRED: {state.halt_reason} — entries halted {cool}h")
            return False

    # Weekly-loss pct circuit
    weekly_pct_cap = float(safety.get('max_weekly_loss_pct', 0.05))
    if state.week_start_balance > 0:
        weekly_dd = (state.week_start_balance + state.weekly_pnl) / state.week_start_balance - 1
        if weekly_dd <= -weekly_pct_cap:
            # Adaptive cooldown: scale with drawdown severity
            if weekly_dd <= -0.20:
                cool_hours = 72  # -20% = 3 days (was 7)
            elif weekly_dd <= -0.10:
                cool_hours = 48  # -10% = 2 days
            else:
                cool_hours = 24   # -5% = 1 day
            state.halt_entries_until = (datetime.now() + timedelta(hours=cool_hours)).isoformat()
            state.halt_reason = f"weekly_loss_circuit ({weekly_dd:.2%})"
            logger.warning(f"⛔ CIRCUIT FIRED: {state.halt_reason} — entries halted {cool_hours}h")
            return False

    # Absolute floor (legacy) — fires if pct check didn't but dollar loss exceeded
    if state.daily_pnl <= -float(safety.get('max_daily_loss_usd', 1e9)):
        return False

    # Consecutive-loss streak
    if state.consecutive_losses >= int(safety.get('max_consecutive_losses', 5)):
        return False

    return True


# ── ENTRY EVALUATION ──
async def evaluate_entry(token: Dict) -> Optional[Dict]:
    sym   = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    price = float(token.get('priceUsd', 0) or token.get('price', 0))

    if price <= 0:
        return None

    # ── SYMBOL FILTER — Whitelist + Blacklist (ULTRA PLAN Phase 1) ──
    from symbol_filter import is_tradeable, get_position_size_pct, get_cooldown_hours
    allowed, reason = is_tradeable(sym)
    if not allowed:
        logger.warning(f"🚫 {sym} REJECTED — {reason}")
        return None
    logger.debug(f"✅ {sym} PASSED — {reason}")

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
    if not _circuits_pass():
        return None

    # ── SYMBOL LIFETIME FILTER ──
    sym_upper = sym.upper()
    if not can_trade_symbol(sym_upper):
        return None

    # ── POSITION SIZING (ULTRA PLAN: Kelly-derived + $2.50 minimum) ──
    # Get symbol-specific position size from whitelist scoring
    base_pct = get_position_size_pct(sym_upper, CONFIG['account']['max_risk_per_trade'])
    size = state.balance * base_pct
    
    # ULTRA: Enforce $2.50 minimum per position (must be able to swap back to SOL)
    MIN_POSITION_USD = 2.50
    if size < MIN_POSITION_USD:
        logger.warning(f"💰 {sym} position ${size:.2f} below ${MIN_POSITION_USD} minimum — SKIPPED")
        return None
    
    # Hard cap at 15% of balance max (Kelly limit)
    hard_cap_pct = min(float(CONFIG['account'].get('position_size_hard_cap_pct', 0.10)), 0.15)
    if size > state.balance * hard_cap_pct:
        size = state.balance * hard_cap_pct
    
    # ULTRA: Double cooldown on consecutive losses
    if state.consecutive_losses > 0:
        cooldown_hours = get_cooldown_hours(state.consecutive_losses)
        logger.info(f"⏸️ {sym} — {state.consecutive_losses} consecutive losses, {cooldown_hours}h cooldown active")
        # Cooldown is enforced at entry level, not per-symbol

    # ── STOP LOSS (ULTRA: 35% for micro-caps) ──
    stop, stop_type = calc_stop(price, token)
    risk = (price - stop) / price
    
    # Risk-equalization cap
    risk_budget_pct = float(CONFIG['account'].get('risk_budget_per_trade_pct', 0.006))
    max_risk_for_size = risk_budget_pct / base_pct if base_pct > 0 else 0.006
    if risk > max_risk_for_size:
        size = size * (max_risk_for_size / risk)
    
    # Final minimum check after all sizing logic
    if size < MIN_POSITION_USD:
        logger.warning(f"💰 {sym} sized-down to ${size:.2f}, below ${MIN_POSITION_USD} — SKIPPED")
        return None

    quantity = size / price if price > 0 else 0

    # ── LLM BRAIN VETO (final gate; off unless USE_LLM_BRAIN=true) ──
    # The brain can only BLOCK a trade the rules already approved.
    # On disable / timeout / error it returns a neutral verdict (no veto).
    brain_verdict = None
    if BRAIN_OK and hermes_brain.ENABLED:
        try:
            brain_verdict = await hermes_brain.score_entry(token)
            if brain_verdict.get('veto'):
                logger.info(
                    f"🧠 LLM veto {sym}: score={brain_verdict['score']:.2f} "
                    f"reason={brain_verdict.get('reason', '')[:80]}"
                )
                return None
        except Exception as e:
            logger.debug(f"brain veto check failed for {sym}: {e}")

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
        'brain':        brain_verdict,
        # mode_at_entry locks the exit semantics for this position. Even if the
        # user flips modes via the dashboard while we're holding, this position
        # will continue to exit under the rules of the mode that opened it.
        'mode_at_entry': get_mode(),
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
    """Dispatches to the exit engine that matches pos['mode_at_entry'].

    SNIPER positions use _check_exit_sniper (tight scaled take-profits, fast time stop).
    COPY positions use _check_exit_copy (principal recovery at 2x, wide trail, 72h time stop).
    Legacy positions without a mode tag fall back to copy semantics (safer).
    """
    pos = state.positions.get(sym)
    if not pos:
        return None

    if price > pos['highest_price']:
        pos['highest_price'] = price
    pos['last_price'] = price

    # Stop loss — ALWAYS check first (safety, mode-independent)
    if price <= pos['stop']:
        return f"stop_loss_{pos['stop_type']}"

    # Get mode config from position
    mode_tag = pos.get('mode_at_entry', MODE_COPY)
    mode_cfg = CONFIG.get('modes', {}).get(mode_tag, {})

    # Time stop — per mode (PUMP.FUN uses 2h max)
    opened = datetime.fromisoformat(pos['opened_at'])
    
    # Check if this is a Pump.fun position (has 'is_pumpfun' flag)
    if pos.get('is_pumpfun', False):
        hold_hours = 2  # Pump.fun: 2 hour max hold
    else:
        hold_hours = float(mode_cfg.get('time_stop_hours',
                                         CONFIG.get('stop_loss', {}).get('time_stop_hours', 72)))
    
    if datetime.now(timezone.utc) - opened > timedelta(hours=hold_hours):
        return "time_stop"

    # Pump.fun has its own exit logic
    if pos.get('is_pumpfun', False):
        return _check_exit_pumpfun(sym, pos, price)

    if mode_tag == MODE_SNIPER:
        return _check_exit_sniper(sym, pos, price, mode_cfg)
    else:
        return _check_exit_copy(sym, pos, price, mode_cfg)


def _check_exit_pumpfun(sym: str, pos: Dict, price: float) -> Optional[str]:
    """PUMP.FUN EXITS — Fast moon or quick death."""
    unrealized_pct = (price - pos['entry']) / pos['entry']
    
    # Pump.fun targets: 2x (100%), 3x (200%), 5x (400%)
    # These are INSANE targets but Pump.fun can do 10-100x in hours
    
    # Tier 1: Sell 50% at +100% (2x)
    if not pos['tier_exits']['1'] and unrealized_pct >= 1.0:
        pos['tier_exits']['1'] = True
        be_price = pos['entry'] * 1.05  # Move stop to +5% (lock profit)
        if be_price > pos['stop']:
            pos['stop'] = be_price
            pos['stop_type'] = 'breakeven'
        return "pumpfun_2x"
    
    # Tier 2: Sell 25% at +200% (3x)
    if pos['tier_exits']['1'] and not pos['tier_exits'].get('2') and unrealized_pct >= 2.0:
        pos['tier_exits']['2'] = True
        # Lock 100% gain as floor
        new_floor = pos['entry'] * 2.0
        if new_floor > pos['stop']:
            pos['stop'] = new_floor
            pos['stop_type'] = 'profit_floor'
        return "pumpfun_3x"
    
    # Tier 3: Sell 25% at +400% (5x)
    if pos['tier_exits'].get('2') and not pos['tier_exits'].get('3') and unrealized_pct >= 4.0:
        pos['tier_exits']['3'] = True
        return "pumpfun_5x"
    
    # Trailing stop after first tier
    if pos['tier_exits']['1']:
        # 30% trail — Pump.fun is volatile
        trail_price = pos['highest_price'] * 0.70
        floor = pos['entry'] * 1.05
        # ABSOLUTE FLOOR: stop can never go below 35% of entry (catastrophic loss limit)
        catastrophe_floor = pos['entry'] * 0.35
        trail_price = max(trail_price, floor, catastrophe_floor)
        if trail_price > pos['stop']:
            pos['stop'] = trail_price
            pos['stop_type'] = 'trailing'
    
    return None


def _check_exit_sniper(sym: str, pos: Dict, price: float, mode_cfg: Dict) -> Optional[str]:
    """Fast-scalp exits: scaled take-profits at +3%/+6%/+10%, tight trail after tier 1."""
    unrealized_pct = (price - pos['entry']) / pos['entry']
    tp = mode_cfg.get('take_profit', {})

    tier_1_pct = float(tp.get('tier_1_pct', 0.05))    # +5% (was 3%)
    tier_2_pct = float(tp.get('tier_2_pct', 0.12))    # +12% (was 6%)
    tier_3_pct = float(tp.get('tier_3_pct', 0.25))    # +25% (was 10%)
    trail_pct = float(tp.get('trail_pct', 0.08))      # 8% trail (was 4%)

    # Tier 1: sell 33% at +3%, move stop to BE
    if not pos['tier_exits']['1'] and unrealized_pct >= tier_1_pct:
        pos['tier_exits']['1'] = True
        be_price = pos['entry'] * 1.002
        if be_price > pos['stop']:
            pos['stop'] = be_price
            pos['stop_type'] = 'breakeven'
        return "sniper_tp1"

    # Tier 2: sell 33% at +6%
    if pos['tier_exits']['1'] and not pos['tier_exits'].get('2') and unrealized_pct >= tier_2_pct:
        pos['tier_exits']['2'] = True
        # Lock +tier_1_pct as new floor
        new_floor = pos['entry'] * (1 + tier_1_pct)
        if new_floor > pos['stop']:
            pos['stop'] = new_floor
            pos['stop_type'] = 'profit_floor'
        return "sniper_tp2"

    # Tier 3: sell remainder at +10%
    if pos['tier_exits'].get('2') and not pos['tier_exits'].get('3') and unrealized_pct >= tier_3_pct:
        pos['tier_exits']['3'] = True
        return "sniper_tp3"

    # Trailing stop after tier 1
    if pos['tier_exits']['1']:
        trail_price = pos['highest_price'] * (1 - trail_pct)
        floor = pos['entry'] * 1.002
        # ABSOLUTE FLOOR: stop can never go below 35% of entry
        catastrophe_floor = pos['entry'] * 0.35
        trail_price = max(trail_price, floor, catastrophe_floor)
        if trail_price > pos['stop']:
            pos['stop'] = trail_price
            pos['stop_type'] = 'trailing'

    return None


def _check_exit_copy(sym: str, pos: Dict, price: float, mode_cfg: Dict) -> Optional[str]:
    """Copy-trade exits: 2x principal recovery → 5x/10x scaled → wide trailing."""
    unrealized_pct = (price - pos['entry']) / pos['entry']
    tp = mode_cfg.get('take_profit', CONFIG.get('take_profit', {}))

    pr_r = float(tp.get('principal_recovery_r', 1.0))

    # Principal recovery at 2x (entry + pr_r × 100%)
    if not pos['tier_exits']['1'] and unrealized_pct >= pr_r:
        pos['tier_exits']['1'] = True
        be_price = pos['entry'] * 1.02
        if be_price > pos['stop']:
            pos['stop'] = be_price
            pos['stop_type'] = 'breakeven'
        logger.info(f"🔒 {sym} 2x hit — principal recovery, stop → BE+2% (${be_price:.6f})")
        return "principal_recovery"

    # Scaled exits on the moon bag
    if pos['tier_exits']['1']:
        if not pos['tier_exits'].get('2') and unrealized_pct >= 4.0:
            pos['tier_exits']['2'] = True
            return "scaled_5x"
        if not pos['tier_exits'].get('3') and unrealized_pct >= 9.0:
            pos['tier_exits']['3'] = True
            return "scaled_10x"

    # Wide trailing on the remainder
    if pos['tier_exits']['1']:
        trail_low = float(tp.get('trail_pct_low', 0.25))
        trail_mid = float(tp.get('trail_pct_mid', 0.20))
        trail_high = float(tp.get('trail_pct_high', 0.15))
        if unrealized_pct >= 5.0:
            effective_trail = trail_high
        elif unrealized_pct >= 1.0:
            effective_trail = trail_mid
        else:
            effective_trail = trail_low
        trail_price = pos['highest_price'] * (1 - effective_trail)
        floor = pos['entry'] * 1.02
        # ABSOLUTE FLOOR: stop can never go below 35% of entry
        catastrophe_floor = pos['entry'] * 0.35
        trail_price = max(trail_price, floor, catastrophe_floor)
        if trail_price > pos['stop']:
            pos['stop'] = trail_price
            pos['stop_type'] = 'trailing'

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
    state.save()   # IMMEDIATE PERSIST

    logger.info(f"PAPER BUY  {sym}: ${invested:.2f} @ ${position['entry']:.6f} | qty={position['quantity']:.4f}")

    # ── Telegram alerts ──
    if alerts:
        try:
            position.update(_account_snapshot())
            await alerts.send_position_opened(position)
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── True-balance snapshot after every trade ──
    await _send_trade_snapshot()


def _compute_sell_portion(pos: Dict, price: float, reason: str) -> float:
    """Compute the fraction of a position to sell, dispatched on exit reason.

    Used by BOTH paper_sell and live_sell so partial-exit semantics stay
    identical across execution modes. Without this, live mode would dump full
    positions on what should be partial sells (sniper_tp1, principal_recovery, etc).
    """
    mode_tag = pos.get('mode_at_entry', MODE_COPY)
    mode_cfg = CONFIG.get('modes', {}).get(mode_tag, {})
    mode_tp = mode_cfg.get('take_profit', {})

    if reason == "principal_recovery":
        # Sell entry_cost / current_market_value units → recovers cost basis
        current_mv = pos['quantity'] * price
        return min(pos['invested'] / current_mv, 0.95) if current_mv > 0 else 0.50
    if reason == "scaled_5x":
        return float(mode_tp.get('scaled_5x_portion',
                                  CONFIG.get('take_profit', {}).get('scaled_exit_5x_pct', 0.25)))
    if reason == "scaled_10x":
        return float(mode_tp.get('scaled_10x_portion',
                                  CONFIG.get('take_profit', {}).get('scaled_exit_10x_pct', 0.25)))
    if reason == "sniper_tp1":
        return float(mode_tp.get('tier_1_portion', 0.33))
    if reason == "sniper_tp2":
        return float(mode_tp.get('tier_2_portion', 0.33))
    if reason == "sniper_tp3":
        return float(mode_tp.get('tier_3_portion', 1.0))
    if 'tier_1_exit1' in reason:
        return 0.50  # legacy reason string
    # Default: full close (stop_loss, time_stop, manual close, unknown reason)
    return 1.0


async def paper_sell(sym: str, price: float, reason: str):
    pos = state.positions.get(sym)
    if not pos:
        return

    # ── HARD STOP-LOSS: Auto-exit any position down >25% ──
    entry = pos.get('entry', 0)
    if entry > 0 and price > 0:
        drawdown = (price - entry) / entry
        if drawdown <= -0.25 and reason != 'stop_loss':
            logger.warning(f"🚨 HARD STOP {sym}: {drawdown:+.1%} drawdown — forcing full exit")
            reason = 'stop_loss'

    portion = _compute_sell_portion(pos, price, reason)
    pyramid_this = False  # pyramid path is config-gated; preserved for compatibility

    sell_qty = pos['quantity'] * portion
    proceeds = sell_qty * price
    cost_basis = pos['invested'] * portion
    gross_pnl = proceeds - cost_basis
    
    # ACCOUNT FOR SWAP FEES: subtract round-trip fees from PnL
    from fee_calculator import calculate_net_pnl
    fee_adjusted = calculate_net_pnl(gross_pnl, cost_basis)
    pnl = fee_adjusted['net_pnl']  # Use net PnL for all tracking
    fees = fee_adjusted['fees_usd']
    
    if fees > 0:
        logger.info(f"💸 {sym} fees: ${fees:.3f} (roundtrip), gross=${gross_pnl:+.2f}, net=${pnl:+.2f}")

    state.balance += proceeds
    state.daily_pnl += pnl
    state.weekly_pnl += pnl

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
        'hold_time_hours': round((datetime.now(timezone.utc) - datetime.fromisoformat(pos['opened_at'])).total_seconds() / 3600, 1),
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
        
        # CHECK: remaining position must be >= $2.50 to swap back to SOL
        remaining_value = pos['quantity'] * price
        if remaining_value < 2.50:
            logger.warning(
                f"💰 {sym} remaining ${remaining_value:.2f} below $2.50 min — "
                f"CLOSING FULL POSITION instead of partial"
            )
            # Sell the rest too
            del state.positions[sym]
            logger.info(f"PAPER SELL {sym}: FULL (forced, below min) | {reason}")
        else:
            logger.info(f"PAPER SELL {sym}: PARTIAL {portion:.0%} ${pnl:+.2f} ({pnl/cost_basis:+.2%}) | {reason} | remaining qty={pos['quantity']:.4f}")

    # ── RECORD SYMBOL TRADE FOR LIFETIME TRACKING ──
    record_symbol_trade(sym, pnl)

    # ── PYRAMID: add to confirmed winner after Exit 1 ──
    # Gated by aggressive_mode.pyramiding (off by default under survival sizing).
    if pyramid_this and sym in state.positions and CONFIG.get('aggressive_mode', {}).get('pyramiding'):
        pyr_size = min(
            state.balance * CONFIG['account']['max_risk_per_trade'],
            state.balance * float(CONFIG['account'].get('position_size_hard_cap_pct', 0.05)),
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
            payload = {
                'token': sym, 'pnl_pct': result['pnl_pct'], 'pnl_usd': pnl,
                'reason': reason, 'portion': portion,
                'mode_at_entry': pos.get('mode_at_entry', '?'),
            }
            payload.update(_account_snapshot())
            await alerts.send_position_closed(payload)
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── True-balance snapshot after every trade ──
    await _send_trade_snapshot()

def _account_snapshot() -> Dict:
    """Compute current cash, total value, unrealized PnL, total return.

    Used inline by entry/exit Telegram pings so each trade alert is self-contained
    (PnL + balance + day + open-count all in one message).
    """
    unrealized = 0.0
    for sym, pos in state.positions.items():
        entry = pos.get('entry', 0)
        last = pos.get('last_price', entry)
        invested = pos.get('invested', 0)
        if entry > 0:
            unrealized += invested * ((last - entry) / entry)
    total = state.balance + unrealized
    start = float(CONFIG['account'].get('starting_balance_usd', 100.0))
    return {
        '_cash_after': state.balance,
        '_total_value': total,
        '_unrealized_pnl': unrealized,
        '_daily_pnl': state.daily_pnl,
        '_total_return_pct': (total - start) / start if start > 0 else 0,
        '_open_count': len(state.positions),
    }


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

    # ── SELL-ROUTE VALIDATION: Confirm Jupiter can sell this token BEFORE buying ──
    # This prevents buying tokens that can't be exited (common with micro-caps)
    sell_route_ok = False
    try:
        import aiohttp
        test_payload = {
            "inputMint": addr,
            "outputMint": "So11111111111111111111111111111111111111112",
            "amount": "1000000",
            "slippageBps": "200"
        }
        async with aiohttp.ClientSession() as test_session:
            async with test_session.get(
                "https://api.jup.ag/swap/v1/quote",
                params=test_payload,
                timeout=5
            ) as test_resp:
                if test_resp.status == 200:
                    sell_route_ok = True
                    logger.info(f"✅ Sell route confirmed for {sym}")
                else:
                    logger.warning(f"🚫 No Jupiter SELL route for {sym} — blocking buy (status {test_resp.status})")
    except Exception as e:
        logger.warning(f"🚫 Sell route check failed for {sym}: {e}")

    if not sell_route_ok:
        logger.info(f"LIVE BUY {sym} — no sell route; executing as paper")
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
        state.save()   # IMMEDIATE PERSIST: prevent race on restart
        logger.info(
            f"LIVE BUY {sym}: ${actual_usd:.2f} @ {position['entry']:.6f} "
            f"| qty={result.output_amount:.4f} | Tx: {result.tx_signature[:20]}..."
        )
        if alerts:
            try:
                position.update(_account_snapshot())
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

    portion = _compute_sell_portion(pos, price, reason)

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
        gross_pnl   = proceeds - cost_basis
        
        # ACCOUNT FOR SWAP FEES: subtract round-trip fees from PnL
        from fee_calculator import calculate_net_pnl
        fee_adjusted = calculate_net_pnl(gross_pnl, cost_basis)
        pnl = fee_adjusted['net_pnl']
        fees = fee_adjusted['fees_usd']
        
        if fees > 0:
            logger.info(f"💸 {sym} fees: ${fees:.3f} (roundtrip), gross=${gross_pnl:+.2f}, net=${pnl:+.2f}")

        state.balance    += proceeds
        state.daily_pnl  += pnl
        state.weekly_pnl += pnl

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
            'hold_time_hours': round((datetime.now(timezone.utc) - datetime.fromisoformat(pos['opened_at'])).total_seconds() / 3600, 1),
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
            
            # CHECK: remaining position must be >= $2.50 to swap back to SOL
            remaining_value = pos['quantity'] * price
            if remaining_value < 2.50:
                logger.warning(
                    f"💰 {sym} remaining ${remaining_value:.2f} below $2.50 min — "
                    f"CLOSING FULL POSITION instead of partial"
                )
                # Sell the rest too
                del state.positions[sym]
                logger.info(f"LIVE SELL {sym}: FULL (forced, below min) | {reason} | Tx: {result.tx_signature[:20]}...")
            else:
                logger.info(f"LIVE SELL {sym}: PARTIAL {portion:.0%} ${pnl:+.2f} | {reason} | Tx: {result.tx_signature[:20]}...")

        record_symbol_trade(sym, pnl)

        if alerts:
            try:
                payload = {
                    'token': sym, 'pnl_pct': rec['pnl_pct'],
                    'pnl_usd': pnl, 'reason': reason, 'portion': portion,
                    'mode_at_entry': pos.get('mode_at_entry', '?'),
                }
                payload.update(_account_snapshot())
                await alerts.send_position_closed(payload)
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

async def high_attention_loop():
    """🔥 High-attention micro coin loop. Active in HIGH_ATTENTION mode only."""
    global paused
    
    if not HIGH_ATTENTION_OK:
        logger.warning("high_attention_loop: module not importable; loop will idle")
        while True:
            await asyncio.sleep(60)
    
    while True:
        try:
            if paused or not is_active(MODE_HIGH_ATTENTION):
                await asyncio.sleep(30)
                continue
            
            # Check position limit
            max_positions = CONFIG.get('strategy', {}).get('trending_meme', {}).get('max_positions', 20)
            if len(state.positions) >= max_positions:
                logger.debug(f"Max positions ({max_positions}) reached — skipping high-attention scan")
                await asyncio.sleep(60)
                continue
            
            # Scan watchlist first (ATTENTION + manual adds)
            logger.info("🔥 Scanning high-attention watchlist...")
            watchlist_tokens = await scan_high_attention()
            
            for token in watchlist_tokens:
                sym = token.get('symbol', 'UNKNOWN')
                if sym in state.positions or _is_reentry_blocked(sym):
                    continue
                
                pos = await evaluate_high_attention(token, state.balance)
                if pos:
                    # ── ANTI-RUG CHECK (critical safety gate) ──
                    addr = pos.get('address', '')
                    if addr and ANTIRUG_OK:
                        from anti_rug_suite import run_full_rug_check
                        rug_result = await run_full_rug_check(addr)
                        if not rug_result['safe']:
                            logger.warning(f"🚫 {sym} blocked by anti-rug: {rug_result['flags']}")
                            continue
                    
                    pos['source'] = 'high_attention'
                    pos['mode_at_entry'] = MODE_HIGH_ATTENTION
                    _block_reentry(sym)
                    
                    if LIVE_MODE and wallet_ready:
                        await live_buy(pos)
                    else:
                        await paper_buy(pos)
                    
                    logger.info(
                        f"🔥 HIGH-ATTENTION BUY: {sym} @ ${pos['entry']:.6f} | "
                        f"Size: ${pos['invested']:.2f} | 1h: +{pos.get('change_1h', 0):.1f}%"
                    )
                    
                    if alerts:
                        try:
                            await alerts.send_info(
                                f"🔥 HIGH-ATTENTION ENTRY: {sym}\n"
                                f"Price: ${pos['entry']:.6f}\n"
                                f"Size: ${pos['invested']:.2f}\n"
                                f"1h Change: +{pos.get('change_1h', 0):.1f}%\n"
                                f"Volume: ${pos.get('volume_1h', 0):.0f}\n"
                                f"Liquidity: ${pos.get('liquidity', 0):.0f}"
                            )
                        except Exception as e:
                            logger.warning(f"Telegram alert failed: {e}")
                    
                    # One entry per cycle to avoid overexposure
                    break
            
            # Auto-discover similar tokens if enabled
            ha_config = CONFIG.get('strategy', {}).get('trending_meme', {})
            if ha_config.get('auto_discover', True) and len(state.positions) < max_positions:
                discovered = await discover_high_attention()
                for token in discovered:
                    sym = token.get('symbol', 'UNKNOWN')
                    if sym in state.positions or _is_reentry_blocked(sym):
                        continue
                    
                    pos = await evaluate_high_attention(token, state.balance)
                    if pos:
                        # ── ANTI-RUG CHECK (critical safety gate) ──
                        addr = pos.get('address', '')
                        if addr and ANTIRUG_OK:
                            from anti_rug_suite import run_full_rug_check
                            rug_result = await run_full_rug_check(addr)
                            if not rug_result['safe']:
                                logger.warning(f"🚫 {sym} blocked by anti-rug: {rug_result['flags']}")
                                continue
                        
                        pos['source'] = 'high_attention_discovered'
                        pos['mode_at_entry'] = MODE_HIGH_ATTENTION
                        _block_reentry(sym)
                        
                        if LIVE_MODE and wallet_ready:
                            await live_buy(pos)
                        else:
                            await paper_buy(pos)
                        
                        logger.info(
                            f"🔥 DISCOVERED HIGH-ATTENTION: {sym} @ ${pos['entry']:.6f} | "
                            f"Size: ${pos['invested']:.2f}"
                        )
                        break
        
        except Exception as e:
            import traceback
            logger.error(f"High-attention loop error: {e}\n{traceback.format_exc()}")
        
        await asyncio.sleep(15)  # 15s cycle — fast for high-attention tokens


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
    """Scan DEXs for micro-cap opportunities. Active in SNIPER mode only."""
    global paused
    while True:
        try:
            if paused or not is_active(MODE_SNIPER):
                await asyncio.sleep(30)
                continue

            sniper_entries = CONFIG.get('modes', {}).get(MODE_SNIPER, {}).get('entries', {})
            if not sniper_entries.get('momentum_scanner', True):
                await asyncio.sleep(30)
                continue

            if not dex:
                logger.warning("DEX unavailable — skipping discovery")
                await asyncio.sleep(60)
                continue

            # ── MOMENTUM SCAN ──
            logger.info("🔍 Scanning DEX for opportunities...")
            tokens = await dex.discover_tokens("mixed", limit=50)  # Try all sources
            logger.info(f"Found {len(tokens)} eligible tokens")

            for token in tokens[:20]:  # Check top 20 — wider net, stricter filters catch the bad ones
                sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')

                if sym in state.positions:
                    continue

                # ── FAST REENTRY BLOCK (15 min after any buy/stop) ──
                if _is_reentry_blocked(sym):
                    logger.debug(f"⏱️ {sym} — reentry blocked ({_REENTRY_BLOCK_MIN}m cooldown)")
                    continue

                # ── DRY-RUN: Confirm Jupiter route exists BEFORE buying ──
                dry_run_pass = False
                try:
                    test_payload = {
                        "inputMint": "So11111111111111111111111111111111111111112",
                        "outputMint": token_address,
                        "amount": "1000000",
                        "slippageBps": "200"
                    }
                    async with aiohttp.ClientSession() as test_session:
                        async with test_session.get(
                            "https://api.jup.ag/swap/v1/quote",
                            params=test_payload,
                            timeout=5
                        ) as test_resp:
                            if test_resp.status == 200:
                                dry_run_pass = True
                                logger.debug(f"Jupiter dry-run OK for {sym}")
                            else:
                                logger.info(f"Jupiter dry-run FAIL for {sym}: {test_resp.status} — skipping")
                except Exception as e:
                    logger.debug(f"Dry-run error for {sym}: {e}")

                if not dry_run_pass:
                    logger.info(f"🚫 No Jupiter route for {sym} — skipping")
                    continue

                # ── PRE-FILTER: hard minimums before expensive momentum check ──
                vol_24h = float(token.get('volume', {}).get('h24', 0))
                liq_usd = float(token.get('liquidity', {}).get('usd', 0))
                ch24h   = float(token.get('priceChange', {}).get('h24', 0))
                if vol_24h < 20_000 or (liq_usd > 0 and liq_usd < 20_000) or ch24h > 300:
                    continue

                # ── MOMENTUM FILTER (fast path for live scanning) ──
                if MOMENTUM_OK:
                    enhanced = await evaluate_momentum_fast(token)
                    if not enhanced:
                        continue
                    token = enhanced
                    logger.info(f"📈 MOMENTUM: {sym} | Score: {token.get('momentum_score', 0):.0f}")

                # ── DRY-RUN: Confirm Jupiter route exists BEFORE buying ──
                dry_run_pass = False
                try:
                    test_payload = {
                        "inputMint": "So11111111111111111111111111111111111111112",
                        "outputMint": token_address,
                        "amount": "1000000",
                        "slippageBps": "200"
                    }
                    async with aiohttp.ClientSession() as test_session:
                        async with test_session.get(
                            "https://api.jup.ag/swap/v1/quote",
                            params=test_payload,
                            timeout=5
                        ) as test_resp:
                            if test_resp.status == 200:
                                dry_run_pass = True
                                logger.debug(f"Jupiter dry-run OK for {sym}")
                            else:
                                logger.info(f"Jupiter dry-run FAIL for {sym}: {test_resp.status} — skipping")
                except Exception as e:
                    logger.debug(f"Dry-run error for {sym}: {e}")

                if not dry_run_pass:
                    logger.info(f"🚫 No Jupiter route for {sym} — skipping")
                    continue

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

            # Copy trading lives in its own mode-gated loop (copy_loop below).
            # The momentum/sniper scanner here ONLY runs in SNIPER mode.

        except Exception as e:
            logger.error(f"Discovery error: {e}")

        await asyncio.sleep(30)   # 30s cycle — 2× faster signal detection
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

    # Copy trading lives in its own mode-gated loop (copy_loop below).
    # The momentum/sniper scanner here ONLY runs in SNIPER mode.

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
    """Dedicated launch sniper loop. Active in SNIPER mode only."""
    global paused
    while True:
        try:
            if paused or not is_active(MODE_SNIPER):
                await asyncio.sleep(60)
                continue

            sniper_entries = CONFIG.get('modes', {}).get(MODE_SNIPER, {}).get('entries', {})
            if not sniper_entries.get('launch_sniper', True):
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
                        
                        # Survival sizing — sniper bound by global hard cap, not its
                        # own 5% default. Honors max_risk_per_trade as the source of truth.
                        sniper_pct = float(SNIPER_CONFIG.get('position_size_pct', 0.015))
                        survival_cap = float(CONFIG['account'].get('position_size_hard_cap_pct', 0.05))
                        sniper_pct = min(sniper_pct, survival_cap)
                        size = state.balance * sniper_pct
                        size = max(size, CONFIG['account']['min_trade_size_usd'])
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

async def copy_loop():
    """Copy-trader entry loop. Active in COPY mode only.

    Scans verified whale wallets (90d-PnL scoreboard) for recent buys and emits
    mirror entries. Sizing capped at 1.5% bankroll / 0.5× whale allocation.
    Exits are governed by COPY-mode rules (principal recovery at 2x, wide trail)
    — see _check_exit_copy.
    """
    global paused

    if not COPY_OK:
        logger.warning("copy_loop: copy_trader module not importable; loop will idle")

    while True:
        try:
            if paused or not is_active(MODE_COPY) or not COPY_OK:
                await asyncio.sleep(30)
                continue

            signals = await scan_whale_wallets()
            for signal in signals:
                from copy_trader import evaluate_copy_signal
                result = await evaluate_copy_signal(signal, state.balance)
                if not result:
                    continue
                sym = result['token']
                if sym in state.positions or _is_reentry_blocked(sym):
                    continue

                # DRY-RUN: Validate Jupiter route exists before mirroring
                token_address = result.get('address', '')
                if token_address:
                    dry_run_pass = False
                    try:
                        import aiohttp
                        test_payload = {
                            "inputMint": "So11111111111111111111111111111111111111112",
                            "outputMint": token_address,
                            "amount": "1000000",
                            "slippageBps": "200"
                        }
                        async with aiohttp.ClientSession() as test_session:
                            async with test_session.get(
                                "https://api.jup.ag/swap/v1/quote",
                                params=test_payload,
                                timeout=5
                            ) as test_resp:
                                if test_resp.status == 200:
                                    dry_run_pass = True
                                    logger.debug(f"Copy dry-run OK for {sym}")
                                else:
                                    logger.info(f"🚫 Copy target {sym} not on Jupiter — skipping")
                    except Exception:
                        pass
                    if not dry_run_pass:
                        continue

                # Synthesize the token dict that evaluate_entry expects
                pos = await evaluate_entry({
                    'symbol': sym,
                    'priceUsd': result['entry'],
                    'tokenAddress': result.get('address'),
                    'liquidity': {'usd': 100_000},   # rug-check ran upstream
                    'priceChange': {'h24': 0},
                    'info': {'socials': ['verified_wallet'], 'websites': ['verified_wallet']},
                })
                if not pos:
                    continue
                # Override sizing with the whale-proportional value
                pos['invested'] = result['invested']
                pos['quantity'] = result['quantity']
                pos['source'] = 'copy_trader'
                pos['wallet_id'] = result.get('wallet_id')
                pos['wallet_name'] = result.get('wallet_name')

                _block_reentry(sym)
                if LIVE_MODE and wallet_ready:
                    await live_buy(pos)
                else:
                    await paper_buy(pos)
                logger.info(
                    f"🐋 COPY {sym} via {result.get('wallet_name', '?')} "
                    f"| ${result['invested']:.2f} "
                    f"({result.get('our_allocation', 0)*100:.2f}% of portfolio)"
                )

        except Exception as e:
            logger.error(f"copy_loop error: {e}")

        await asyncio.sleep(20)


async def copy_trader_v2_loop():
    """NEW: Wallet-scored copy trader loop. Runs in HIGH_ATTENTION mode too.
    
    Scans tracked wallets for new buys, mirrors with smart sizing.
    Replaces the old COPY mode loop with a real system.
    """
    global paused

    if not WALLET_SCORER_OK or not copy_engine:
        logger.warning("copy_trader_v2: engine not available; loop will idle")
        while True:
            await asyncio.sleep(60)

    while True:
        try:
            if paused:
                await asyncio.sleep(30)
                continue

            # Only run if we have tracked wallets
            if not copy_engine.tracked_wallets:
                logger.debug("No tracked wallets — skipping copy scan")
                await asyncio.sleep(60)
                continue

            # Check position limit
            if len(state.positions) >= CONFIG['account']['max_open_positions']:
                await asyncio.sleep(60)
                continue

            # Scan for new trades from tracked wallets
            signals = await copy_engine.scan_for_new_trades()
            for signal in signals:
                # Check circuits BEFORE executing
                if not _circuits_pass():
                    logger.warning("⛔ Copy trade blocked — circuit breaker active")
                    break

                # Mode check: copy trading only in COPY or HIGH_ATTENTION modes
                current_mode = get_mode()
                if current_mode not in (MODE_COPY, MODE_HIGH_ATTENTION):
                    logger.debug(f"Copy loop idle — mode={current_mode}, not COPY/HIGH_ATTENTION")
                    await asyncio.sleep(60)
                    continue

                # Get token data for entry evaluation
                token_mint = signal.get("token_mint", "")
                if not token_mint:
                    continue

                # DRY-RUN: Validate Jupiter route before ANY copy trade
                dry_run_pass = False
                try:
                    import aiohttp
                    test_payload = {
                        "inputMint": "So11111111111111111111111111111111111111112",
                        "outputMint": token_mint,
                        "amount": "1000000",
                        "slippageBps": "200"
                    }
                    async with aiohttp.ClientSession() as test_session:
                        async with test_session.get(
                            "https://api.jup.ag/swap/v1/quote",
                            params=test_payload,
                            timeout=5
                        ) as test_resp:
                            if test_resp.status == 200:
                                dry_run_pass = True
                                logger.info(f"✅ Copy v2 dry-run OK for {token_mint[:20]}...")
                            else:
                                logger.info(f"🚫 Copy v2 target not on Jupiter ({test_resp.status}) — skipping")
                except Exception:
                    pass
                if not dry_run_pass:
                    continue

                # Anti-rug check
                if ANTIRUG_OK:
                    rug_result = await run_full_rug_check(token_mint)
                    if not rug_result['safe']:
                        logger.warning(f"🚫 Copy target blocked by anti-rug: {rug_result['flags']}")
                        continue

                # Get real price from Jupiter
                entry_price = 0
                try:
                    async with aiohttp.ClientSession() as price_session:
                        price_payload = {
                            "inputMint": COPY_CONFIG["sol_mint"],
                            "outputMint": token_mint,
                            "amount": "100000000",
                            "slippageBps": "300"
                        }
                        async with price_session.get(
                            COPY_CONFIG["jupiter_quote_url"],
                            params=price_payload,
                            timeout=5
                        ) as price_resp:
                            if price_resp.status == 200:
                                price_data = await price_resp.json()
                                if price_data.get('data'):
                                    out_amount = float(price_data['data'].get('outAmount', 0))
                                    in_amount = float(price_data['data'].get('inAmount', 1))
                                    entry_price = (0.1 * out_amount) / in_amount if in_amount > 0 else 0
                except Exception:
                    pass

                if entry_price <= 0:
                    logger.info(f"🚫 Copy price fetch failed for {token_mint[:20]}...")
                    continue

                # Build position directly — skip evaluate_entry() for copy trades
                # Whale-verified trades don't need DEX-screening criteria
                invested = min(COPY_CONFIG["position_size_usd"], state.balance * COPY_CONFIG["max_position_pct"])
                if invested <= 0 or invested > state.balance:
                    logger.info(f"🚫 Copy position size invalid: ${invested:.2f} (balance: ${state.balance:.2f})")
                    continue

                pos = {
                    'token': signal.get('token_symbol', 'UNKNOWN'),
                    'entry': entry_price,
                    'invested': invested,
                    'quantity': invested / entry_price,
                    'stop': entry_price * 0.75,  # -25% hard stop
                    'target': entry_price * 1.50,  # +50% target
                    'time': datetime.now(timezone.utc).isoformat(),
                    'source': 'copy_trader_v2',
                    'wallet_id': signal['wallet'],
                    'wallet_name': signal.get('wallet_name', 'Unknown'),
                    'mode_at_entry': MODE_COPY,
                    'copied_from': signal['wallet'],
                    'source_tx': signal['tx'],
                    'paper': not LIVE_MODE,
                }

                _block_reentry(pos['token'])
                if LIVE_MODE and wallet_ready:
                    await live_buy(pos)
                else:
                    await paper_buy(pos)

                logger.info(
                    f"🐋 COPY EXECUTED: {pos['token']} | ${pos['invested']:.2f} | "
                    f"Entry: ${pos['entry']:.8f} | "
                    f"From: {signal['wallet'][:20]}... | "
                    f"Tx: {signal['tx'][:20]}..."
                )

                if alerts:
                    try:
                        await alerts.send_info(
                            f"🐋 COPY TRADE EXECUTED\n"
                            f"Token: {pos['token']}\n"
                            f"Size: ${pos['invested']:.2f}\n"
                            f"Entry: ${pos['entry']:.8f}\n"
                            f"Stop: ${pos['stop']:.8f} (-25%)\n"
                            f"Target: ${pos['target']:.8f} (+50%)\n"
                            f"Copied from: {signal['wallet'][:20]}..."
                        )
                    except Exception:
                        pass

                # One copy per cycle to avoid overexposure
                break

            # Periodic re-discovery (every 6 hours)
            # This is crude — better to use a cron or separate task
            hour = datetime.now().hour
            minute = datetime.now().minute
            if hour % 6 == 0 and minute < 2 and discovery:
                try:
                    logger.info("🔄 Running scheduled wallet re-discovery...")
                    scored = await discovery.run_discovery_cycle()
                    mirrors = discovery.get_top_mirrors(n=3)
                    if mirrors:
                        await copy_engine.set_tracked_wallets(mirrors)
                        logger.info(f"🐋 Updated mirrors: {len(mirrors)} wallets")
                except Exception as e:
                    logger.warning(f"Re-discovery failed: {e}")

        except Exception as e:
            logger.error(f"copy_trader_v2_loop error: {e}")

        await asyncio.sleep(15)  # 15s scan cycle — faster than old 20s


async def monitor_loop():
    """Monitor open positions for exits."""
    global paused
    while True:
        try:
            for sym, pos in list(state.positions.items()):
                price = await get_position_price(sym, pos)
                if not price:
                    continue

                # ── HARD STOP-LOSS: Force exit any position down >25% ──
                entry = pos.get('entry', 0)
                if entry > 0:
                    drawdown = (price - entry) / entry
                    if drawdown <= -0.25:
                        logger.warning(f"🚨 MONITOR HARD STOP {sym}: {drawdown:+.1%} — forcing exit")
                        if LIVE_MODE and wallet_ready:
                            await live_sell(sym, price, 'stop_loss')
                        else:
                            await paper_sell(sym, price, 'stop_loss')
                        continue  # Position handled, move to next

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
            # Only trigger on drawdown from TODAY's starting balance, not all-time
            today_dd = (state.day_start_balance - val) / state.day_start_balance if state.day_start_balance > 0 else 0
            if today_dd > CONFIG['account']['max_drawdown_pct'] and state.trades_today > 0:
                logger.warning(f"🚨 MAX DRAWDOWN HIT: {today_dd:.1%} (today) — pausing new entries")
                global paused
                paused = True
            elif state.max_drawdown > CONFIG['account']['max_drawdown_pct']:
                # Log but don't pause for stale all-time drawdown
                logger.info(f"📊 All-time DD: {state.max_drawdown:.1%} | Today DD: {today_dd:.1%} — within limits")

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

            # Roll daily counters; snapshot day-start balance for tomorrow's circuit
            state.daily_pnl = 0.0
            state.trades_today = 0
            state.day_start_balance = val

            # Weekly rollover (Sunday → Monday boundary)
            if datetime.now().weekday() == 0:
                logger.info(f"📊 WEEKLY: PnL ${state.weekly_pnl:+.2f}")
                state.weekly_pnl = 0.0
                state.week_start_balance = val

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
    # Ensure mode file exists; honor default_on_first_run if not.
    if not os.path.exists(os.path.join(_HERE, 'state', 'bot_mode.json')):
        default = CONFIG.get('modes', {}).get('default_on_first_run', MODE_OFF)
        set_mode(default, reason="first_run_default")
    current_mode = get_mode()

    logger.info("=" * 50)
    logger.info(f"🚀 CRYPTO BOT v2.0 | Execution: {'LIVE' if LIVE_MODE else 'PAPER'}")
    logger.info(f"🎚️ Active Mode: {current_mode}")
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

    # Initialize new wallet scoring + discovery
    if WALLET_SCORER_OK and scorer:
        await scorer.initialize()
        await discovery.initialize()
        await copy_engine.initialize()
        logger.info("📊 Wallet scorer + discovery + copy engine: ACTIVE")

    # Initialize high-attention scalper
    if HIGH_ATTENTION_OK:
        await init_high_attention()
        logger.info("🔥 High-attention scalper: ACTIVE")

    await init_wallet()

    # After wallet init, run wallet discovery if no tracked wallets
    if WALLET_SCORER_OK and copy_engine:
        logger.info(f"🔍 Copy engine has {len(copy_engine.tracked_wallets)} tracked wallets")
        if not copy_engine.tracked_wallets:
            logger.info("🔍 No tracked wallets — loading from leaderboard...")
            try:
                mirrors = get_mirrors(min_pnl=50.0, min_win_rate=0.45)
                if mirrors:
                    await copy_engine.set_tracked_wallets(mirrors)
                    logger.info(f"🐋 Now mirroring {len(mirrors)} wallets from leaderboard")
                    for m in mirrors:
                        logger.info(
                            f"   → {m['name']} | {m['wallet'][:20]}... | "
                            f"Score: {m['tier']} | PnL: +{m['pnl_30d_sol']:.1f} SOL | WinRate: {m['win_rate']:.0%}"
                        )
                else:
                    logger.warning("Leaderboard has no MIRROR-grade wallets")
            except Exception as e:
                logger.warning(f"Leaderboard load failed: {e}")
        else:
            logger.info(f"🐋 Already tracking {len(copy_engine.tracked_wallets)} wallets — skipping leaderboard load")

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
        asyncio.create_task(copy_trader_v2_loop()),  # ONLY copy trading — verified wallets
        asyncio.create_task(monitor_loop()),
        asyncio.create_task(report_loop()),
        asyncio.create_task(save_loop()),
        asyncio.create_task(daily_report_loop()),
    ]

    # Optional: sweep profits to cold wallet
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
