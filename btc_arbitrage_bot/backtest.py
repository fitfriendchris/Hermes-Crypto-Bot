"""
BTC Discrepancy Arbitrage — BACKTEST ENGINE
Uses real historical BTC data to simulate Polymarket-style binary contracts
and test the arbitrage signal generation + risk management.

Author: Hermes | May 2026
"""

import csv
import json
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Optional, Tuple

import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s'
)
logger = logging.getLogger('ARB_BACKTEST')

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')


# ──────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────────────────────────────────

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
# RESOLUTION SIMULATOR
# ──────────────────────────────────────────────────────────────────────────

def simulate_intraday_path(open_p: float, high: float, low: float, close: float,
                           resolution_time: datetime) -> float:
    """
    Simulate an intraday price path and return the price at resolution_time.
    Uses a random walk constrained by OHLC bounds.
    
    This prevents look-ahead bias where we always know the close price
    at resolution time.
    """
    import random
    
    # Resolution time as fraction of day (0=start, 1=end)
    # Assume trading day ~16 hours for BTC (00:00 to 16:00 for daily resolution)
    frac = (resolution_time.hour * 60 + resolution_time.minute) / (16 * 60)
    frac = max(0.05, min(0.95, frac))
    
    # Start at open
    current = open_p
    
    # Generate path: random walk trending toward close
    steps = 48  # 30-min steps
    target = close
    
    # Brownian bridge: random walk that starts at open and ends at close
    for step in range(1, steps + 1):
        t = step / steps
        
        # Mean reversion toward close
        drift = (target - current) * (1 - t) * 0.1
        
        # Random component
        vol = (high - low) / current / math.sqrt(steps)
        noise = random.gauss(0, vol * current)
        
        current += drift + noise
        
        # Enforce OHLC bounds
        current = max(low, min(high, current))
        
        # If we're at resolution time, return
        if t >= frac:
            return current
    
    return current


# ──────────────────────────────────────────────────────────────────────────
# INDICATORS
# ──────────────────────────────────────────────────────────────────────────

def calc_sma(values: List[float], period: int) -> float:
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    return sum(values[-period:]) / period


def calc_ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    multiplier = 2.0 / (period + 1)
    ema = values[0]
    for price in values[1:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_atr(candles: List[Dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        c = candles[i]
        prev = candles[i-1]
        tr = max(
            c['high'] - c['low'],
            abs(c['high'] - prev['close']),
            abs(c['low'] - prev['close']),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def calc_vwap(candles: List[Dict]) -> float:
    """Calculate VWAP for a list of candles."""
    total_pv = sum(c['close'] * c['volume'] for c in candles)
    total_vol = sum(c['volume'] for c in candles)
    return total_pv / total_vol if total_vol > 0 else candles[-1]['close']


def calc_roc(closes: List[float], period: int = 1) -> float:
    """Rate of Change: (current - past) / past * 100"""
    if len(closes) < period + 1:
        return 0.0
    current = closes[-1]
    past = closes[-(period + 1)]
    return ((current - past) / past) * 100.0


# ──────────────────────────────────────────────────────────────────────────
# POLYMARKET CONTRACT SIMULATOR
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class SimulatedContract:
    """Represents a Polymarket-style binary contract on historical BTC data."""
    contract_id: str
    question: str
    strike: float
    expiration: datetime
    created_at: datetime
    
    # Simulated order book state
    yes_ask: float = 0.50    # Market starts at ~50/50
    yes_bid: float = 0.50
    no_ask: float = 0.50
    no_bid: float = 0.50
    
    # Resolution
    resolved: bool = False
    resolution: Optional[str] = None  # 'yes' or 'no'
    
    def update_from_spot(self, spot_price: float, momentum: float, volatility: float):
        """
        Simulate Polymarket order book lag behind spot.
        
        Logic:
        - Fair probability = sigmoid((spot - strike) / strike * k)
        - Market price = fair + lag_factor + noise
        - Lag increases with volatility (less liquid = slower to update)
        - Momentum boost: strong directional move pushes prob toward extremes
        """
        distance = (spot_price - self.strike) / self.strike
        k = 25.0
        fair_prob = 1.0 / (1.0 + math.exp(-k * distance))
        
        # Momentum boost: if ROC is strong, shift probability
        if abs(momentum) > 1.0:
            if momentum > 0:
                fair_prob = min(1.0, fair_prob + momentum * 0.02)
            else:
                fair_prob = max(0.0, fair_prob + momentum * 0.02)
        
        # Simulated lag: Polymarket updates slower than spot
        # In high vol, lag is larger → bigger arbitrage opportunities
        lag_factor = 0.02 * volatility * (1.0 - fair_prob if fair_prob > 0.5 else fair_prob)
        
        # Add noise
        noise = np.random.normal(0, 0.01)
        
        # Yes ask = fair + lag + noise (market asks more than fair when fair < 0.5)
        # Yes bid = fair - lag + noise
        spread = 0.02  # 2 cent spread on $1 contract
        
        if fair_prob > 0.5:
            # Yes is favored, ask is slightly above fair, bid below
            self.yes_ask = min(0.99, fair_prob + lag_factor + noise + spread/2)
            self.yes_bid = max(0.01, fair_prob - lag_factor + noise - spread/2)
        else:
            # No is favored, Yes ask is cheap
            self.yes_ask = max(0.01, fair_prob + lag_factor + noise - spread/2)
            self.yes_bid = max(0.01, fair_prob - lag_factor + noise - spread/2)
        
        # No prices are inverse
        self.no_ask = 1.0 - self.yes_bid
        self.no_bid = 1.0 - self.yes_ask
    
    def resolve(self, final_price: float):
        self.resolved = True
        self.resolution = 'yes' if final_price >= self.strike else 'no'
    
    @property
    def edge_to_fair(self, spot_price: float) -> float:
        """Return the edge between fair value and market ask."""
        distance = (spot_price - self.strike) / self.strike
        fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
        return fair - self.yes_ask


# ──────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    contract_id: str
    direction: str      # 'buy_yes' or 'sell_yes'
    entry_price: float
    exit_price: Optional[float] = None
    qty: float = 1.0
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    exit_reason: str = ''
    pnl: float = 0.0


class ArbitrageBacktest:
    """
    Backtest the discrepancy arbitrage strategy on historical data.
    
    Simulates:
    1. Spot BTC price action (from real data)
    2. Polymarket-style binary contracts with strike levels
    3. Order book lag creating mispricing
    4. Entry/exit with fees and slippage
    """
    
    # Parameters
    INITIAL_BALANCE = 10000.0
    POSITION_SIZE = 500.0       # $ per trade
    MIN_EDGE_BPS = 150          # 1.5% min edge
    TAKER_FEE = 0.02            # 2% Polymarket fee
    SLIPPAGE = 0.001            # 0.1% slippage
    MAX_POSITIONS = 3
    
    # CRITICAL: Only trade contracts where spot is ABOVE strike
    # The edge comes from Polymarket lagging behind a pump, NOT
    # from betting on a recovery from below strike.
    REQUIRE_SPOT_ABOVE_STRIKE = True
    
    def __init__(self):
        self.balance = self.INITIAL_BALANCE
        self.equity_curve: List[Tuple[datetime, float]] = []
        self.trades: List[Trade] = []
        self.open_positions: Dict[str, Trade] = {}
        self.contracts: List[SimulatedContract] = []
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.current_date: Optional[datetime] = None
        self.max_drawdown = 0.0
        self.peak = self.INITIAL_BALANCE
        
        # Stats
        self.wins = 0
        self.losses = 0
        self.total_fees = 0.0
    
    def create_contracts(self, date: datetime, spot_price: float):
        """Generate synthetic contracts for the day based on spot price."""
        # Create contracts at strikes ABOVE current price (we only buy Yes when spot > strike)
        # e.g. "Will BTC be above $X at 5PM?"
        strikes = []
        base = spot_price
        
        # Strikes BELOW current price (contracts we're already winning)
        # These have high fair value but market may lag
        for pct in [-0.15, -0.10, -0.05, -0.02]:
            strike = round(base * (1 + pct), -3)
            if strike > 0 and strike < base:
                strikes.append(strike)
        
        # Strikes slightly ABOVE current price (near-the-money)
        for pct in [0.02, 0.05, 0.08]:
            strike = round(base * (1 + pct), -3)
            if strike > base:
                strikes.append(strike)
        
        # Round numbers
        for strike in [80000, 90000, 100000, 110000, 120000, 130000, 140000]:
            if strike > 0 and abs(strike - base) / base < 0.20:
                strikes.append(strike)
        
        strikes = sorted(set(strikes))
        
        contracts = []
        for strike in strikes:
            # Resolution at a random time during the day (to prevent look-ahead)
            resolution_hour = 14 + (hash(str(strike)) % 8)  # 14:00 to 21:00
            resolution_time = datetime.combine(date, datetime.min.time()) + timedelta(hours=resolution_hour)
            
            contract = SimulatedContract(
                contract_id=f"{date.strftime('%Y%m%d')}_{strike:.0f}",
                question=f"Will BTC be above ${strike:,.0f} on {date.strftime('%b %d')}?",
                strike=strike,
                expiration=resolution_time,
                created_at=date,
            )
            contracts.append(contract)
        
        return contracts
        
        contracts = []
        for strike in strikes:
            contract = SimulatedContract(
                contract_id=f"{date.strftime('%Y%m%d')}_{strike:.0f}",
                question=f"Will BTC be above ${strike:,.0f} on {date.strftime('%b %d')}?",
                strike=strike,
                expiration=date + timedelta(days=1),  # Resolves next day
                created_at=date,
            )
            contracts.append(contract)
        
        return contracts
    
    def scan_opportunities(self, candle: Dict, contracts: List[SimulatedContract]) -> List[Dict]:
        """
        Scan all active contracts for arbitrage signals.
        Returns list of valid signals sorted by edge.
        """
        signals = []
        spot = candle['close']
        
        # Momentum metrics
        closes = [c['close'] for c in [candle]]  # Would need history
        
        for contract in contracts:
            if contract.resolved:
                continue
            
            # Calculate fair value
            distance = (spot - contract.strike) / contract.strike
            fair_prob = 1.0 / (1.0 + math.exp(-25.0 * distance))
            
            # Momentum boost
            # (In real backtest we'd have ROC from previous candles)
            # Simplified: use ATR-based momentum
            
            # CRITICAL FILTER: Only buy "Yes" when spot is ABOVE strike.
            # The strategy is: spot pumps, Polymarket lags, we buy cheap Yes.
            # We do NOT buy Yes when spot is below strike (that's gambling on recovery).
            if self.REQUIRE_SPOT_ABOVE_STRIKE and spot < contract.strike:
                continue
            
            # Edge calculation
            gross_edge = fair_prob - contract.yes_ask
            net_edge = gross_edge - self.TAKER_FEE - self.SLIPPAGE
            
            if net_edge <= 0:
                continue
            
            edge_bps = int((net_edge / contract.yes_ask) * 10000) if contract.yes_ask > 0 else 0
            
            if edge_bps < self.MIN_EDGE_BPS:
                continue
            
            # Confidence: edge size + distance from 0.5
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
        
        # Sort by edge descending
        signals.sort(key=lambda x: x['edge_bps'], reverse=True)
        return signals
    
    def execute_signal(self, signal: Dict, candle: Dict) -> Optional[Trade]:
        """Simulate buying Yes shares when edge is detected."""
        contract = signal['contract']
        
        if len(self.open_positions) >= self.MAX_POSITIONS:
            return None
        
        if contract.contract_id in self.open_positions:
            return None
        
        # Check balance
        cost = self.POSITION_SIZE * signal['yes_ask']
        if cost > self.balance:
            return None
        
        # Calculate fees
        fee = cost * self.TAKER_FEE
        slippage = cost * self.SLIPPAGE
        total_cost = cost + fee + slippage
        
        if total_cost > self.balance:
            return None
        
        # Execute
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
            f"Ask {signal['yes_ask']:.4f} | "
            f"Fair {signal['fair_prob']:.4f} | "
            f"Edge {signal['edge_bps']} bps | "
            f"Cost ${total_cost:.2f}"
        )
        
        return trade
    
    def check_exits(self, candle: Dict, contracts: List[SimulatedContract]) -> None:
        """Check open positions for exit conditions."""
        spot = candle['close']
        to_close = []
        
        for contract_id, trade in list(self.open_positions.items()):
            contract = next((c for c in contracts if c.contract_id == contract_id), None)
            if not contract:
                continue
            
            # Exit condition 1: Contract resolved (end of day)
            if contract.resolved:
                if contract.resolution == 'yes':
                    exit_price = 1.0  # Yes pays $1
                    pnl = trade.qty * (exit_price - trade.entry_price)
                    trade.exit_reason = 'resolved_yes'
                else:
                    exit_price = 0.0  # Yes pays $0
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
                    f"EXIT | {contract.question} | "
                    f"Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | "
                    f"PnL ${pnl:+.2f} | {trade.exit_reason}"
                )
                continue
            
            # Exit condition 2: "Get Back" — edge evaporated (price moved against)
            distance = (spot - contract.strike) / contract.strike
            current_fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
            
            # If fair value dropped below entry, get out
            if current_fair < trade.entry_price * 0.95:  # 5% adverse move
                exit_price = max(0.01, contract.yes_bid)
                pnl = trade.qty * (exit_price - trade.entry_price)
                
                trade.exit_price = exit_price
                trade.exit_time = candle['date']
                trade.pnl = pnl
                trade.exit_reason = 'get_back_stop'
                self.balance += trade.qty * exit_price
                to_close.append(contract_id)
                
                if pnl > 0:
                    self.wins += 1
                else:
                    self.losses += 1
                
                logger.warning(
                    f"GET BACK | {contract.question} | "
                    f"Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | "
                    f"PnL ${pnl:+.2f}"
                )
                continue
            
            # Exit condition 3: Take profit if edge fully captured
            if current_fair > trade.entry_price + 0.05:  # 5 cent profit
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
                    f"TP | {contract.question} | "
                    f"Entry {trade.entry_price:.4f} | "
                    f"Exit {exit_price:.4f} | "
                    f"PnL ${pnl:+.2f}"
                )
        
        for cid in to_close:
            del self.open_positions[cid]
    
    def run(self, candles: List[Dict]) -> Dict:
        """Run the full backtest."""
        logger.info("=" * 80)
        logger.info("BTC DISCREPANCY ARBITRAGE — BACKTEST")
        logger.info("=" * 80)
        logger.info(f"Data: {len(candles)} candles from {candles[0]['date'].date()} to {candles[-1]['date'].date()}")
        logger.info(f"Initial Balance: ${self.INITIAL_BALANCE:,.2f}")
        logger.info(f"Position Size: ${self.POSITION_SIZE}")
        logger.info(f"Min Edge: {self.MIN_EDGE_BPS} bps")
        logger.info("")
        
        for i in range(60, len(candles)):
            candle = candles[i]
            spot = candle['close']
            
            # Create new contracts for the day
            if self.current_date != candle['date'].date():
                self.current_date = candle['date'].date()
                # Resolve any open contracts from previous day
                for contract in self.contracts:
                    if not contract.resolved and contract.expiration.date() <= self.current_date:
                        # Resolve using simulated intraday path to prevent look-ahead bias
                        resolution_time = contract.expiration
                        resolved_price = simulate_intraday_path(
                            candle['open'], candle['high'], candle['low'], candle['close'],
                            resolution_time
                        )
                        contract.resolve(resolved_price)
                
                # Create new contracts
                new_contracts = self.create_contracts(candle['date'], spot)
                self.contracts.extend(new_contracts)
            
            # Update all active contracts with current spot
            for contract in self.contracts:
                if not contract.resolved:
                    # Calculate volatility from recent candles
                    recent = candles[max(0, i-20):i+1]
                    if len(recent) > 1:
                        returns = [math.log(c['close'] / recent[j-1]['close']) 
                                  for j, c in enumerate(recent[1:], 1)]
                        vol = np.std(returns) * math.sqrt(365) if returns else 0.5
                    else:
                        vol = 0.5
                    
                    # Momentum from 5-day ROC
                    if i >= 5:
                        momentum = (candle['close'] - candles[i-5]['close']) / candles[i-5]['close'] * 100
                    else:
                        momentum = 0.0
                    
                    contract.update_from_spot(spot, momentum, vol)
            
            # Check exits first
            self.check_exits(candle, self.contracts)
            
            # Scan for new opportunities
            active_contracts = [c for c in self.contracts if not c.resolved]
            signals = self.scan_opportunities(candle, active_contracts)
            
            for signal in signals:
                self.execute_signal(signal, candle)
            
            # Track equity
            equity = self.balance
            for trade in self.open_positions.values():
                contract = next((c for c in self.contracts if c.contract_id == trade.contract_id), None)
                if contract:
                    distance = (spot - contract.strike) / contract.strike
                    fair = 1.0 / (1.0 + math.exp(-25.0 * distance))
                    equity += trade.qty * fair
            
            self.equity_curve.append((candle['date'], equity))
            
            if equity > self.peak:
                self.peak = equity
            dd = (self.peak - equity) / self.peak
            if dd > self.max_drawdown:
                self.max_drawdown = dd
        
        # Close any remaining positions at final price
        # Use the last candle's close as final resolution
        final_candle = candles[-1]
        for contract_id, trade in list(self.open_positions.items()):
            contract = next((c for c in self.contracts if c.contract_id == contract_id), None)
            if contract and not contract.resolved:
                resolved_price = simulate_intraday_path(
                    final_candle['open'], final_candle['high'], final_candle['low'], final_candle['close'],
                    contract.expiration
                )
                contract.resolve(resolved_price)
                self.check_exits(candles[-1], [contract])
        
        return self.get_stats()
    
    def get_stats(self) -> Dict:
        total_trades = len(self.trades)
        closed_trades = [t for t in self.trades if t.exit_price is not None]
        open_trades = [t for t in self.trades if t.exit_price is None]
        
        if closed_trades:
            wins = sum(1 for t in closed_trades if t.pnl > 0)
            losses = len(closed_trades) - wins
            win_rate = wins / len(closed_trades)
            
            avg_win = sum(t.pnl for t in closed_trades if t.pnl > 0) / wins if wins > 0 else 0
            avg_loss = sum(t.pnl for t in closed_trades if t.pnl < 0) / losses if losses > 0 else 0
            
            profit_factor = (
                sum(t.pnl for t in closed_trades if t.pnl > 0) /
                abs(sum(t.pnl for t in closed_trades if t.pnl < 0))
                if losses > 0 else float('inf')
            )
            
            total_pnl = sum(t.pnl for t in closed_trades)
            return_pct = (self.balance - self.INITIAL_BALANCE) / self.INITIAL_BALANCE * 100
        else:
            wins = losses = 0
            win_rate = avg_win = avg_loss = profit_factor = total_pnl = return_pct = 0
        
        return {
            'initial_balance': self.INITIAL_BALANCE,
            'final_balance': self.balance,
            'total_return_pct': return_pct,
            'total_trades': total_trades,
            'closed_trades': len(closed_trades),
            'open_trades': len(open_trades),
            'wins': wins,
            'losses': losses,
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'max_drawdown_pct': self.max_drawdown * 100,
            'total_fees': self.total_fees,
            'trades': [
                {
                    'contract_id': t.contract_id,
                    'direction': t.direction,
                    'entry': t.entry_price,
                    'exit': t.exit_price,
                    'pnl': t.pnl,
                    'reason': t.exit_reason,
                    'entry_time': t.entry_time.isoformat(),
                    'exit_time': t.exit_time.isoformat() if t.exit_time else None,
                }
                for t in closed_trades
            ]
        }


# ──────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────

def run_backtest():
    """Run the full backtest and print results."""
    # Load data
    candles = load_daily_data()
    
    # Run backtest
    engine = ArbitrageBacktest()
    stats = engine.run(candles)
    
    # Print results
    logger.info("")
    logger.info("=" * 80)
    logger.info("BACKTEST RESULTS")
    logger.info("=" * 80)
    logger.info(f"Period: {candles[0]['date'].date()} to {candles[-1]['date'].date()}")
    logger.info(f"Candles: {len(candles)}")
    logger.info("")
    logger.info(f"Initial Balance:     ${stats['initial_balance']:,.2f}")
    logger.info(f"Final Balance:       ${stats['final_balance']:,.2f}")
    logger.info(f"Total Return:        {stats['total_return_pct']:+.2f}%")
    logger.info("")
    logger.info(f"Total Trades:        {stats['total_trades']}")
    logger.info(f"Closed Trades:       {stats['closed_trades']}")
    logger.info(f"Open Trades:         {stats['open_trades']}")
    logger.info(f"Wins:                {stats['wins']}")
    logger.info(f"Losses:              {stats['losses']}")
    logger.info(f"Win Rate:            {stats['win_rate']*100:.1f}%")
    logger.info(f"Avg Win:             ${stats['avg_win']:+.2f}")
    logger.info(f"Avg Loss:            ${stats['avg_loss']:+.2f}")
    logger.info(f"Profit Factor:       {stats['profit_factor']:.2f}")
    logger.info(f"Max Drawdown:        {stats['max_drawdown_pct']:.2f}%")
    logger.info(f"Total Fees Paid:     ${stats['total_fees']:,.2f}")
    logger.info("=" * 80)
    
    # Save results
    output_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    with open(os.path.join(output_dir, 'arb_backtest_results.json'), 'w') as f:
        json.dump(stats, f, indent=2, default=str)
    
    # Save equity curve
    with open(os.path.join(output_dir, 'arb_equity_curve.csv'), 'w', newline='') as f:
        import csv as csv_module
        writer = csv_module.writer(f)
        writer.writerow(['date', 'equity'])
        for dt, eq in engine.equity_curve:
            writer.writerow([dt.strftime('%Y-%m-%d'), f"{eq:.2f}"])
    
    logger.info("Results saved to arb_backtest_results.json and arb_equity_curve.csv")
    
    return stats


if __name__ == '__main__':
    run_backtest()
