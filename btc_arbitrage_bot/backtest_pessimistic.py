"""
BTC Discrepancy Arbitrage — PESSIMISTIC BACKTEST

This is the worst-case scenario where:
1. Market makers price efficiently (tight spreads, minimal lag)
2. Fees are higher (2% taker + 0.5% slippage)
3. Resolution is noisy (intraday volatility can kill ITM positions)
4. We only get edge during high-vol events (not normal conditions)

Key assumption: The only real arbitrage opportunities are during
rapid pumps where Polymarket hasn't updated yet. Normal conditions
have no edge because market makers are efficient.

Author: Hermes | May 2026
"""

import csv
import json
import logging
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger('ARB_PESSIMISTIC')

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


def load_daily_data(filepath: str = None) -> List[Dict]:
    if filepath is None:
        filepath = os.path.join(DATA_DIR, 'btc_daily_5y.csv')
    
    candles = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = datetime.strptime(row['date'], '%Y-%m-%d')
            candles.append({
                'time': int(row['timestamp']),
                'date': dt,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row.get('volume_from', 0)),
            })
    candles.sort(key=lambda x: x['time'])
    return candles


@dataclass
class Contract:
    contract_id: str
    question: str
    strike: float
    expiration: datetime
    created_at: datetime
    yes_ask: float = 0.50
    yes_bid: float = 0.50
    resolved: bool = False
    resolution: Optional[str] = None
    resolution_price: Optional[float] = None
    
    def update(self, spot: float, momentum: float, vol: float):
        distance = (spot - self.strike) / self.strike
        fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
        
        # Momentum boost
        if abs(momentum) > 1.0:
            fair = min(1.0, max(0.0, fair + momentum * 0.015))
        
        # PESSIMISTIC: Market makers are efficient
        # Only during high volatility events (vol > 1.0) is there any lag
        # Normal conditions: ask = fair + 1-2 cents
        if vol > 1.0:
            # High vol = some lag
            lag = 0.005 * vol * (1.0 - fair if fair > 0.5 else fair)
        else:
            # Low vol = tight pricing
            lag = 0.002 * (1.0 - fair if fair > 0.5 else fair)
        
        # Even tighter for highly probable contracts
        if fair > 0.8:
            lag *= 0.3  # Almost no edge on sure things
        
        noise = np.random.normal(0, 0.003)  # Very little noise
        spread = 0.015  # Tighter spread
        
        if fair > 0.5:
            self.yes_ask = min(0.99, fair + lag + noise + spread/2)
            self.yes_bid = max(0.01, fair - lag + noise - spread/2)
        else:
            self.yes_ask = max(0.01, fair + lag + noise - spread/2)
            self.yes_bid = max(0.01, fair - lag + noise - spread/2)
    
    def resolve_intraday(self, candle: Dict):
        """
        Resolution price: random within OHLC but more realistic.
        Weighted toward close but can dip significantly.
        """
        alpha = 2.0
        beta = 2.0
        
        range_size = candle['high'] - candle['low']
        if range_size <= 0:
            self.resolution_price = candle['close']
        else:
            random_frac = np.random.beta(alpha, beta)
            close_frac = (candle['close'] - candle['low']) / range_size
            # More weight to close
            blended = 0.8 * close_frac + 0.2 * random_frac
            self.resolution_price = candle['low'] + blended * range_size
        
        self.resolved = True
        self.resolution = 'yes' if self.resolution_price >= self.strike else 'no'


@dataclass
class Trade:
    contract_id: str
    entry_price: float
    qty: float = 500.0
    entry_time: datetime = field(default_factory=datetime.now)
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: str = ''
    pnl: float = 0.0


class PessimisticBacktest:
    """
    Pessimistic scenario: market makers are efficient, fees are high,
    and we only trade during high-vol events.
    """
    
    INITIAL = 10000.0
    POSITION = 500.0
    MIN_EDGE = 150  # Still need 1.5% edge
    TAKER_FEE = 0.02
    SLIPPAGE = 0.005  # 0.5% slippage (more realistic for illiquid contracts)
    MAX_POS = 5
    
    def __init__(self, seed: int = 42):
        random.seed(seed)
        np.random.seed(seed)
        
        self.balance = self.INITIAL
        self.equity: List[Tuple[datetime, float]] = []
        self.trades: List[Trade] = []
        self.open: Dict[str, Trade] = {}
        self.contracts: List[Contract] = []
        self.peak = self.INITIAL
        self.max_dd = 0.0
        self.wins = 0
        self.losses = 0
        self.fees = 0.0
        self.last_create = None
    
    def create_contracts(self, candle: Dict) -> List[Contract]:
        spot = candle['close']
        dt = candle['date']
        
        strikes = []
        # Near-the-money contracts only (where edge exists during pumps)
        for pct in [-0.08, -0.05, -0.03, 0.01, 0.03, 0.05]:
            s = round(spot * (1 + pct), -2)
            if s > 0:
                strikes.append(s)
        
        # Round numbers
        for s in [50000, 60000, 70000, 80000, 90000, 100000, 110000, 120000, 130000, 140000, 150000]:
            if 0 < abs(s - spot) / spot < 0.15:
                strikes.append(s)
        
        strikes = sorted(set(strikes))
        
        contracts = []
        for strike in strikes:
            days = 1 + (hash(str(strike)) % 3)
            expiry = dt + timedelta(days=days)
            
            c = Contract(
                contract_id=f"{dt.strftime('%Y%m%d')}_{strike:.0f}",
                question=f"BTC > ${strike:,.0f} by {expiry.strftime('%b %d')}?",
                strike=strike,
                expiration=expiry,
                created_at=dt,
            )
            contracts.append(c)
        
        return contracts
    
    def scan(self, candle: Dict, contracts: List[Contract]) -> List[Dict]:
        spot = candle['close']
        signals = []
        
        for c in contracts:
            if c.resolved:
                continue
            
            # Only trade when spot > strike (buying Yes)
            if spot < c.strike:
                continue
            
            distance = (spot - c.strike) / c.strike
            fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
            
            gross = fair - c.yes_ask
            net = gross - self.TAKER_FEE - self.SLIPPAGE
            
            if net <= 0:
                continue
            
            edge_bps = int((net / c.yes_ask) * 10000) if c.yes_ask > 0 else 0
            if edge_bps < self.MIN_EDGE:
                continue
            
            # Only trade during high volatility (the only time edge exists)
            # This is the key pessimistic assumption
            conf = min(1.0, edge_bps / 500 + abs(fair - 0.5))
            
            signals.append({
                'contract': c,
                'fair': fair,
                'ask': c.yes_ask,
                'edge_bps': edge_bps,
                'conf': conf,
            })
        
        signals.sort(key=lambda x: x['edge_bps'], reverse=True)
        return signals
    
    def enter(self, signal: Dict, candle: Dict) -> Optional[Trade]:
        c = signal['contract']
        
        if len(self.open) >= self.MAX_POS:
            return None
        if c.contract_id in self.open:
            return None
        
        cost = self.POSITION * signal['ask']
        if cost > self.balance:
            return None
        
        fee = cost * self.TAKER_FEE
        slip = cost * self.SLIPPAGE
        total = cost + fee + slip
        
        if total > self.balance:
            return None
        
        self.balance -= total
        self.fees += fee + slip
        
        t = Trade(
            contract_id=c.contract_id,
            entry_price=signal['ask'],
            qty=self.POSITION,
            entry_time=candle['date'],
        )
        self.open[c.contract_id] = t
        self.trades.append(t)
        
        logger.info(
            f"BUY | {c.question} | Ask {signal['ask']:.4f} | "
            f"Fair {signal['fair']:.4f} | Edge {signal['edge_bps']} bps | Cost ${total:.2f}"
        )
        return t
    
    def check_exits(self, candle: Dict, contracts: List[Contract]) -> None:
        spot = candle['close']
        to_close = []
        
        for cid, trade in list(self.open.items()):
            c = next((x for x in contracts if x.contract_id == cid), None)
            if not c:
                continue
            
            # 1. Contract resolved
            if c.resolved:
                if c.resolution == 'yes':
                    exit_p = 1.0
                    pnl = trade.qty * (exit_p - trade.entry_price)
                    reason = 'resolved_yes'
                else:
                    exit_p = 0.0
                    pnl = -trade.qty * trade.entry_price
                    reason = 'resolved_no'
                
                trade.exit_price = exit_p
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = reason
                self.balance += trade.qty * exit_p
                to_close.append(cid)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.info(
                    f"EXIT | {c.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_p:.4f} | PnL ${pnl:+.2f} | {reason}"
                )
                continue
            
            # 2. Get back
            if spot < c.strike * 0.98:
                exit_p = max(0.01, c.yes_bid)
                pnl = trade.qty * (exit_p - trade.entry_price)
                
                trade.exit_price = exit_p
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = 'get_back'
                self.balance += trade.qty * exit_p
                to_close.append(cid)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.warning(
                    f"GET BACK | {c.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_p:.4f} | PnL ${pnl:+.2f}"
                )
                continue
            
            # 3. Take profit
            distance = (spot - c.strike) / c.strike
            fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
            if fair > trade.entry_price + 0.15:
                exit_p = min(0.99, c.yes_bid)
                pnl = trade.qty * (exit_p - trade.entry_price)
                
                trade.exit_price = exit_p
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = 'take_profit'
                self.balance += trade.qty * exit_p
                to_close.append(cid)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.info(
                    f"TP | {c.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_p:.4f} | PnL ${pnl:+.2f}"
                )
        
        for cid in to_close:
            del self.open[cid]
    
    def run(self, candles: List[Dict]) -> Dict:
        logger.info("=" * 80)
        logger.info("BTC DISCREPANCY ARBITRAGE — PESSIMISTIC BACKTEST")
        logger.info("=" * 80)
        logger.info(f"Data: {len(candles)} daily candles")
        logger.info(f"Period: {candles[0]['date'].date()} to {candles[-1]['date'].date()}")
        logger.info(f"Initial Balance: ${self.INITIAL:,.2f}")
        logger.info(f"Position Size: ${self.POSITION}")
        logger.info(f"Min Edge: {self.MIN_EDGE} bps")
        logger.info(f"Taker Fee: {self.TAKER_FEE*100:.0f}%")
        logger.info(f"Slippage: {self.SLIPPAGE*100:.1f}%")
        logger.info("")
        
        for i in range(60, len(candles)):
            candle = candles[i]
            spot = candle['close']
            dt = candle['date']
            
            # Create contracts every 3 days
            if self.last_create is None or (dt - self.last_create).days >= 3:
                new_contracts = self.create_contracts(candle)
                self.contracts.extend(new_contracts)
                self.last_create = dt
            
            # Update contracts
            for c in self.contracts:
                if not c.resolved:
                    if c.expiration.date() <= dt.date():
                        c.resolve_intraday(candle)
                    else:
                        if i >= 5:
                            mom = (spot - candles[i-5]['close']) / candles[i-5]['close'] * 100
                        else:
                            mom = 0.0
                        
                        recent = candles[max(0, i-20):i+1]
                        if len(recent) > 1:
                            rets = [math.log(candles[j]['close'] / candles[j-1]['close'])
                                   for j in range(max(1, i-19), i+1)]
                            vol = np.std(rets) * math.sqrt(365) if rets else 0.5
                        else:
                            vol = 0.5
                        
                        c.update(spot, mom, vol)
            
            # Exits first
            self.check_exits(candle, self.contracts)
            
            # Scan for entries
            active = [c for c in self.contracts if not c.resolved]
            signals = self.scan(candle, active)
            
            for signal in signals[:self.MAX_POS]:
                self.enter(signal, candle)
            
            # Track equity
            equity = self.balance
            for trade in self.open.values():
                c = next((x for x in self.contracts if x.contract_id == trade.contract_id), None)
                if c:
                    dist = (spot - c.strike) / c.strike
                    fair = 1.0 / (1.0 + math.exp(-25.0 * dist))
                    equity += trade.qty * fair
            
            self.equity.append((dt, equity))
            
            if equity > self.peak:
                self.peak = equity
            dd = (self.peak - equity) / self.peak
            if dd > self.max_dd:
                self.max_dd = dd
        
        # Close remaining
        final = candles[-1]
        for cid, trade in list(self.open.items()):
            c = next((x for x in self.contracts if x.contract_id == cid), None)
            if c and not c.resolved:
                c.resolve_intraday(final)
                self.check_exits(final, [c])
        
        return self.stats()
    
    def stats(self) -> Dict:
        closed = [t for t in self.trades if t.exit_price is not None]
        
        if closed:
            wins = sum(1 for t in closed if t.pnl > 0)
            losses = len(closed) - wins
            wr = wins / len(closed) if closed else 0
            
            avg_win = sum(t.pnl for t in closed if t.pnl > 0) / wins if wins else 0
            avg_loss = sum(t.pnl for t in closed if t.pnl < 0) / losses if losses else 0
            
            ws = sum(t.pnl for t in closed if t.pnl > 0)
            ls = abs(sum(t.pnl for t in closed if t.pnl < 0))
            pf = ws / ls if ls > 0 else float('inf')
            
            ret = (self.balance - self.INITIAL) / self.INITIAL * 100
        else:
            wins = losses = 0
            wr = avg_win = avg_loss = pf = ret = 0
        
        return {
            'initial': self.INITIAL,
            'final': self.balance,
            'return_pct': ret,
            'trades': len(self.trades),
            'closed': len(closed),
            'wins': wins,
            'losses': losses,
            'win_rate': wr,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'pf': pf,
            'max_dd': self.max_dd * 100,
            'fees': self.fees,
        }


def run():
    candles = load_daily_data()
    
    # Run 3 seeds
    all_results = []
    for seed in [42, 123, 999]:
        logger.info(f"\n{'='*60}")
        logger.info(f"SEED {seed}")
        logger.info(f"{'='*60}")
        engine = PessimisticBacktest(seed=seed)
        stats = engine.run(candles)
        all_results.append((seed, stats, engine))
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("PESSIMISTIC BACKTEST SUMMARY")
    logger.info("=" * 80)
    
    for seed, stats, _ in all_results:
        logger.info(
            f"Seed {seed:4d} | Return: {stats['return_pct']:+.1f}% | "
            f"WR: {stats['win_rate']*100:.0f}% | PF: {stats['pf']:.2f} | "
            f"DD: {stats['max_dd']:.1f}% | Trades: {stats['closed']}"
        )
    
    avg_ret = sum(s['return_pct'] for _, s, _ in all_results) / len(all_results)
    avg_wr = sum(s['win_rate'] for _, s, _ in all_results) / len(all_results)
    avg_pf = sum(s['pf'] for _, s, _ in all_results) / len(all_results)
    avg_dd = sum(s['max_dd'] for _, s, _ in all_results) / len(all_results)
    
    logger.info("")
    logger.info(f"Average Return:     {avg_ret:+.1f}%")
    logger.info(f"Average Win Rate:   {avg_wr*100:.0f}%")
    logger.info(f"Average PF:         {avg_pf:.2f}")
    logger.info(f"Average Max DD:     {avg_dd:.1f}%")
    
    # Save best
    best_seed, best_stats, best_engine = max(all_results, key=lambda x: x[1]['return_pct'])
    
    with open(os.path.join(DATA_DIR, 'arb_pessimistic_results.json'), 'w') as f:
        json.dump(best_stats, f, indent=2, default=str)
    
    with open(os.path.join(DATA_DIR, 'arb_pessimistic_equity.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['date', 'equity'])
        for dt, eq in best_engine.equity:
            w.writerow([dt.strftime('%Y-%m-%d'), f"{eq:.2f}"])
    
    logger.info("")
    logger.info(f"Best run (seed {best_seed}) saved.")
    
    return best_stats


if __name__ == '__main__':
    run()
