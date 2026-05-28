"""
BTC HEDGE BOT — Standalone 15m BTC trader
Author: Hermes | May 2026

Runs alongside the main crypto bot or standalone.
- Checks 15m BTC signal every 5 minutes
- Long: RSI < 35 + price at/below lower BB
- Short: RSI > 70 + price at/above upper BB
- Position: 15% of balance
- Stop: 1.5% | Target: 3% | Time stop: 4h
- Max 1 position at a time
"""

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from btc_hedge_strategy import BTCHedgeStrategy
from HERMES_SWAP_EXECUTOR import JupiterSwap, SwapManager
from HERMES_wallet_integration import WalletManager

# ── LOGGING ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'logs', 'btc_hedge.log')),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger('BTCHedge')

# ── CONFIG ──
CHECK_INTERVAL = 300  # 5 minutes
MAX_POSITIONS = 1
POSITION_PCT = 0.15
STOP_PCT = 0.015
TP_PCT = 0.03
TIME_STOP_HOURS = 4

STATE_FILE = os.path.join(os.path.dirname(__file__), 'state', 'btc_hedge_state.json')

# ── STATE ──
class HedgeState:
    def __init__(self):
        self.balance: float = 0.0
        self.position: Optional[Dict] = None
        self.history: list = []
        self.daily_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.trades: int = 0
        self.load()

    def load(self):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            self.balance = data.get('balance', 0.0)
            self.position = data.get('position')
            self.history = data.get('history', [])
            self.daily_pnl = data.get('daily_pnl', 0.0)
            self.total_pnl = data.get('total_pnl', 0.0)
            self.trades = data.get('trades', 0)
        except FileNotFoundError:
            pass

    def save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w') as f:
            json.dump({
                'balance': self.balance,
                'position': self.position,
                'history': self.history[-100:],  # Keep last 100
                'daily_pnl': self.daily_pnl,
                'total_pnl': self.total_pnl,
                'trades': self.trades,
                'updated_at': datetime.now().isoformat(),
            }, f, indent=2, default=str)

    def reset_daily(self):
        self.daily_pnl = 0.0

state = HedgeState()

# ── WALLET ──
wallet = WalletManager()
swap_manager: Optional[SwapManager] = None
wallet_ready = False

async def init_wallet():
    global wallet_ready, swap_manager
    try:
        preferred = 'exodus'
        await wallet.initialize(chain='solana', wallet=preferred)
        sol_balance = await wallet.get_balance()
        logger.info(f"Wallet ready: {preferred} | {wallet.get_address()[:20]}... | {sol_balance:.4f} SOL")
        
        swap_manager = SwapManager(wallet)
        await swap_manager.initialize()
        logger.info("Swap manager initialized")
        
        # Sync balance
        if swap_manager:
            sol_price = await swap_manager.get_sol_price()
            state.balance = sol_balance * sol_price
            state.save()
            logger.info(f"Balance synced: ${state.balance:.2f}")
        
        wallet_ready = True
    except Exception as e:
        logger.error(f"Wallet init failed: {e}")

# ── POSITION MANAGEMENT ──
async def open_position(signal: Dict):
    """Open BTC position via Jupiter (SOL -> WBTC for long, reverse for short)."""
    if state.position:
        logger.warning("Position already open, skipping")
        return

    size_usd = state.balance * POSITION_PCT
    size_usd = min(size_usd, state.balance * 0.5)  # Max 50% of balance
    if size_usd < 5.0:
        logger.warning(f"Position size ${size_usd:.2f} too small")
        return

    direction = signal['direction']
    entry = signal['entry_price']
    stop = signal['stop_loss']
    tp = signal['take_profit']

    logger.info(f"📊 Opening {direction.upper()} BTC | Size: ${size_usd:.2f} | Entry: ${entry:,.0f}")

    if not wallet_ready or not swap_manager:
        logger.error("Wallet or swap manager not ready")
        return

    try:
        # Convert USD to lamports
        sol_price = await swap_manager.get_sol_price()
        lamports = int((size_usd / sol_price) * 1e9)
        
        if direction == 'long':
            # Buy WBTC with SOL
            result = await swap_manager.execute_swap(
                input_mint=SOL_MINT,
                output_mint=WBTC_MINT,
                amount_in=lamports,
                slippage_bps=100,
            )
        else:
            # For short, we'd need perps or borrow — for now, just log
            # Short requires: borrow WBTC, sell for SOL, buy back later
            logger.info("Short BTC — requires perp/borrow, not yet implemented")
            return

        if result.success:
            state.position = {
                'direction': direction,
                'entry': entry,
                'stop': stop,
                'take_profit': tp,
                'size_usd': size_usd,
                'tx': result.tx_signature,
                'opened_at': datetime.now().isoformat(),
                'highest_price': entry,
                'lowest_price': entry,
            }
            state.balance -= size_usd
            state.save()
            logger.info(f"✅ Position opened | Tx: {result.tx_signature[:20]}...")
        else:
            logger.error(f"❌ Position open failed: {result.error}")
    except Exception as e:
        logger.error(f"Position open error: {e}")

async def check_exit():
    """Check if position should be closed."""
    if not state.position:
        return

    pos = state.position
    direction = pos['direction']
    entry = pos['entry']
    stop = pos['stop']
    tp = pos['take_profit']
    opened = datetime.fromisoformat(pos['opened_at'])
    
    # Get current BTC price
    strategy = BTCHedgeStrategy()
    await strategy.initialize()
    candles = await strategy.analyzer.get_btc_ohlc()
    await strategy.close()
    
    if not candles:
        return
    
    current = candles[-1]['close']
    
    # Update highest/lowest
    if current > pos['highest_price']:
        pos['highest_price'] = current
    if current < pos['lowest_price']:
        pos['lowest_price'] = current
    
    pnl_pct = (current - entry) / entry
    if direction == 'short':
        pnl_pct = -pnl_pct
    
    # Stop loss
    if pnl_pct <= -STOP_PCT:
        await close_position('stop_loss', pnl_pct)
        return
    
    # Take profit
    if pnl_pct >= TP_PCT:
        await close_position('take_profit', pnl_pct)
        return
    
    # Time stop
    if datetime.now() - opened > timedelta(hours=TIME_STOP_HOURS):
        await close_position('time_stop', pnl_pct)
        return
    
    # Trailing stop after 1% profit
    if pnl_pct > 0.01:
        trail_stop = pos['highest_price'] * 0.985 if direction == 'long' else pos['highest_price'] * 1.015
        if direction == 'long' and current < trail_stop:
            await close_position('trailing_stop', pnl_pct)
            return
        elif direction == 'short' and current > trail_stop:
            await close_position('trailing_stop', pnl_pct)
            return

async def close_position(reason: str, pnl_pct: float):
    """Close BTC position."""
    if not state.position:
        return

    pos = state.position
    size_usd = pos['size_usd']
    pnl_usd = size_usd * pnl_pct
    
    logger.info(f"📊 Closing BTC position | Reason: {reason} | PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2%})")

    # Update state
    state.balance += size_usd + pnl_usd
    state.daily_pnl += pnl_usd
    state.total_pnl += pnl_usd
    state.trades += 1
    state.history.append({
        'direction': pos['direction'],
        'entry': pos['entry'],
        'exit': pos.get('current_price', pos['entry']),
        'pnl': pnl_usd,
        'pnl_pct': pnl_pct,
        'reason': reason,
        'opened_at': pos['opened_at'],
        'closed_at': datetime.now().isoformat(),
    })
    state.position = None
    state.save()

    logger.info(f"✅ Position closed | Balance: ${state.balance:.2f} | Total PnL: ${state.total_pnl:+.2f}")

# ── MAIN LOOP ──
async def main():
    await init_wallet()
    
    if not wallet_ready:
        logger.error("Cannot start without wallet")
        return

    strategy = BTCHedgeStrategy()
    await strategy.initialize()

    logger.info("=" * 60)
    logger.info("BTC HEDGE BOT STARTED")
    logger.info(f"Balance: ${state.balance:.2f}")
    logger.info("=" * 60)

    while True:
        try:
            # Check for exit on existing position
            await check_exit()
            
            # Check for new entry if no position
            if not state.position:
                signal = await strategy.get_signal(state.balance)
                if signal and signal.confidence >= 60:
                    logger.info(f"🚨 BTC SIGNAL: {signal.direction.upper()} | Confidence: {signal.confidence:.0f}%")
                    logger.info(f"   Entry: ${signal.entry_price:,.0f} | Stop: ${signal.stop_loss:,.0f} | TP: ${signal.take_profit:,.0f}")
                    logger.info(f"   Reason: {signal.reason}")
                    await open_position({
                        'direction': signal.direction,
                        'entry_price': signal.entry_price,
                        'stop_loss': signal.stop_loss,
                        'take_profit': signal.take_profit,
                        'confidence': signal.confidence,
                    })
                else:
                    logger.info("No BTC signal — waiting...")
            else:
                logger.info(f"Holding position | Entry: ${state.position['entry']:,.0f} | Direction: {state.position['direction']}")
            
            # Reset daily PnL at midnight
            if datetime.now().hour == 0 and datetime.now().minute < 5:
                state.reset_daily()
                state.save()

        except Exception as e:
            logger.error(f"Loop error: {e}")

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
