"""
BTC Discrepancy Arbitrage — HOURLY BACKTEST ENGINE
Uses real 1h BTC data for more realistic intraday arbitrage simulation.

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
from decimal import Decimal
from typing import List, Dict, Optional, Tuple

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger('ARB_BACKTEST_HOURLY')

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


# ──────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────────────────────────────────

def load_hourly_data(filepath: str = None) -> List[Dict]:
    if filepath is None:
        filepath = os.path.join(DATA_DIR, 'btc_1h.csv')
    
    candles = []
    with open(filepath) as f:
        reader = csv.DictReader(f)
        for row in reader:
            dt = datetime.fromisoformat(row['timestamp'])
            candles.append({
                'time': dt.timestamp(),
                'date': dt,
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row.get('volume_from', 0)),
            })
    candles.sort(key=lambda x: x['time'])
    return candles


# ──────────────────────────────────────────────────────────────────────────
# CONTRACT SIMULATOR
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class HourlyContract:
    """Represents a Polymarket-style binary contract on hourly BTC data."""
    contract_id: str
    question: str
    strike: float
    expiration: datetime
    created_at: datetime
    
    yes_ask: float = 0.50
    yes_bid: float = 0.50
    
    resolved: bool = False
    resolution: Optional[str] = None
    
    def update_from_spot(self, spot_price: float, momentum: float, volatility: float):
        """Update simulated order book from current spot price."""
        distance = (spot_price - self.strike) / self.strike
        fair_prob = 1.0 / (1.0 + math.exp(-25.0 * distance))
        
        # Momentum boost
        if abs(momentum) > 1.0:
            if momentum > 0:
                fair_prob = min(1.0, fair_prob + momentum * 0.02)
            else:
                fair_prob = max(0.0, fair_prob + momentum * 0.02)
        
        # Simulated lag: larger in high volatility
        lag_factor = 0.015 * volatility * (1.0 - fair_prob if fair_prob > 0.5 else fair_prob)
        noise = np.random.normal(0, 0.015)
        spread = 0.02
        
        if fair_prob > 0.5:
            self.yes_ask = min(0.99, fair_prob + lag_factor + noise + spread/2)
            self.yes_bid = max(0.01, fair_prob - lag_factor + noise - spread/2)
        else:
            self.yes_ask = max(0.01, fair_prob + lag_factor + noise - spread/2)
            self.yes_bid = max(0.01, fair_prob - lag_factor + noise - spread/2)
    
    def resolve(self, final_price: float):
        self.resolved = True
        self.resolution = 'yes' if final_price >= self.strike else 'no'


@dataclass
class Trade:
    contract_id: str
    direction: str
    entry_price: float
    exit_price: Optional[float] = None
    qty: float = 1.0
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    exit_reason: str = ''
    pnl: float = 0.0


class HourlyArbitrageBacktest:
    """
    Hourly backtest for discrepancy arbitrage.
    More realistic than daily — contracts resolve within hours, not days.
    """
    
    INITIAL_BALANCE = 10000.0
    POSITION_SIZE = 500.0
    MIN_EDGE_BPS = 150
    TAKER_FEE = 0.02
    SLIPPAGE = 0.001
    MAX_POSITIONS = 5
    
    def __init__(self):
        self.balance = self.INITIAL_BALANCE
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trades: List[Trade] = []
        self.open_positions: Dict[str, Trade] = {}
        self.contracts: List[HourlyContract] = []
        self.max_drawdown = 0.0
        self.peak = self.INITIAL_BALANCE
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
        self.last_contract_creation = None
    
    def create_contracts(self, candle: Dict) -> List[HourlyContract]:
        """Create contracts that expire 4-8 hours from now."""
        spot = candle['close']
        dt = candle['date']
        
        strikes = []
        # In-the-money strikes (spot > strike, we buy Yes)
        for pct in [-0.12, -0.08, -0.05, -0.03]:
            strike = round(spot * (1 + pct), -2)
            if 0 < strike < spot:
                strikes.append(strike)
        
        # Near-the-money (slightly above, for when spot pumps)
        for pct in [0.01, 0.03, 0.05]:
            strike = round(spot * (1 + pct), -2)
            if strike > spot:
                strikes.append(strike)
        
        # Round numbers
        for strike in [70000, 80000, 90000, 100000, 110000, 120000]:
            if abs(strike - spot) / spot < 0.15:
                strikes.append(strike)
        
        strikes = sorted(set(strikes))
        
        contracts = []
        for strike in strikes:
            # Expire 4-8 hours from now
            hours_to_expiry = 4 + (hash(str(strike) + str(dt)) % 5)
            expiration = dt + timedelta(hours=hours_to_expiry)
            
            contract = HourlyContract(
                contract_id=f"{dt.strftime('%Y%m%d%H')}_{strike:.0f}",
                question=f"Will BTC > ${strike:,.0f} by {expiration.strftime('%H:%M')}?",
                strike=strike,
                expiration=expiration,
                created_at=dt,
            )
            contracts.append(contract)
        
        return contracts
    
    def scan_opportunities(self, candle: Dict, contracts: List[HourlyContract]) -> List[Dict]:
        spot = candle['close']
        signals = []
        
        for contract in contracts:
            if contract.resolved:
                continue
            
            # Only buy Yes when spot > strike
            if spot < contract.strike:
                continue
            
            distance = (spot - contract.strike) / contract.strike
            fair_prob = 1.0 / (1.0 + math.exp(-25.0 * distance))
            
            gross_edge = fair_prob - contract.yes_ask
            net_edge = gross_edge - self.TAKER_FEE - self.SLIPPAGE
            
            if net_edge <= 0:
                continue
            
            edge_bps = int((net_edge / contract.yes_ask) * 10000) if contract.yes_ask > 0 else 0
            if edge_bps < self.MIN_EDGE_BPS:
                continue
            
            confidence = min(1.0, edge_bps / 500 + abs(fair_prob - 0.5))
            
            signals.append({
                'contract': contract,
                'fair_prob': fair_prob,
                'yes_ask': contract.yes_ask,
                'net_edge': net_edge,
                'edge_bps': edge_bps,
                'confidence': confidence,
                'spot': spot,
            })
        
        signals.sort(key=lambda x: x['edge_bps'], reverse=True)
        return signals
    
    def execute_signal(self, signal: Dict, candle: Dict) -> Optional[Trade]:
        contract = signal['contract']
        
        if len(self.open_positions) >= self.MAX_POSITIONS:
            return None
        if contract.contract_id in self.open_positions:
            return None
        
        cost = self.POSITION_SIZE * signal['yes_ask']
        if cost > self.balance:
            return None
        
        fee = cost * self.TAKER_FEE
        slippage = cost * self.SLIPPAGE
        total_cost = cost + fee + slippage
        
        if total_cost > self.balance:
            return None
        
        self.balance -= total_cost
        self.total_fees += fee + slippage
        
        trade = Trade(
            contract_id=contract.contract_id,
            direction='buy_yes',
            entry_price=signal['yes_ask'],
            qty=self.POSITION_SIZE,
            entry_time=candle['date'],
        )
        
        self.open_positions[contract.contract_id] = trade
        self.trades.append(trade)
        
        logger.info(
            f"BUY | {contract.question} | "
            f"Ask {signal['yes_ask']:.4f} | Fair {signal['fair_prob']:.4f} | "
            f"Edge {signal['edge_bps']} bps | Cost ${total_cost:.2f}"
        )
        return trade
    
    def check_exits(self, candle: Dict, contracts: List[HourlyContract]) -> None:
        spot = candle['close']
        to_close = []
        
        for contract_id, trade in list(self.open_positions.items()):
            contract = next((c for c in contracts if c.contract_id == contract_id), None)
            if not contract:
                continue
            
            # 1. Contract resolved
            if contract.resolved:
                if contract.resolution == 'yes':
                    exit_price = 1.0
                    pnl = trade.qty * (exit_price - trade.entry_price)
                    trade.exit_reason = 'resolved_yes'
                else:
                    exit_price = 0.0
                    pnl = -trade.qty * trade.entry_price
                    trade.exit_reason = 'resolved_no'
                
                trade.exit_price = exit_price
                trade.exit_time = candle['date']
                trade.pnl = pnl
                self.balance += trade.qty * exit_price
                to_close.append(contract_id)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.info(
                    f"EXIT | {contract.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | PnL ${pnl:+.2f} | {trade.exit_reason}"
                )
                continue
            
            # 2. Get back — price dropped below strike
            if spot < contract.strike * 0.98:
                exit_price = max(0.01, contract.yes_bid)
                pnl = trade.qty * (exit_price - trade.entry_price)
                
                trade.exit_price = exit_price
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = 'get_back'
                self.balance += trade.qty * exit_price
                to_close.append(contract_id)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.warning(
                    f"GET BACK | {contract.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | PnL ${pnl:+.2f}"
                )
                continue
            
            # 3. Take profit
            distance = (spot - contract.strike) / contract.strike
            current_fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
            if current_fair > trade.entry_price + 0.10:
                exit_price = min(0.99, contract.yes_bid)
                pnl = trade.qty * (exit_price - trade.entry_price)
                
                trade.exit_price = exit_price
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = 'take_profit'
                self.balance += trade.qty * exit_price
                to_close.append(contract_id)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.info(
                    f"TP | {contract.question} | Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | PnL ${pnl:+.2f}"
                )
        
        for cid in to_close:
            del self.open_positions[cid]
    
    def run(self, candles: List[Dict]) -> Dict:
        logger.info("=" * 80)
        logger.info("BTC DISCREPANCY ARBITRAGE — HOURLY BACKTEST")
        logger.info("=" * 80)
        logger.info(f"Data: {len(candles)} hourly candles from {candles[0]['date']} to {candles[-1]['date']}")
        logger.info(f"Initial Balance: ${self.INITIAL_BALANCE:,.2f}")
        logger.info("")
        
        for i in range(24, len(candles)):
            candle = candles[i]
            spot = candle['close']
            dt = candle['date']
            
            # Create new contracts every 4 hours
            if self.last_contract_creation is None or (dt - self.last_contract_creation).total_seconds() >= 4 * 3600:
                new_contracts = self.create_contracts(candle)
                self.contracts.extend(new_contracts)
                self.last_contract_creation = dt
            
            # Update contracts
            for contract in self.contracts:
                if not contract.resolved:
                    # Check if expired
                    if contract.expiration <= dt:
                        # Use a random price between low and high as resolution
                        # (simulates intraday price action)
                        resolved_price = random.uniform(candle['low'], candle['high'])
                        contract.resolve(resolved_price)
                    else:
                        # Calculate momentum from last 6 hours
                        if i >= 6:
                            momentum = (spot - candles[i-6]['close']) / candles[i-6]['close'] * 100
                        else:
                            momentum = 0.0
                        
                        # Volatility from last 24 hours
                        recent = candles[max(0, i-24):i+1]
                        if len(recent) > 1:
                            returns = [math.log(c['close'] / recent[j-1]['close']) 
                                      for j, c in enumerate(recent[1:], 1)]
                            vol = np.std(returns) * math.sqrt(365 * 24) if returns else 1.0
                        else:
                            vol = 1.0
                        
                        contract.update_from_spot(spot, momentum, vol)
            
            # Check exits
            self.check_exits(candle, self.contracts)
            
            # Scan for opportunities
            active = [c for c in self.contracts if not c.resolved]
            signals = self.scan_opportunities(candle, active)
            
            for signal in signals[:self.MAX_POSITIONS]:
                self.execute_signal(signal, candle)
            
            # Track equity
            equity = self.balance
            for trade in self.open_positions.values():
                contract = next((c for c in self.contracts if c.contract_id == trade.contract_id), None)
                if contract:
                    distance = (spot - contract.strike) / contract.strike
                    fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
                    equity += trade.qty * fair
            
            self.equity_curve.append((dt, equity))
            
            if equity > self.peak:
                self.peak = equity
            dd = (self.peak - equity) / self.peak
            if dd > self.max_drawdown:
                self.max_drawdown = dd
        
        # Close remaining
        final_candle = candles[-1]
        for contract_id, trade in list(self.open_positions.items()):
            contract = next((c for c in self.contracts if c.contract_id == contract_id), None)
            if contract and not contract.resolved:
                resolved_price = random.uniform(final_candle['low'], final_candle['high'])
                contract.resolve(resolved_price)
                self.check_exits(final_candle, [contract])
        
        return self.get_stats()
    
    def get_stats(self) -> Dict:
        closed = [t for t in self.trades if t.exit_price is not None]
        
        if closed:
            wins = sum(1 for t in closed if t.pnl > 0)
            losses = len(closed) - wins
            win_rate = wins / len(closed) if closed else 0
            
            avg_win = sum(t.pnl for t in closed if t.pnl > 0) / wins if wins > 0 else 0
            avg_loss = sum(t.pnl for t in closed if t.pnl < 0) / losses if losses > 0 else 0
            
            wins_sum = sum(t.pnl for t in closed if t.pnl > 0)
            losses_sum = abs(sum(t.pnl for t in closed if t.pnl < 0))
            pf = wins_sum / losses_sum if losses_sum > 0 else float('inf')
            
            return_pct = (self.balance - self.INITIAL_BALANCE) / self.INITIAL_BALANCE * 100
        else:
            wins = losses = 0
            win_rate = avg_win = avg_loss = pf = return_pct = 0
        
        return {
            'initial_balance': self.INITIAL_BALANCE,
            'final_balance': self.balance,
            'total_return_pct': return_pct,
            'total_trades': len(self.trades),
            'closed_trades': len(closed),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': pf,
            'max_drawdown_pct': self.max_drawdown * 100,
            'total_fees': self.total_fees,
        }


def run_hourly_backtest():
    candles = load_hourly_data()
    engine = HourlyArbitrageBacktest()
    stats = engine.run(candles)
    
    logger.info("")
    logger.info("=" * 80)
    logger.info("HOURLY BACKTEST RESULTS")
    logger.info("=" * 80)
    logger.info(f"Period: {candles[0]['date']} to {candles[-1]['date']}")
    logger.info(f"Candles: {len(candles)}")
    logger.info("")
    logger.info(f"Initial Balance:     ${stats['initial_balance']:,.2f}")
    logger.info(f"Final Balance:       ${stats['final_balance']:,.2f}")
    logger.info(f"Total Return:        {stats['total_return_pct']:+.2f}%")
    logger.info("")
    logger.info(f"Total Trades:        {stats['total_trades']}")
    logger.info(f"Closed Trades:       {stats['closed_trades']}")
    logger.info(f"Wins:                {stats['wins']}")
    logger.info(f"Losses:              {stats['losses']}")
    logger.info(f"Win Rate:            {stats['win_rate']*100:.1f}%")
    logger.info(f"Avg Win:             ${stats['avg_win']:+.2f}")
    logger.info(f"Avg Loss:            ${stats['avg_loss']:+.2f}")
    logger.info(f"Profit Factor:       {stats['profit_factor']:.2f}")
    logger.info(f"Max Drawdown:        {stats['max_drawdown_pct']:.2f}%")
    logger.info(f"Total Fees Paid:     ${stats['total_fees']:,.2f}")
    logger.info("=" * 80)
    
    # Save
    output_dir = DATA_DIR
    with open(os.path.join(output_dir, 'arb_hourly_results.json'), 'w') as f:
        json.dump(stats, f, indent=2, default=str)
    
    with open(os.path.join(output_dir, 'arb_hourly_equity.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['datetime', 'equity'])
        for dt, eq in engine.equity_curve:
            w.writerow([dt.strftime('%Y-%m-%d %H:%M'), f"{eq:.2f}"])
    
    logger.info("Results saved to arb_hourly_results.json and arb_hourly_equity.csv")
    return stats


if __name__ == '__main__':
    run_hourly_backtest()
