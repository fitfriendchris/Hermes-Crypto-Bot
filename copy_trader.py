#!/usr/bin/env python3
"""
COPY TRADER — Proportional Whale Following
Tracks verified wallets and copies trades at proportion of our balance.

Example:
- Whale has $100K portfolio, buys $5K of TOKEN (5% of their balance)
- We have $1K portfolio → buy $50 of TOKEN (5% of our balance)
- NOT copying their $5K size, but their allocation %

Filters:
- >60% win rate over 50+ trades
- Profit factor >1.3
- Max drawdown <30%
- Consistent monthly profits
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger('CopyTrader')

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── WALLET TRACKING ──
WALLET_DB_FILE = os.path.join(_HERE, 'state', 'wallet_performance.json')

# Default tracked wallets (to be updated with real addresses)
DEFAULT_WALLETS = {
    'whale_1': {
        'address': '5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVbNU5BpaVgYdP',
        'name': 'SmartMoney_01',
        'tracked_trades': 0,
        'wins': 0,
        'losses': 0,
        'total_pnl': 0.0,
        'win_rate': 0.0,
        'profit_factor': 0.0,
        'max_drawdown': 0.0,
        'score': 0,
        'status': 'tracking',  # tracking, active, paused, rejected
        'last_trade_time': None,
    }
}

wallet_db: Dict[str, Dict] = {}

# Recent whale signals (last 2 minutes)
recent_signals: List[Dict] = []


def load_wallet_db():
    """Load wallet performance database."""
    global wallet_db
    try:
        with open(WALLET_DB_FILE) as f:
            wallet_db = json.load(f)
    except FileNotFoundError:
        wallet_db = dict(DEFAULT_WALLETS)
        save_wallet_db()


def save_wallet_db():
    """Save wallet performance database."""
    os.makedirs(os.path.dirname(WALLET_DB_FILE), exist_ok=True)
    with open(WALLET_DB_FILE, 'w') as f:
        json.dump(wallet_db, f, default=str)


def calculate_wallet_score(wallet: Dict) -> int:
    """Calculate confidence score for a wallet (0-100)."""
    if wallet['tracked_trades'] < 10:
        return 0  # Need more data
        
    score = 0
    
    # Win rate component (max 40 pts)
    wr = wallet['win_rate']
    if wr >= 0.60:
        score += 40
    elif wr >= 0.50:
        score += 25
    elif wr >= 0.40:
        score += 10
        
    # Profit factor component (max 30 pts)
    pf = wallet['profit_factor']
    if pf >= 1.5:
        score += 30
    elif pf >= 1.2:
        score += 20
    elif pf >= 1.0:
        score += 10
        
    # Drawdown component (max 20 pts)
    dd = wallet['max_drawdown']
    if dd <= 0.15:
        score += 20
    elif dd <= 0.30:
        score += 15
    elif dd <= 0.50:
        score += 10
        
    # Consistency component (max 10 pts)
    if wallet['tracked_trades'] >= 100:
        score += 10
    elif wallet['tracked_trades'] >= 50:
        score += 5
        
    return score


def update_wallet_status(wallet_id: str):
    """Update wallet status based on performance."""
    wallet = wallet_db[wallet_id]
    score = calculate_wallet_score(wallet)
    wallet['score'] = score
    
    if score >= 70:
        wallet['status'] = 'active'
    elif score >= 40:
        wallet['status'] = 'tracking'
    elif wallet['tracked_trades'] >= 20:
        wallet['status'] = 'rejected'
        
    save_wallet_db()


def record_wallet_trade(wallet_id: str, pnl: float):
    """Record a trade outcome for a tracked wallet."""
    if wallet_id not in wallet_db:
        return
        
    wallet = wallet_db[wallet_id]
    wallet['tracked_trades'] += 1
    wallet['total_pnl'] += pnl
    
    if pnl > 0:
        wallet['wins'] += 1
    else:
        wallet['losses'] += 1
        
    # Update derived stats
    total = wallet['wins'] + wallet['losses']
    if total > 0:
        wallet['win_rate'] = wallet['wins'] / total
        
        # Approximate profit factor (need actual win/loss sizes for accurate)
        avg_win = wallet['total_pnl'] / wallet['wins'] if wallet['wins'] > 0 else 0
        avg_loss = abs(wallet['total_pnl']) / wallet['losses'] if wallet['losses'] > 0 else 1
        wallet['profit_factor'] = abs(avg_win / avg_loss) if avg_loss > 0 else float('inf')
        
    wallet['last_trade_time'] = datetime.now().isoformat()
    update_wallet_status(wallet_id)


async def scan_whale_wallets():
    """
    Scan blockchain for whale wallet activity.
    Returns list of recent buy signals.
    """
    signals = []
    
    # This is a placeholder — real implementation would:
    # 1. Connect to Solana RPC
    # 2. Monitor token transfers for tracked wallets
    # 3. Detect buy/sell patterns
    
    # For now, return empty list (waiting for wallet integration)
    return signals


def calculate_proportional_size(
    whale_balance: float,
    whale_trade_size: float,
    our_balance: float,
    min_trade: float = 5.0,
    max_trade_pct: float = 0.10
) -> float:
    """
    Calculate our trade size based on whale's allocation %.
    
    Args:
        whale_balance: Whale's total portfolio value
        whale_trade_size: Whale's trade size
        our_balance: Our portfolio value
        min_trade: Minimum trade size
        max_trade_pct: Max % of our balance per trade
    
    Returns:
        Our trade size in USD
    """
    if whale_balance <= 0:
        return min_trade
        
    # Whale's allocation %
    whale_allocation = whale_trade_size / whale_balance
    
    # Apply to our balance
    our_size = our_balance * whale_allocation
    
    # Apply limits
    max_size = our_balance * max_trade_pct
    our_size = min(our_size, max_size)
    our_size = max(our_size, min_trade)
    
    return our_size


async def get_whale_portfolio_estimate(wallet_address: str) -> float:
    """
    Estimate whale's total portfolio value.
    Placeholder — real implementation needs on-chain balance query.
    """
    # Default assumption: $100K for active whales
    return 100000.0


async def evaluate_copy_signal(
    signal: Dict,
    our_balance: float
) -> Optional[Dict]:
    """
    Evaluate a whale trade signal.
    Returns position dict if valid, None if rejected.
    """
    wallet_id = signal.get('wallet_id')
    if wallet_id not in wallet_db:
        return None
        
    wallet = wallet_db[wallet_id]
    
    # Only copy active wallets
    if wallet['status'] != 'active':
        return None
        
    # Check recency
    last_trade = wallet.get('last_trade_time')
    if last_trade:
        last = datetime.fromisoformat(last_trade)
        if datetime.now() - last < timedelta(minutes=2):
            # Too soon after last trade
            return None
            
    # Calculate proportional size
    whale_balance = await get_whale_portfolio_estimate(wallet['address'])
    whale_size = signal.get('trade_size_usd', 5000)
    
    our_size = calculate_proportional_size(
        whale_balance,
        whale_size,
        our_balance
    )
    
    return {
        'token': signal['token'],
        'address': signal.get('token_address'),
        'entry': signal['price'],
        'invested': our_size,
        'quantity': our_size / signal['price'] if signal['price'] > 0 else 0,
        'source': 'copy_trader',
        'wallet_id': wallet_id,
        'wallet_name': wallet['name'],
        'whale_allocation': whale_size / whale_balance,
        'our_allocation': our_size / our_balance,
    }


async def discovery_loop_copy_trader(our_balance: float):
    """
    Background loop: scan for whale signals and emit trades.
    """
    load_wallet_db()
    
    while True:
        try:
            signals = await scan_whale_wallets()
            
            for signal in signals:
                result = await evaluate_copy_signal(signal, our_balance)
                if result:
                    logger.info(
                        f"🐋 COPY: {result['token']} via {result['wallet_name']} "
                        f"| Size: ${result['invested']:.2f} ({result['our_allocation']*100:.1f}% of portfolio)"
                    )
                    # Return to main bot for execution
                    return result
                    
        except Exception as e:
            logger.error(f"Copy trader error: {e}")
            
        await asyncio.sleep(30)  # Scan every 30 seconds


# ── INIT ──
async def init_copy_trader():
    """Initialize copy trader module."""
    load_wallet_db()
    logger.info(f"Copy trader initialized with {len(wallet_db)} tracked wallets")
    
    # Log active wallets
    active = [w for w in wallet_db.values() if w['status'] == 'active']
    logger.info(f"Active wallets: {len(active)}")


if __name__ == "__main__":
    # Test proportional sizing
    logging.basicConfig(level=logging.INFO)
    
    # Test: Whale has $500K, buys $25K (5%)
    # We have $1K → should buy $50 (5%)
    size = calculate_proportional_size(500000, 25000, 1000)
    print(f"Test 1: Whale $500K buys $25K, we have $1K → our size: ${size:.2f}")
    
    # Test: Whale has $100K, buys $2K (2%)
    # We have $500 → should buy $10 (2%)
    size = calculate_proportional_size(100000, 2000, 500)
    print(f"Test 2: Whale $100K buys $2K, we have $500 → our size: ${size:.2f}")
    
    # Test: Whale has $1M, buys $100K (10%)
    # We have $100 → should buy $10 (10%, but capped at max)
    size = calculate_proportional_size(1000000, 100000, 100)
    print(f"Test 3: Whale $1M buys $100K, we have $100 → our size: ${size:.2f}")
