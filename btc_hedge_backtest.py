"""
BTC Edge Backtest — 3 Month Historical Validation
Author: Hermes | May 2026

Backtests the 15m BTC strategy on historical data.
Strategy: Long when RSI < 35 + price <= lower BB
          Short when RSI > 70 + price >= upper BB
Risk: 15% position, 1.5% stop, 3% target, 4h time stop
"""

import json
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('BTCBacktest')

# ── CONFIG ──
POSITION_PCT = 0.15
STOP_PCT = 0.015
TP_PCT = 0.03
TIME_STOP_BARS = 16  # 4 hours = 16 x 15m bars
MAX_POSITIONS = 1
MIN_CONFIDENCE = 60
INITIAL_BALANCE = 1000.0

# ── INDICATORS ──
def calc_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_bollinger(closes: List[float], period: int = 20) -> Dict:
    if len(closes) < period:
        return {'upper': 0, 'middle': 0, 'lower': 0}
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    std = variance ** 0.5
    return {'upper': sma + 2 * std, 'middle': sma, 'lower': sma - 2 * std}

def calc_sma(closes: List[float], period: int = 20) -> float:
    if len(closes) < period:
        return 0.0
    return sum(closes[-period:]) / period

# ── SIGNAL ──
def get_signal(candles: List[Dict]) -> Optional[Dict]:
    """
    Generate BTC trade signal from 15m candles.
    Returns signal dict or None.
    """
    if len(candles) < 20:
        return None
    
    closes = [c['close'] for c in candles]
    current_price = closes[-1]
    prev_price = closes[-2]
    
    rsi = calc_rsi(closes)
    bb = calc_bollinger(closes)
    sma20 = calc_sma(closes)
    
    # Volume proxy
    ranges = [c['high'] - c['low'] for c in candles[-5:-1]]
    avg_range = sum(ranges) / len(ranges) if ranges else 0
    latest_range = candles[-1]['high'] - candles[-1]['low']
    vol_spike = latest_range > avg_range * 1.5
    
    prev_sma = calc_sma(closes[:-1])
    crossed_below_sma = prev_price >= prev_sma and current_price < sma20
    crossed_above_sma = prev_price <= prev_sma and current_price > sma20
    
    direction = None
    confidence = 0
    reasons = []
    
    # LONG: RSI < 35 + price <= lower BB
    if rsi < 35:
        if current_price <= bb['lower']:
            direction = 'long'
            confidence = (35 - rsi) * 2 + 20
            reasons.append(f"RSI oversold ({rsi:.0f})")
            reasons.append(f"price at/below BB lower (${bb['lower']:,.0f})")
        elif crossed_below_sma and vol_spike:
            direction = 'long'
            confidence = (35 - rsi) * 2 + 10
            reasons.append(f"RSI oversold ({rsi:.0f})")
            reasons.append("crossed below SMA20 with volume")
    
    # SHORT: RSI > 70 + price >= upper BB
    if rsi > 70:
        if current_price >= bb['upper']:
            direction = 'short'
            confidence = (rsi - 70) * 2 + 20
            reasons.append(f"RSI overbought ({rsi:.0f})")
            reasons.append(f"price at/above BB upper (${bb['upper']:,.0f})")
        elif crossed_above_sma and vol_spike:
            direction = 'short'
            confidence = (rsi - 70) * 2 + 10
            reasons.append(f"RSI overbought ({rsi:.0f})")
            reasons.append("crossed above SMA20 with volume")
    
    if direction and confidence >= MIN_CONFIDENCE:
        return {
            'direction': direction,
            'confidence': confidence,
            'entry': current_price,
            'rsi': rsi,
            'reason': " | ".join(reasons),
        }
    return None

# ── BACKTEST ENGINE ──
class BacktestEngine:
    def __init__(self, initial_balance: float = INITIAL_BALANCE):
        self.balance = initial_balance
        self.initial = initial_balance
        self.position: Optional[Dict] = None
        self.history: List[Dict] = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak = initial_balance
        
    def open_position(self, signal: Dict, bar_idx: int):
        if self.position:
            return
        
        size = self.balance * POSITION_PCT
        if size < 10:
            return
            
        self.position = {
            'direction': signal['direction'],
            'entry': signal['entry'],
            'size': size,
            'opened_at': bar_idx,
            'highest': signal['entry'],
            'lowest': signal['entry'],
        }
        self.balance -= size
        
    def check_exit(self, candles: List[Dict], bar_idx: int) -> bool:
        if not self.position:
            return False
            
        pos = self.position
        current = candles[bar_idx]['close']
        entry = pos['entry']
        direction = pos['direction']
        size = pos['size']
        
        # Update highest/lowest
        if current > pos['highest']:
            pos['highest'] = current
        if current < pos['lowest']:
            pos['lowest'] = current
        
        # PnL calculation
        if direction == 'long':
            pnl_pct = (current - entry) / entry
        else:
            pnl_pct = (entry - current) / entry
        
        # Stop loss
        if pnl_pct <= -STOP_PCT:
            self.close_position(candles, bar_idx, 'stop_loss', pnl_pct)
            return True
        
        # Take profit
        if pnl_pct >= TP_PCT:
            self.close_position(candles, bar_idx, 'take_profit', pnl_pct)
            return True
        
        # Time stop
        if bar_idx - pos['opened_at'] >= TIME_STOP_BARS:
            self.close_position(candles, bar_idx, 'time_stop', pnl_pct)
            return True
        
        # Trailing stop after 1% profit
        if pnl_pct > 0.01:
            if direction == 'long':
                trail_stop = pos['highest'] * (1 - STOP_PCT)
                if current < trail_stop:
                    self.close_position(candles, bar_idx, 'trailing_stop', pnl_pct)
                    return True
            else:
                trail_stop = pos['lowest'] * (1 + STOP_PCT)
                if current > trail_stop:
                    self.close_position(candles, bar_idx, 'trailing_stop', pnl_pct)
                    return True
        
        return False
    
    def close_position(self, candles: List[Dict], bar_idx: int, reason: str, pnl_pct: float):
        pos = self.position
        size = pos['size']
        pnl = size * pnl_pct
        
        self.balance += size + pnl
        self.total_pnl += pnl
        
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        # Track drawdown
        if self.balance > self.peak:
            self.peak = self.balance
        dd = (self.peak - self.balance) / self.peak
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        
        self.history.append({
            'direction': pos['direction'],
            'entry': pos['entry'],
            'exit': candles[bar_idx]['close'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'hold_bars': bar_idx - pos['opened_at'],
            'balance': self.balance,
        })
        
        self.position = None
        
    def run(self, candles: List[Dict]) -> Dict:
        """Run backtest on historical 15m candles."""
        for i in range(20, len(candles)):
            # Check exit first
            if self.position:
                self.check_exit(candles, i)
            
            # Check entry
            if not self.position:
                window = candles[max(0, i-50):i+1]
                signal = get_signal(window)
                if signal:
                    self.open_position(signal, i)
        
        # Close any open position at end
        if self.position:
            final_price = candles[-1]['close']
            entry = self.position['entry']
            direction = self.position['direction']
            if direction == 'long':
                pnl_pct = (final_price - entry) / entry
            else:
                pnl_pct = (entry - final_price) / entry
            self.close_position(candles, len(candles)-1, 'end_of_data', pnl_pct)
        
        return self.get_stats()
    
    def get_stats(self) -> Dict:
        total_trades = self.wins + self.losses
        win_rate = self.wins / total_trades if total_trades > 0 else 0
        
        pnls = [h['pnl'] for h in self.history]
        avg_win = sum(h['pnl'] for h in self.history if h['pnl'] > 0) / self.wins if self.wins > 0 else 0
        avg_loss = sum(h['pnl'] for h in self.history if h['pnl'] < 0) / self.losses if self.losses > 0 else 0
        
        profit_factor = abs(sum(h['pnl'] for h in self.history if h['pnl'] > 0)) / abs(sum(h['pnl'] for h in self.history if h['pnl'] < 0)) if self.losses > 0 else float('inf')
        
        return {
            'initial_balance': self.initial,
            'final_balance': self.balance,
            'total_pnl': self.total_pnl,
            'total_return_pct': (self.balance - self.initial) / self.initial * 100,
            'total_trades': total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'max_drawdown_pct': self.max_drawdown * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe': self.calc_sharpe(),
        }
    
    def calc_sharpe(self) -> float:
        if len(self.history) < 2:
            return 0.0
        returns = [h['pnl_pct'] for h in self.history]
        avg = sum(returns) / len(returns)
        std = np.std(returns) if len(returns) > 1 else 0.001
        return avg / std if std > 0 else 0.0


# ── DATA FETCHING ──
def fetch_historical_ohlc(days: int = 90) -> List[Dict]:
    """
    Fetch historical BTC 15m candles from CoinGecko.
    Note: CoinGecko free tier only provides ~1 day of 15m data.
    For 3 months, we'd need to aggregate daily data or use a premium API.
    """
    import requests
    
    logger.info(f"Fetching {days} days of BTC OHLC from CoinGecko...")
    
    # CoinGecko free tier: max 365 days of daily data
    # For 15m, we can only get 1 day at a time
    # We'll use daily data and interpolate for backtesting
    url = f"https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days={min(days, 365)}"
    
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            candles = []
            for c in data:
                candles.append({
                    'timestamp': int(c[0]),
                    'open': float(c[1]),
                    'high': float(c[2]),
                    'low': float(c[3]),
                    'close': float(c[4]),
                })
            logger.info(f"Got {len(candles)} daily candles")
            return candles
    except Exception as e:
        logger.error(f"Fetch failed: {e}")
    
    return []


def generate_synthetic_15m_from_daily(daily_candles: List[Dict]) -> List[Dict]:
    """
    Convert daily candles to synthetic 15m candles for backtesting.
    This creates 96 15m candles per day with realistic intraday patterns.
    """
    candles_15m = []
    
    for daily in daily_candles:
        open_p = daily['open']
        high_p = daily['high']
        low_p = daily['low']
        close_p = daily['close']
        ts = daily['timestamp']
        
        # Generate 96 15m candles per day
        # Use a random walk with the daily range as bounds
        np.random.seed(int(ts) % 10000)  # Deterministic for reproducibility
        
        current = open_p
        for i in range(96):
            # Drift toward close
            target = close_p
            drift = (target - current) / (96 - i) if i < 95 else (target - current)
            
            # Random noise
            noise = np.random.normal(0, (high_p - low_p) * 0.02)
            
            # New price
            new_price = current + drift + noise
            new_price = max(low_p, min(high_p, new_price))
            
            # Create 15m candle
            bar_high = max(current, new_price) * (1 + abs(np.random.normal(0, 0.001)))
            bar_low = min(current, new_price) * (1 - abs(np.random.normal(0, 0.001)))
            bar_high = min(high_p, bar_high)
            bar_low = max(low_p, bar_low)
            
            candles_15m.append({
                'timestamp': ts + i * 15 * 60 * 1000,
                'open': current,
                'high': bar_high,
                'low': bar_low,
                'close': new_price,
            })
            
            current = new_price
    
    return candles_15m


# ── MAIN ──
def run_backtest(days: int = 90):
    """Run 3-month backtest on BTC strategy."""
    logger.info("=" * 60)
    logger.info("BTC EDGE BACKTEST — 15m Strategy")
    logger.info("=" * 60)
    logger.info(f"Period: {days} days")
    logger.info(f"Initial Balance: ${INITIAL_BALANCE:,.2f}")
    logger.info(f"Position Size: {POSITION_PCT*100:.0f}%")
    logger.info(f"Stop: {STOP_PCT*100:.1f}% | Target: {TP_PCT*100:.1f}%")
    logger.info("=" * 60)
    
    # Fetch data
    daily = fetch_historical_ohlc(days)
    if not daily:
        logger.error("No data available")
        return
    
    # Convert to 15m
    candles = generate_synthetic_15m_from_daily(daily)
    logger.info(f"Generated {len(candles)} synthetic 15m candles")
    
    # Run backtest
    engine = BacktestEngine()
    stats = engine.run(candles)
    
    # Results
    logger.info("\n" + "=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info(f"Final Balance: ${stats['final_balance']:,.2f}")
    logger.info(f"Total PnL: ${stats['total_pnl']:+.2f} ({stats['total_return_pct']:+.2f}%)")
    logger.info(f"Total Trades: {stats['total_trades']}")
    logger.info(f"Win Rate: {stats['win_rate']*100:.1f}%")
    logger.info(f"Max Drawdown: {stats['max_drawdown_pct']:.2f}%")
    logger.info(f"Avg Win: ${stats['avg_win']:+.2f}")
    logger.info(f"Avg Loss: ${stats['avg_loss']:+.2f}")
    logger.info(f"Profit Factor: {stats['profit_factor']:.2f}")
    logger.info(f"Sharpe Ratio: {stats['sharpe']:.2f}")
    logger.info("=" * 60)
    
    # Monthly breakdown
    if engine.history:
        logger.info("\nLast 10 trades:")
        for h in engine.history[-10:]:
            logger.info(f"  {h['direction']:5s} | Entry: ${h['entry']:,.0f} | Exit: ${h['exit']:,.0f} | PnL: ${h['pnl']:+.2f} ({h['pnl_pct']:+.2%}) | {h['reason']}")
    
    return stats


if __name__ == '__main__':
    run_backtest(90)  # 3 months
