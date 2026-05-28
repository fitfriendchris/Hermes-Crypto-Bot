#!/usr/bin/env python3
"""
Profitability Monitor for Hermes Crypto Bot
Tracks paper trades and calculates win rate, PnL, drawdown
Run: python3 profit_monitor.py
"""

import json
import time
import subprocess
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path("logs/HERMES_CRYPTO_BOT.log")
STATE_FILE = Path("state/HERMES_CRYPTO_STATE.json")
REPORT_FILE = Path("state/profitability_report.json")

def parse_log_for_trades():
    """Extract paper/live trade executions from log."""
    trades = []
    if not LOG_FILE.exists():
        return trades
    
    with open(LOG_FILE, 'r') as f:
        for line in f:
            # Look for COPY EXECUTED lines
            if "COPY EXECUTED" in line or "PAPER BUY" in line or "LIVE BUY" in line:
                parts = line.split('|')
                timestamp = parts[0].strip() if parts else ""
                trades.append({
                    'time': timestamp,
                    'raw': line.strip(),
                    'type': 'copy' if 'COPY' in line else 'direct',
                    'mode': 'paper' if 'PAPER' in line else 'live'
                })
            # Look for SELL lines
            elif "SELL" in line and ("profit" in line.lower() or "loss" in line.lower()):
                parts = line.split('|')
                timestamp = parts[0].strip() if parts else ""
                trades.append({
                    'time': timestamp,
                    'raw': line.strip(),
                    'type': 'exit',
                    'mode': 'paper' if 'PAPER' in line else 'live'
                })
    return trades

def get_current_state():
    """Read current bot state."""
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE, 'r') as f:
        return json.load(f)

def calculate_metrics(state):
    """Calculate trading metrics."""
    positions = state.get('positions', {})
    daily_pnl = state.get('daily_pnl', 0)
    weekly_pnl = state.get('weekly_pnl', 0)
    trades_today = state.get('trades_today', 0)
    balance = state.get('balance', 0)
    
    # Estimate win rate from positions
    winners = sum(1 for p in positions.values() if p.get('current_price', 0) > p.get('entry', 0))
    total = len(positions)
    win_rate = (winners / total * 100) if total > 0 else 0
    
    return {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'balance': balance,
        'positions_count': total,
        'trades_today': trades_today,
        'daily_pnl': daily_pnl,
        'weekly_pnl': weekly_pnl,
        'win_rate_pct': round(win_rate, 1),
        'open_positions': list(positions.keys()),
    }

def main():
    print("=" * 60)
    print("🏛️ HERMES PROFITABILITY MONITOR")
    print("=" * 60)
    
    # Check mode
    state = get_current_state()
    mode = state.get('mode', 'unknown')
    live = state.get('live_mode', False)
    
    print(f"\n📊 Bot Mode: {'LIVE' if live else 'PAPER'}")
    print(f"📊 Trade Mode: {mode}")
    print(f"📊 Balance: ${state.get('balance', 0):.2f}")
    print(f"📊 Positions: {len(state.get('positions', {}))}")
    print(f"📊 Daily PnL: ${state.get('daily_pnl', 0):.2f}")
    print(f"📊 Weekly PnL: ${state.get('weekly_pnl', 0):.2f}")
    print(f"📊 Trades Today: {state.get('trades_today', 0)}")
    
    # Parse log
    trades = parse_log_for_trades()
    print(f"\n📝 Recent Trades from Log: {len(trades)}")
    for t in trades[-10:]:  # Last 10
        print(f"   {t['time']} | {t['type']} | {t['mode']}")
    
    # Calculate metrics
    metrics = calculate_metrics(state)
    
    # Save report
    with open(REPORT_FILE, 'w') as f:
        json.dump(metrics, f, indent=2)
    
    print(f"\n💾 Report saved: {REPORT_FILE}")
    print(f"\n🎯 Win Rate: {metrics['win_rate_pct']}%")
    print(f"🎯 Daily PnL: ${metrics['daily_pnl']:.2f}")
    print(f"🎯 Weekly PnL: ${metrics['weekly_pnl']:.2f}")
    
    print("\n" + "=" * 60)
    print("Run this every 15 min to track profitability")
    print("=" * 60)

if __name__ == "__main__":
    main()
