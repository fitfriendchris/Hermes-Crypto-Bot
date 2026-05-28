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
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp
import yaml
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ── Optional module imports ──
try:
    from dex_connector import DEXConnector
    DEX_OK = True
except ImportError as e:
    DEX_OK = False
    print(f"[WARN] DEX connector unavailable: {e}")

try:
    from telegram_alerts import TelegramAlertManager, TELEGRAM_AVAILABLE
except ImportError:
    TELEGRAM_AVAILABLE = False

try:
    from profit_protection import ProfitProtection
    PP_OK = True
except ImportError:
    PP_OK = False

try:
    from wallet_integration import WalletManager
    WALLET_OK = True
except ImportError:
    WALLET_OK = False

# ── CONFIG ──
CONFIG_PATH = os.path.join(_HERE, 'CRYPTO_BOT_CONFIG.yaml')
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
        logging.FileHandler(os.path.join(_HERE, 'crypto_bot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('CryptoBot')

# ── PERSISTENT STATE ──
STATE_PATH = os.path.join(_HERE, 'bot_state.json')

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
        alerts = TelegramAlertManager(token, chat_id)
    else:
        logger.warning("Telegram credentials missing — alerts disabled")

profit_guard = ProfitProtection(CONFIG.get('profit_protection', {})) if PP_OK else None
wallet = WalletManager() if WALLET_OK else None
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
    except Exception as e:
        logger.warning(f"Wallet init failed: {e}")

# ── STOP LOSS CALCULATION ──
def calc_stop(entry: float, token_data: Dict) -> tuple:
    max_risk = CONFIG['stop_loss']['fixed_pct']
    fixed_stop = entry * (1 - max_risk)

    # Volatility-based override
    change_24h = token_data.get('priceChange', {}).get('h24', 0)
    if change_24h:
        vol_stop = entry * (1 - abs(change_24h) / 100 * 1.5)
        if vol_stop > fixed_stop:
            return vol_stop, 'volatility'

    return fixed_stop, 'fixed'

# ── ENTRY EVALUATION ──
async def evaluate_entry(token: Dict) -> Optional[Dict]:
    sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')
    price = float(token.get('priceUsd', 0) or token.get('price', 0))

    if price <= 0:
        return None

    # Safety circuits
    if len(state.positions) >= CONFIG['account']['max_open_positions']:
        return None
    if state.daily_pnl <= -CONFIG['safety']['max_daily_loss_usd']:
        return None
    if state.consecutive_losses >= CONFIG['safety']['max_consecutive_losses']:
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
        'token': sym,
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

    # Stop loss
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

    # Tiered exits
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

    # Breakeven trail at 2R
    if r >= 2.0 and pos['stop_type'] not in ('breakeven', 'trailing'):
        pos['stop'] = pos['entry'] * 1.005
        pos['stop_type'] = 'breakeven'
        logger.info(f"Moved {sym} stop to breakeven")

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
    if alerts:
        try:
            await alerts.send_position_opened(position)
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

async def paper_sell(sym: str, price: float, reason: str):
    pos = state.positions.pop(sym, None)
    if not pos:
        return

    proceeds = pos['quantity'] * price
    pnl = proceeds - pos['invested']
    pnl_pct = pnl / pos['invested'] if pos['invested'] > 0 else 0

    state.balance += proceeds
    state.daily_pnl += pnl

    if pnl < 0:
        state.consecutive_losses += 1
    else:
        state.consecutive_losses = 0
        if profit_guard:
            profit_guard.add_profit(pnl)

    result = {
        'token': sym,
        'entry': pos['entry'],
        'exit': price,
        'invested': pos['invested'],
        'proceeds': proceeds,
        'pnl': pnl,
        'pnl_pct': pnl_pct,
        'reason': reason,
        'hold_time_hours': round((datetime.now() - datetime.fromisoformat(pos['opened_at'])).total_seconds() / 3600, 1),
        'highest_price': pos['highest_price'],
    }
    state.history.append(result)

    logger.info(f"PAPER SELL {sym}: ${pnl:+.2f} ({pnl_pct:+.2%}) | {reason}")
    if alerts:
        try:
            await alerts.send_position_closed({
                'token': sym, 'pnl_pct': pnl_pct, 'pnl_usd': pnl, 'reason': reason
            })
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

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
async def get_position_price(sym: str, pos: Dict) -> Optional[float]:
    """Get current price for a position. Try DEX search first."""
    if not dex:
        return pos['last_price'] * 0.995  # Decay fallback

    # Try search by symbol
    try:
        pairs = await dex.search_token(sym)
        if pairs:
            price = float(pairs[0].get('priceUsd', 0))
            if price > 0:
                return price
    except Exception as e:
        logger.debug(f"Price search failed for {sym}: {e}")

    # Try pair data if we have an address
    addr = pos.get('address')
    chain = pos.get('chain', 'solana')
    if addr:
        try:
            price = await dex.get_token_price(chain, addr)
            if price and price > 0:
                return price
        except Exception as e:
            logger.debug(f"Price fetch failed for {sym}: {e}")

    # Fallback: decay last known price
    return pos['last_price'] * 0.995

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

            logger.info("🔍 Scanning DEX for opportunities...")
            tokens = await dex.discover_tokens("mixed", limit=20)
            logger.info(f"Found {len(tokens)} eligible tokens")

            for token in tokens[:5]:
                sym = token.get('baseToken', {}).get('symbol') or token.get('symbol', 'UNKNOWN')

                if sym in state.positions:
                    continue

                pos = await evaluate_entry(token)
                if pos:
                    if LIVE_MODE and wallet_ready:
                        await live_buy(pos)
                    else:
                        await paper_buy(pos)

                    if alerts:
                        try:
                            await alerts.send_whale_signal({
                                'token': sym,
                                'value_usd': pos['invested'],
                                'wallet': 'DEX_DISCOVERY'
                            })
                        except Exception as e:
                            logger.warning(f"Telegram alert failed: {e}")

        except Exception as e:
            logger.error(f"Discovery error: {e}")

        await asyncio.sleep(60)

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

    await init_wallet()

    if alerts:
        try:
            await alerts.send_startup()
        except Exception as e:
            logger.warning(f"Startup alert failed: {e}")

    tasks = [
        asyncio.create_task(discovery_loop()),
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
