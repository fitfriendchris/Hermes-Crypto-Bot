"""
BTC Multi-Timeframe Backtest — ICT/SMC + Trend Following
Uses 5 years of real daily data (2021-2026).
Author: Hermes | May 2026
"""

import csv
import json
import logging
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('BTCBacktest')

# ── LOAD DATA ──
def load_daily_data(filepath: str = 'data/btc_daily_5y.csv') -> List[Dict]:
    candles = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append({
                'time': int(row['timestamp']),
                'date': datetime.fromtimestamp(int(row['timestamp'])),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume_to']),
            })
    # Sort oldest first
    candles.sort(key=lambda x: x['time'])
    return candles

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

def calc_ema(closes: List[float], period: int = 20) -> float:
    multiplier = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calc_sma(closes: List[float], period: int = 20) -> float:
    return sum(closes[-period:]) / period

def calc_atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period:
        return 0.0
    trs = []
    for i in range(-period, 0):
        c = candles[i]
        prev_close = candles[i-1]['close']
        tr = max(
            c['high'] - c['low'],
            abs(c['high'] - prev_close),
            abs(c['low'] - prev_close),
        )
        trs.append(tr)
    return sum(trs) / len(trs)

def calc_bollinger(closes: List[float], period: int = 20) -> Dict:
    sma = calc_sma(closes, period)
    std = np.std(closes[-period:])
    return {'upper': sma + 2 * std, 'middle': sma, 'lower': sma - 2 * std}

def calc_macd(closes: List[float]) -> Tuple[float, float, float]:
    """Returns (macd_line, signal_line, histogram)."""
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd = ema12 - ema26
    # Signal line = EMA9 of MACD
    macd_history = []
    for i in range(35, len(closes) + 1):
        e12 = calc_ema(closes[:i], 12)
        e26 = calc_ema(closes[:i], 26)
        macd_history.append(e12 - e26)
    signal = calc_ema(macd_history, 9) if len(macd_history) >= 9 else 0
    return macd, signal, macd - signal

def calc_adx(candles: List[Dict], period: int = 14) -> float:
    """Average Directional Index — trend strength."""
    if len(candles) < period * 2:
        return 0.0
    
    plus_dms = []
    minus_dms = []
    trs = []
    
    for i in range(-period*2, 0):
        c = candles[i]
        prev = candles[i-1]
        
        up_move = c['high'] - prev['high']
        down_move = prev['low'] - c['low']
        
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        
        tr = max(c['high'] - c['low'], abs(c['high'] - prev['close']), abs(c['low'] - prev['close']))
        
        plus_dms.append(plus_dm)
        minus_dms.append(minus_dm)
        trs.append(tr)
    
    atr = sum(trs[-period:]) / period
    plus_di = (sum(plus_dms[-period:]) / period) / atr * 100 if atr > 0 else 0
    minus_di = (sum(minus_dms[-period:]) / period) / atr * 100 if atr > 0 else 0
    
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx

# ── ICT/SMC PATTERNS (adapted for daily) ──
def detect_liquidity_sweep(candles: List[Dict], lookback: int = 5) -> Dict:
    """Detect if price swept previous high/low then reversed."""
    if len(candles) < lookback + 2:
        return {'swept_high': False, 'swept_low': False, 'strength': 0}
    
    recent = candles[-lookback-1:-1]
    prev_high = max(c['high'] for c in recent)
    prev_low = min(c['low'] for c in recent)
    current = candles[-1]
    
    swept_high = current['high'] > prev_high and current['close'] < prev_high
    swept_low = current['low'] < prev_low and current['close'] > prev_low
    
    if swept_high:
        strength = (current['high'] - current['close']) / (current['high'] - prev_high)
    elif swept_low:
        strength = (current['close'] - current['low']) / (prev_low - current['low'])
    else:
        strength = 0
    
    return {'swept_high': swept_high, 'swept_low': swept_low, 'strength': min(1.0, strength)}

def detect_order_block(candles: List[Dict], lookback: int = 5) -> Optional[Dict]:
    """Detect last opposite candle before a strong move."""
    if len(candles) < lookback + 3:
        return None
    
    recent = candles[-lookback:]
    move_pct = (recent[-1]['close'] - recent[0]['open']) / recent[0]['open']
    
    if abs(move_pct) < 0.03:  # Need 3%+ move
        return None
    
    is_bullish = move_pct > 0
    
    for i in range(len(recent) - 2, -1, -1):
        c = recent[i]
        is_bearish = c['close'] < c['open']
        is_bullish_candle = c['close'] > c['open']
        
        if is_bullish and is_bearish:
            return {'type': 'bullish', 'high': c['high'], 'low': c['low'], 'open': c['open'], 'close': c['close']}
        elif not is_bullish and is_bullish_candle:
            return {'type': 'bearish', 'high': c['high'], 'low': c['low'], 'open': c['open'], 'close': c['close']}
    
    return None

# ── STRATEGIES ──
class Strategy:
    """Base strategy class."""
    def generate_signal(self, candles: List[Dict]) -> Optional[Dict]:
        raise NotImplementedError

class ICTLiquidityStrategy(Strategy):
    """
    ICT Liquidity Sweep Strategy.
    Long: Price sweeps below previous low, then reverses up (takes out stops, then rallies)
    Short: Price sweeps above previous high, then reverses down (takes out stops, then dumps)
    """
    def __init__(self, lookback: int = 5, min_strength: float = 0.3):
        self.lookback = lookback
        self.min_strength = min_strength
    
    def generate_signal(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < self.lookback + 10:
            return None
        
        closes = [c['close'] for c in candles]
        rsi = calc_rsi(closes)
        adx = calc_adx(candles)
        sweep = detect_liquidity_sweep(candles, self.lookback)
        
        # Need trend strength
        if adx < 20:
            return None  # No trend, skip
        
        if sweep['swept_low'] and sweep['strength'] > self.min_strength and rsi < 50:
            return {
                'direction': 'long',
                'confidence': 50 + sweep['strength'] * 50,
                'reason': f"liquidity sweep low (strength: {sweep['strength']:.0%}), RSI: {rsi:.0f}, ADX: {adx:.0f}",
                'entry': candles[-1]['close'],
                'stop': candles[-1]['low'],
            }
        
        if sweep['swept_high'] and sweep['strength'] > self.min_strength and rsi > 50:
            return {
                'direction': 'short',
                'confidence': 50 + sweep['strength'] * 50,
                'reason': f"liquidity sweep high (strength: {sweep['strength']:.0%}), RSI: {rsi:.0f}, ADX: {adx:.0f}",
                'entry': candles[-1]['close'],
                'stop': candles[-1]['high'],
            }
        
        return None

class TrendFollowingStrategy(Strategy):
    """
    Classic trend following with EMA crossover + ADX filter.
    Long: Price > EMA20, EMA20 > EMA50, ADX > 25
    Short: Price < EMA20, EMA20 < EMA50, ADX > 25
    """
    def generate_signal(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < 60:
            return None
        
        closes = [c['close'] for c in candles]
        current = candles[-1]
        
        ema20 = calc_ema(closes, 20)
        ema50 = calc_ema(closes, 50)
        rsi = calc_rsi(closes)
        adx = calc_adx(candles)
        atr = calc_atr(candles)
        
        # Need strong trend
        if adx < 25:
            return None
        
        price_above_ema20 = current['close'] > ema20
        ema20_above_ema50 = ema20 > ema50
        
        if price_above_ema20 and ema20_above_ema50 and rsi > 50:
            return {
                'direction': 'long',
                'confidence': 60 + (adx - 25),
                'reason': f"trend up | EMA20 > EMA50 | RSI {rsi:.0f} | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': current['close'] - 2 * atr,
            }
        
        if not price_above_ema20 and not ema20_above_ema50 and rsi < 50:
            return {
                'direction': 'short',
                'confidence': 60 + (adx - 25),
                'reason': f"trend down | EMA20 < EMA50 | RSI {rsi:.0f} | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': current['close'] + 2 * atr,
            }
        
        return None

class MeanReversionStrategy(Strategy):
    """
    RSI + Bollinger mean reversion.
    Long: RSI < 30 + price < lower BB
    Short: RSI > 70 + price > upper BB
    """
    def generate_signal(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < 20:
            return None
        
        closes = [c['close'] for c in candles]
        current = candles[-1]
        
        rsi = calc_rsi(closes)
        bb = calc_bollinger(closes)
        adx = calc_adx(candles)
        
        # Avoid ranging markets
        if adx > 30:
            return None  # Strong trend, don't mean revert
        
        if rsi < 30 and current['close'] < bb['lower']:
            return {
                'direction': 'long',
                'confidence': 70 + (30 - rsi),
                'reason': f"mean reversion | RSI {rsi:.0f} | below BB | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': current['low'] * 0.97,
            }
        
        if rsi > 70 and current['close'] > bb['upper']:
            return {
                'direction': 'short',
                'confidence': 70 + (rsi - 70),
                'reason': f"mean reversion | RSI {rsi:.0f} | above BB | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': current['high'] * 1.03,
            }
        
        return None

class BreakoutStrategy(Strategy):
    """
    Volatility breakout — price breaks above/below 20-day range.
    Long: Close > 20-day high, volume above average
    Short: Close < 20-day low, volume above average
    """
    def generate_signal(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < 30:
            return None
        
        recent = candles[-20:]
        current = candles[-1]
        prev = candles[-2]
        
        highest_20 = max(c['high'] for c in recent[:-1])
        lowest_20 = min(c['low'] for c in recent[:-1])
        
        avg_volume = sum(c['volume'] for c in recent[:-1]) / len(recent[:-1])
        current_volume = current['volume']
        
        closes = [c['close'] for c in candles]
        rsi = calc_rsi(closes)
        adx = calc_adx(candles)
        
        if current['close'] > highest_20 and current_volume > avg_volume * 1.2 and adx > 20:
            return {
                'direction': 'long',
                'confidence': 70,
                'reason': f"breakout above 20d high | volume {current_volume/avg_volume:.1f}x | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': lowest_20,
            }
        
        if current['close'] < lowest_20 and current_volume > avg_volume * 1.2 and adx > 20:
            return {
                'direction': 'short',
                'confidence': 70,
                'reason': f"breakout below 20d low | volume {current_volume/avg_volume:.1f}x | ADX {adx:.0f}",
                'entry': current['close'],
                'stop': highest_20,
            }
        
        return None

# ── BACKTEST ENGINE ──
class BacktestEngine:
    def __init__(self, strategy: Strategy, initial_balance: float = 10000.0):
        self.strategy = strategy
        self.balance = initial_balance
        self.initial = initial_balance
        self.position: Optional[Dict] = None
        self.history: List[Dict] = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak = initial_balance
        self.equity_curve: List[Tuple[datetime, float]] = []
        
    def open_position(self, signal: Dict, candle: Dict):
        if self.position:
            return
        
        size = self.balance * 0.20  # 20% position
        if size < 100:
            return
        
        entry = signal['entry']
        stop = signal['stop']
        
        # Calculate position size based on risk
        risk_pct = abs(entry - stop) / entry
        if risk_pct < 0.001:
            return
        
        # Risk 2% of balance per trade
        risk_amount = self.balance * 0.02
        position_size = risk_amount / risk_pct
        position_size = min(position_size, size)
        
        # Target: 2:1 R:R minimum
        if signal['direction'] == 'long':
            target = entry + 2 * abs(entry - stop)
        else:
            target = entry - 2 * abs(entry - stop)
        
        self.position = {
            'direction': signal['direction'],
            'entry': entry,
            'stop': stop,
            'target': target,
            'size': position_size,
            'opened_at': candle['date'],
            'highest': entry,
            'lowest': entry,
            'reason': signal['reason'],
            'confidence': signal['confidence'],
        }
        self.balance -= position_size
        
    def check_exit(self, candle: Dict) -> bool:
        if not self.position:
            return False
        
        pos = self.position
        current = candle['close']
        entry = pos['entry']
        stop = pos['stop']
        target = pos['target']
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
        
        # Stop loss
        if (direction == 'long' and current <= stop) or (direction == 'short' and current >= stop):
            self.close_position(candle, 'stop_loss', pnl_pct)
            return True
        
        # Take profit
        if (direction == 'long' and current >= target) or (direction == 'short' and current <= target):
            self.close_position(candle, 'take_profit', pnl_pct)
            return True
        
        # Trailing stop after 1R profit
        if pnl_pct > 0.01:
            if direction == 'long':
                trail = pos['highest'] * 0.98
                if current < trail:
                    self.close_position(candle, 'trailing_stop', pnl_pct)
                    return True
            else:
                trail = pos['lowest'] * 1.02
                if current > trail:
                    self.close_position(candle, 'trailing_stop', pnl_pct)
                    return True
        
        # Time stop (10 days)
        days_held = (candle['date'] - pos['opened_at']).days
        if days_held >= 10:
            self.close_position(candle, 'time_stop', pnl_pct)
            return True
        
        return False
    
    def close_position(self, candle: Dict, reason: str, pnl_pct: float):
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
            'exit': candle['close'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'days_held': (candle['date'] - pos['opened_at']).days,
            'balance': self.balance,
            'opened_at': pos['opened_at'].strftime('%Y-%m-%d'),
            'closed_at': candle['date'].strftime('%Y-%m-%d'),
            'confidence': pos['confidence'],
        })
        
        self.position = None
        
    def run(self, candles: List[Dict]) -> Dict:
        for i in range(60, len(candles)):
            candle = candles[i]
            
            # Check exit first
            if self.position:
                self.check_exit(candle)
                self.equity_curve.append((candle['date'], self.balance + (self.position['size'] * (candle['close'] - self.position['entry']) / self.position['entry'] if self.position else 0)))
            else:
                self.equity_curve.append((candle['date'], self.balance))
            
            # Check entry
            if not self.position:
                window = candles[max(0, i-60):i+1]
                signal = self.strategy.generate_signal(window)
                if signal and signal['confidence'] >= 60:
                    self.open_position(signal, candle)
        
        # Close any open position at end
        if self.position:
            final_price = candles[-1]['close']
            entry = self.position['entry']
            direction = self.position['direction']
            if direction == 'long':
                pnl_pct = (final_price - entry) / entry
            else:
                pnl_pct = (entry - final_price) / entry
            self.close_position(candles[-1], 'end_of_data', pnl_pct)
        
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
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'max_drawdown_pct': self.max_drawdown * 100,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe': sharpe,
        }


# ── MAIN ──
def run_all_backtests():
    logger.info("=" * 80)
    logger.info("BTC 5-YEAR BACKTEST SUITE")
    logger.info("=" * 80)
    
    # Load data
    candles = load_daily_data()
    logger.info(f"Loaded {len(candles)} daily candles ({candles[0]['date'].date()} to {candles[-1]['date'].date()})")
    
    strategies = {
        'ICT Liquidity Sweep': ICTLiquidityStrategy(),
        'Trend Following (EMA+ADX)': TrendFollowingStrategy(),
        'Mean Reversion (RSI+BB)': MeanReversionStrategy(),
        'Breakout (20d Range)': BreakoutStrategy(),
    }
    
    results = []
    
    for name, strategy in strategies.items():
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Testing: {name}")
        logger.info(f"{'=' * 60}")
        
        engine = BacktestEngine(strategy)
        stats = engine.run(candles)
        
        logger.info(f"Final Balance: ${stats['final_balance']:,.2f}")
        logger.info(f"Total Return: {stats['total_return_pct']:+.2f}%")
        logger.info(f"Trades: {stats['total_trades']} | Wins: {stats['wins']} | Losses: {stats['losses']}")
        logger.info(f"Win Rate: {stats['win_rate']*100:.1f}%")
        logger.info(f"Profit Factor: {stats['profit_factor']:.2f}")
        logger.info(f"Max Drawdown: {stats['max_drawdown_pct']:.2f}%")
        logger.info(f"Avg Win: ${stats['avg_win']:+.2f} | Avg Loss: ${stats['avg_loss']:+.2f}")
        logger.info(f"Sharpe: {stats['sharpe']:.2f}")
        
        results.append({
            'name': name,
            'stats': stats,
            'history': engine.history,
            'equity': engine.equity_curve,
        })
    
    # Summary
    logger.info(f"\n{'=' * 80}")
    logger.info("SUMMARY")
    logger.info(f"{'=' * 80}")
    for r in results:
        s = r['stats']
        logger.info(f"{r['name']:35s} | Return: {s['total_return_pct']:+.1f}% | PF: {s['profit_factor']:.2f} | WR: {s['win_rate']*100:.0f}% | DD: {s['max_drawdown_pct']:.1f}%")
    
    # Save results
    with open('data/backtest_results.json', 'w') as f:
        json.dump([{
            'name': r['name'],
            'stats': r['stats'],
            'history': r['history'],
        } for r in results], f, indent=2, default=str)
    
    # Save equity curves
    with open('data/equity_curves.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['date'] + [r['name'] for r in results])
        
        # Get all dates
        all_dates = results[0]['equity']
        for i, (date, _) in enumerate(all_dates):
            row = [date.strftime('%Y-%m-%d')]
            for r in results:
                if i < len(r['equity']):
                    row.append(f"{r['equity'][i][1]:.2f}")
                else:
                    row.append('')
            writer.writerow(row)
    
    logger.info("\nResults saved to data/backtest_results.json and data/equity_curves.csv")
    
    return results


if __name__ == '__main__':
    run_all_backtests()
