"""
BTC Edge Backtest — Optimized Parameters
Tests multiple parameter combinations to find profitable settings.
"""

import json
import logging
import requests
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('BTCBacktest')

# ── CONFIG GRID ──
PARAM_GRID = {
    'stop_pct': [0.015, 0.02, 0.025, 0.03],
    'tp_pct': [0.02, 0.03, 0.04, 0.05],
    'rsi_oversold': [30, 35, 40],
    'rsi_overbought': [65, 70, 75],
    'bb_threshold': [0.3, 0.5, 0.7],
    'use_trend_filter': [True, False],
}

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

def calc_ema(closes: List[float], period: int = 20) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0
    multiplier = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

# ── SIGNAL ──
def get_signal(candles: List[Dict], params: Dict) -> Optional[Dict]:
    if len(candles) < 20:
        return None
    
    closes = [c['close'] for c in candles]
    current_price = closes[-1]
    
    rsi = calc_rsi(closes)
    bb = calc_bollinger(closes)
    sma20 = calc_sma(closes)
    ema20 = calc_ema(closes)
    
    # Volume proxy
    ranges = [c['high'] - c['low'] for c in candles[-5:-1]]
    avg_range = sum(ranges) / len(ranges) if ranges else 0
    latest_range = candles[-1]['high'] - candles[-1]['low']
    vol_spike = latest_range > avg_range * 1.5
    
    # Trend filter
    trend_up = current_price > ema20 if params.get('use_trend_filter', False) else True
    trend_down = current_price < ema20 if params.get('use_trend_filter', False) else True
    
    direction = None
    confidence = 0
    reasons = []
    
    rsi_os = params['rsi_oversold']
    rsi_ob = params['rsi_overbought']
    bb_thresh = params['bb_threshold']
    
    # LONG
    if rsi < rsi_os and trend_up:
        bb_range = bb['upper'] - bb['lower']
        bb_position = (current_price - bb['lower']) / bb_range if bb_range > 0 else 0.5
        
        if bb_position <= bb_thresh:
            direction = 'long'
            confidence = (rsi_os - rsi) * 2 + 20
            reasons.append(f"RSI oversold ({rsi:.0f} < {rsi_os})")
            reasons.append(f"BB position {bb_position:.0%}")
            if vol_spike:
                confidence += 10
                reasons.append("volume spike")
    
    # SHORT
    if rsi > rsi_ob and trend_down:
        bb_range = bb['upper'] - bb['lower']
        bb_position = (current_price - bb['lower']) / bb_range if bb_range > 0 else 0.5
        
        if bb_position >= (1 - bb_thresh):
            direction = 'short'
            confidence = (rsi - rsi_ob) * 2 + 20
            reasons.append(f"RSI overbought ({rsi:.0f} > {rsi_ob})")
            reasons.append(f"BB position {bb_position:.0%}")
            if vol_spike:
                confidence += 10
                reasons.append("volume spike")
    
    if direction and confidence >= 60:
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
    def __init__(self, params: Dict, initial_balance: float = 1000.0):
        self.params = params
        self.balance = initial_balance
        self.initial = initial_balance
        self.position: Optional[Dict] = None
        self.history: List[Dict] = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak = initial_balance
        
    def check_exit(self, candles: List[Dict], bar_idx: int) -> bool:
        if not self.position:
            return False
            
        pos = self.position
        current = candles[bar_idx]['close']
        entry = pos['entry']
        direction = pos['direction']
        size = pos['size']
        
        if current > pos['highest']:
            pos['highest'] = current
        if current < pos['lowest']:
            pos['lowest'] = current
        
        if direction == 'long':
            pnl_pct = (current - entry) / entry
        else:
            pnl_pct = (entry - current) / entry
        
        stop_pct = self.params['stop_pct']
        tp_pct = self.params['tp_pct']
        
        # Stop loss
        if pnl_pct <= -stop_pct:
            self.close_position(candles, bar_idx, 'stop_loss', pnl_pct)
            return True
        
        # Take profit
        if pnl_pct >= tp_pct:
            self.close_position(candles, bar_idx, 'take_profit', pnl_pct)
            return True
        
        # Time stop (4 hours = 16 bars)
        if bar_idx - pos['opened_at'] >= 16:
            self.close_position(candles, bar_idx, 'time_stop', pnl_pct)
            return True
        
        # Trailing stop after 1% profit
        if pnl_pct > 0.01:
            if direction == 'long':
                trail_stop = pos['highest'] * (1 - stop_pct)
                if current < trail_stop:
                    self.close_position(candles, bar_idx, 'trailing_stop', pnl_pct)
                    return True
            else:
                trail_stop = pos['lowest'] * (1 + stop_pct)
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
        })
        
        self.position = None
        
    def run(self, candles: List[Dict]) -> Dict:
        for i in range(20, len(candles)):
            if self.position:
                self.check_exit(candles, i)
            
            if not self.position:
                window = candles[max(0, i-50):i+1]
                signal = get_signal(window, self.params)
                if signal:
                    size = self.balance * 0.15
                    if size >= 10:
                        self.position = {
                            'direction': signal['direction'],
                            'entry': signal['entry'],
                            'size': size,
                            'opened_at': i,
                            'highest': signal['entry'],
                            'lowest': signal['entry'],
                        }
                        self.balance -= size
        
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
        
        avg_win = sum(h['pnl'] for h in self.history if h['pnl'] > 0) / self.wins if self.wins > 0 else 0
        avg_loss = sum(h['pnl'] for h in self.history if h['pnl'] < 0) / self.losses if self.losses > 0 else 0
        
        wins_sum = sum(h['pnl'] for h in self.history if h['pnl'] > 0)
        losses_sum = abs(sum(h['pnl'] for h in self.history if h['pnl'] < 0))
        profit_factor = wins_sum / losses_sum if losses_sum > 0 else float('inf')
        
        returns = [h['pnl_pct'] for h in self.history]
        sharpe = np.mean(returns) / np.std(returns) if len(returns) > 1 and np.std(returns) > 0 else 0
        
        return {
            'final_balance': self.balance,
            'total_return_pct': (self.balance - self.initial) / self.initial * 100,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'max_drawdown_pct': self.max_drawdown * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe': sharpe,
            'wins': self.wins,
            'losses': self.losses,
        }


# ── DATA FETCHING ──
def fetch_data():
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?vs_currency=usd&days=365"
    resp = requests.get(url, timeout=30)
    data = resp.json()
    
    daily = []
    for c in data:
        daily.append({'timestamp': int(c[0]), 'open': float(c[1]), 'high': float(c[2]), 'low': float(c[3]), 'close': float(c[4])})
    
    candles = []
    for d in daily:
        np.random.seed(int(d['timestamp']) % 10000)
        current = d['open']
        for i in range(96):
            target = d['close']
            drift = (target - current) / (96 - i) if i < 95 else (target - current)
            noise = np.random.normal(0, (d['high'] - d['low']) * 0.02)
            new_price = max(d['low'], min(d['high'], current + drift + noise))
            bar_high = min(d['high'], max(current, new_price) * (1 + abs(np.random.normal(0, 0.001))))
            bar_low = max(d['low'], min(current, new_price) * (1 - abs(np.random.normal(0, 0.001))))
            candles.append({'timestamp': d['timestamp'] + i * 15*60*1000, 'open': current, 'high': bar_high, 'low': bar_low, 'close': new_price})
            current = new_price
    
    return candles


# ── GRID SEARCH ──
def grid_search(candles: List[Dict]):
    best = None
    best_score = -999
    results = []
    
    total_combos = len(PARAM_GRID['stop_pct']) * len(PARAM_GRID['tp_pct']) * len(PARAM_GRID['rsi_oversold']) * len(PARAM_GRID['rsi_overbought']) * len(PARAM_GRID['bb_threshold']) * len(PARAM_GRID['use_trend_filter'])
    logger.info(f"Testing {total_combos} parameter combinations...")
    
    for stop_pct in PARAM_GRID['stop_pct']:
        for tp_pct in PARAM_GRID['tp_pct']:
            for rsi_oversold in PARAM_GRID['rsi_oversold']:
                for rsi_overbought in PARAM_GRID['rsi_overbought']:
                    for bb_threshold in PARAM_GRID['bb_threshold']:
                        for use_trend_filter in PARAM_GRID['use_trend_filter']:
                            params = {
                                'stop_pct': stop_pct,
                                'tp_pct': tp_pct,
                                'rsi_oversold': rsi_oversold,
                                'rsi_overbought': rsi_overbought,
                                'bb_threshold': bb_threshold,
                                'use_trend_filter': use_trend_filter,
                            }
                            
                            engine = BacktestEngine(params)
                            stats = engine.run(candles)
                            
                            # Score: return * profit_factor / max_drawdown
                            score = 0
                            if stats['max_drawdown_pct'] > 0:
                                score = (stats['total_return_pct'] * stats['profit_factor']) / (stats['max_drawdown_pct'] + 1)
                            else:
                                score = stats['total_return_pct'] * stats['profit_factor']
                            
                            results.append({
                                'params': params,
                                'stats': stats,
                                'score': score,
                            })
                            
                            if score > best_score and stats['total_trades'] >= 10:
                                best_score = score
                                best = {'params': params, 'stats': stats, 'score': score}
    
    # Sort by score
    results.sort(key=lambda x: x['score'], reverse=True)
    
    logger.info("\n" + "=" * 60)
    logger.info("TOP 5 PARAMETER COMBINATIONS")
    logger.info("=" * 60)
    
    for i, r in enumerate(results[:5]):
        p = r['params']
        s = r['stats']
        logger.info(f"\n#{i+1} | Score: {r['score']:.2f}")
        logger.info(f"  Stop: {p['stop_pct']*100:.1f}% | TP: {p['tp_pct']*100:.1f}%")
        logger.info(f"  RSI: {p['rsi_oversold']}/{p['rsi_overbought']} | BB: {p['bb_threshold']:.0%} | Trend: {p['use_trend_filter']}")
        logger.info(f"  Return: {s['total_return_pct']:+.2f}% | Trades: {s['total_trades']} | WR: {s['win_rate']*100:.1f}%")
        logger.info(f"  PF: {s['profit_factor']:.2f} | DD: {s['max_drawdown_pct']:.2f}% | Sharpe: {s['sharpe']:.2f}")
    
    return best


# ── MAIN ──
def main():
    logger.info("Fetching BTC data...")
    candles = fetch_data()
    logger.info(f"Got {len(candles)} 15m candles")
    
    logger.info("Running grid search...")
    best = grid_search(candles)
    
    if best:
        logger.info("\n" + "=" * 60)
        logger.info("BEST PARAMETERS")
        logger.info("=" * 60)
        p = best['params']
        s = best['stats']
        logger.info(f"Stop: {p['stop_pct']*100:.1f}%")
        logger.info(f"Target: {p['tp_pct']*100:.1f}%")
        logger.info(f"RSI Oversold: {p['rsi_oversold']}")
        logger.info(f"RSI Overbought: {p['rsi_overbought']}")
        logger.info(f"BB Threshold: {p['bb_threshold']:.0%}")
        logger.info(f"Trend Filter: {p['use_trend_filter']}")
        logger.info(f"\nReturn: {s['total_return_pct']:+.2f}%")
        logger.info(f"Trades: {s['total_trades']}")
        logger.info(f"Win Rate: {s['win_rate']*100:.1f}%")
        logger.info(f"Profit Factor: {s['profit_factor']:.2f}")
        logger.info(f"Max Drawdown: {s['max_drawdown_pct']:.2f}%")
        logger.info(f"Sharpe: {s['sharpe']:.2f}")
        logger.info("=" * 60)


if __name__ == '__main__':
    main()
