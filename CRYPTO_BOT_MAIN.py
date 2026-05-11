#!/usr/bin/env python3
"""
CRYPTO COPY-TRADING BOT
Author: Hermes (Sovereign Chief of Staff)
Operator: Chris (yuhfriendchris)
Strategy: Aggressive micro-cap growth with smart stops
Version: 1.0.0
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import ccxt.async_support as ccxt
import yaml
from dotenv import load_dotenv

# Load config
with open('CRYPTO_BOT_CONFIG.yaml', 'r') as f:
    CONFIG = yaml.safe_load(f)

# Load API keys
load_dotenv()

logging.basicConfig(
    level=getattr(logging, CONFIG['logging']['level'].upper()),
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('crypto_bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('CryptoBot')

class SmartStops:
    """Dynamic stop loss calculation using multiple layers."""
    
    def __init__(self, config: Dict):
        self.config = config['stop_loss']
        self.max_risk = self.config['fixed_pct']
        self.atr_mult = self.config['atr_multiplier']
    
    def calculate(
        self,
        entry_price: float,
        ict_data: Dict,
        whale_data: Dict,
        token_data: Dict,
        position_size: float
    ) -> Tuple[float, str]:
        """
        Calculate smart stop. Returns (stop_price, stop_type).
        Priority: structure > whale > fvg > liquidity > atr > fixed
        """
        stops = {}
        
        # Layer 1: Structure stop
        if ict_data.get('higher_low'):
            stops['structure'] = ict_data['higher_low'] * 0.995
        
        # Layer 2: Whale cluster stop
        if whale_data.get('stop_cluster'):
            stops['whale'] = whale_data['stop_cluster'] * 0.98
        
        # Layer 3: FVG stop
        if ict_data.get('bullish_fvg_low'):
            stops['fvg'] = ict_data['bullish_fvg_low']
        
        # Layer 4: Liquidity stop
        if ict_data.get('equal_lows'):
            stops['liquidity'] = ict_data['equal_lows'] * 0.99
        
        # Layer 5: ATR stop
        atr = token_data.get('atr_14', entry_price * 0.05)
        stops['atr'] = entry_price - (self.atr_mult * atr)
        
        # Layer 6: Fixed emergency
        stops['fixed'] = entry_price * (1 - self.max_risk)
        
        # Select: Tightest valid stop above fixed emergency
        valid_stops = {k: v for k, v in stops.items() if v < entry_price}
        
        if not valid_stops:
            return stops['fixed'], 'fixed_emergency'
        
        # Priority selection
        if 'structure' in valid_stops and valid_stops['structure'] > stops['fixed']:
            return max(valid_stops['structure'], stops['atr']), 'structure'
        elif 'whale' in valid_stops and valid_stops['whale'] > stops['fixed']:
            return max(valid_stops['whale'], stops['atr']), 'whale'
        elif 'fvg' in valid_stops and valid_stops['fvg'] > stops['fixed']:
            return max(valid_stops['fvg'], stops['atr']), 'fvg'
        else:
            return max(valid_stops.values()), 'hybrid'
    
    def should_trail(
        self,
        position: Dict,
        current_price: float,
        unrealized_r: float
    ) -> Optional[float]:
        """Determine if stop should be trailed. Returns new stop or None."""
        config = self.config
        
        # Phase 1: Breakeven
        if unrealized_r >= 2.0 and position['stop_type'] not in ['breakeven', 'trailing']:
            return position['entry'] * 1.005, 'breakeven'
        
        # Phase 2: Structure trail
        if unrealized_r >= 3.0 and position.get('new_higher_low'):
            new_stop = position['new_higher_low'] * 0.995
            if new_stop > position['stop']:
                return new_stop, 'trailing_structure'
        
        # Phase 3: FVG trail
        if unrealized_r >= 4.0 and position.get('new_fvg_low'):
            if position['new_fvg_low'] > position['stop']:
                return position['new_fvg_low'], 'trailing_fvg'
        
        return None

class RiskFilter:
    """Multi-factor scam detection for micro-caps."""
    
    def __init__(self, config: Dict):
        self.config = config['risk_filter']
        self.min_score = self.config['min_scam_score']
    
    async def score_token(self, token_address: str) -> Tuple[int, List[str]]:
        """
        Score token 0-100. Below min_score = REJECT.
        Returns (score, red_flags).
        """
        score = 100
        flags = []
        
        # Check 1: Liquidity locked
        if not await self._liquidity_locked(token_address):
            score -= 20
            flags.append('liquidity_not_locked')
        
        # Check 2: Honeypot
        if await self._is_honeypot(token_address):
            score -= 50
            flags.append('HONEYPOT')
        
        # Check 3: Mint function
        if await self._can_mint(token_address):
            score -= 15
            flags.append('mint_function_enabled')
        
        # Check 4: Ownership
        if not await self._ownership_renounced(token_address):
            score -= 10
            flags.append('ownership_not_renounced')
        
        # Check 5: Holder concentration
        concentration = await self._holder_concentration(token_address)
        if concentration > self.config['max_holder_concentration']:
            score -= 15
            flags.append(f'top10_holders_{concentration:.0%}')
        
        # Check 6: Volume
        volume = await self._daily_volume(token_address)
        if volume < self.config['min_volume_24h']:
            score -= 10
            flags.append(f'low_volume_${volume:,.0f}')
        
        # Check 7: Dev wallet activity
        if await self._dev_recent_sells(token_address):
            score -= 20
            flags.append('dev_wallet_selling')
        
        # Check 8: Social presence
        if not await self._has_socials(token_address):
            score -= 10
            flags.append('no_social_presence')
        
        return max(0, score), flags
    
    async def _liquidity_locked(self, address: str) -> bool:
        # TODO: Implement via Team Finance / Uncx API
        return True  # Placeholder
    
    async def _is_honeypot(self, address: str) -> bool:
        # TODO: Implement via Token Sniffer / Honeypot.is API
        return False  # Placeholder
    
    async def _can_mint(self, address: str) -> bool:
        # TODO: Implement via contract analysis
        return False  # Placeholder
    
    async def _ownership_renounced(self, address: str) -> bool:
        # TODO: Implement via contract analysis
        return True  # Placeholder
    
    async def _holder_concentration(self, address: str) -> float:
        # TODO: Implement via Bubblemaps / holder APIs
        return 0.30  # Placeholder
    
    async def _daily_volume(self, address: str) -> float:
        # TODO: Implement via DexScreener / DEXTools
        return 50000  # Placeholder
    
    async def _dev_recent_sells(self, address: str) -> bool:
        # TODO: Implement via wallet monitoring
        return False  # Placeholder
    
    async def _has_socials(self, address: str) -> bool:
        # TODO: Implement via social scan
        return True  # Placeholder

class WhaleTracker:
    """Track whale wallets for copy-trading signals."""
    
    def __init__(self, config: Dict):
        self.config = config['whales']
        self.wallets = []
        self.scores = {}
    
    async def initialize(self):
        """Load whale wallet list and scores."""
        # TODO: Load from database or API
        self.wallets = [
            '0x1234...',  # Placeholder
            '0x5678...',
        ]
        logger.info(f"Tracking {len(self.wallets)} whale wallets")
    
    async def monitor(self, callback):
        """Monitor wallets for new trades."""
        while True:
            for wallet in self.wallets:
                trades = await self._get_recent_trades(wallet)
                for trade in trades:
                    if self._is_significant(trade):
                        await callback(trade)
            await asyncio.sleep(5)  # 5 second check interval
    
    async def _get_recent_trades(self, wallet: str) -> List[Dict]:
        # TODO: Implement via Etherscan / BscScan / Arkham APIs
        return []
    
    def _is_significant(self, trade: Dict) -> bool:
        return trade.get('value_usd', 0) > self.config['min_trade_size_usd']

class PositionManager:
    """Manage open positions, sizing, and exits."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.positions = {}
        self.smart_stops = SmartStops(config)
        self.risk_filter = RiskFilter(config)
    
    def calculate_size(
        self,
        balance: float,
        whale_trade: Dict,
        risk_multiplier: float = 1.0
    ) -> float:
        """Calculate position size based on whale and balance."""
        whale_pct = whale_trade['value_usd'] / whale_trade['whale_portfolio']
        base_size = balance * whale_pct * risk_multiplier
        
        # Cap at max risk
        max_risk = balance * self.config['account']['max_risk_per_trade']
        return min(base_size, max_risk)
    
    async def open_position(
        self,
        token: str,
        entry_price: float,
        size: float,
        ict_data: Dict,
        whale_data: Dict,
        token_data: Dict
    ) -> Dict:
        """Open new position with smart stop."""
        
        # Risk filter check
        if token.startswith('0x'):
            score, flags = await self.risk_filter.score_token(token)
            if score < self.config['risk_filter']['min_scam_score']:
                logger.warning(f"REJECTED {token}: score {score}, flags {flags}")
                return None
        
        # Calculate smart stop
        stop, stop_type = self.smart_stops.calculate(
            entry_price, ict_data, whale_data, token_data, size
        )
        
        risk = (entry_price - stop) / entry_price
        
        # Check max risk
        if risk > self.config['stop_loss']['fixed_pct']:
            # Reduce size
            new_size = size * (self.config['stop_loss']['fixed_pct'] / risk)
            size = new_size
            logger.info(f"Reduced size to {size:.2f} to maintain risk limit")
        
        position = {
            'token': token,
            'entry': entry_price,
            'size': size,
            'stop': stop,
            'stop_type': stop_type,
            'risk_pct': risk,
            'opened_at': datetime.now(),
            'tier_exits': {1: False, 2: False, 3: False, 4: False},
            'highest_price': entry_price,
        }
        
        self.positions[token] = position
        logger.info(f"OPEN {token}: entry=${entry_price:.6f}, stop=${stop:.6f}, risk={risk:.2%}")
        
        return position
    
    def check_exits(self, token: str, current_price: float) -> Optional[str]:
        """Check if position should be exited. Returns exit reason or None."""
        position = self.positions.get(token)
        if not position:
            return None
        
        # Update highest price
        if current_price > position['highest_price']:
            position['highest_price'] = current_price
        
        # Stop loss
        if current_price <= position['stop']:
            return f"stop_loss_{position['stop_type']}"
        
        # Time stop
        hold_time = datetime.now() - position['opened_at']
        if hold_time > timedelta(hours=self.config['stop_loss']['time_stop_hours']):
            return "time_stop"
        
        # Calculate R multiple
        risk = position['entry'] - position['stop']
        if risk == 0:
            return None
        unrealized_r = (current_price - position['entry']) / risk
        
        # Tiered exits
        tiers = self.config['take_profit']
        if not position['tier_exits'][1] and unrealized_r >= tiers['tier_1_r']:
            position['tier_exits'][1] = True
            return f"tier_1_{tiers['tier_1_r']}R"
        
        if not position['tier_exits'][2] and unrealized_r >= tiers['tier_2_r']:
            position['tier_exits'][2] = True
            return f"tier_2_{tiers['tier_2_r']}R"
        
        if not position['tier_exits'][3] and unrealized_r >= tiers['tier_3_r']:
            position['tier_exits'][3] = True
            return f"tier_3_{tiers['tier_3_r']}R"
        
        if not position['tier_exits'][4] and unrealized_r >= tiers['tier_4_r']:
            position['tier_exits'][4] = True
            return f"tier_4_{tiers['tier_4_r']}R"
        
        # Trailing stop for final portion
        if position['tier_exits'][4]:
            trail_level = position['highest_price'] * (1 - tiers['final_trail_pct'])
            if current_price <= trail_level:
                return "trailing_stop"
        
        # Market cap targets (if available)
        if 'market_cap' in position:
            mc = position['market_cap']
            if mc >= tiers.get('mc_target_3', 50000000):
                return "mc_target_3_50M"
            elif mc >= tiers.get('mc_target_2', 10000000):
                return "mc_target_2_10M"
            elif mc >= tiers.get('mc_target_1', 1000000):
                return "mc_target_1_1M"
        
        return None
    
    def close_position(self, token: str, exit_price: float, reason: str) -> Dict:
        """Close position and calculate PnL."""
        position = self.positions.pop(token, None)
        if not position:
            return None
        
        pnl = (exit_price - position['entry']) / position['entry']
        pnl_usd = position['size'] * pnl
        
        result = {
            'token': token,
            'entry': position['entry'],
            'exit': exit_price,
            'size': position['size'],
            'pnl_pct': pnl,
            'pnl_usd': pnl_usd,
            'reason': reason,
            'hold_time': datetime.now() - position['opened_at'],
            'highest_price': position['highest_price'],
        }
        
        logger.info(f"CLOSE {token}: ${pnl_usd:+.2f} ({pnl:+.2%}) | {reason}")
        
        return result

class ExchangeConnector:
    """Connect to exchange APIs for execution."""
    
    def __init__(self, config: Dict):
        self.config = config['execution']
        self.exchange = None
    
    async def initialize(self):
        """Initialize exchange connection."""
        exchange_id = self.config['exchange']
        
        api_key = os.getenv(f'{exchange_id.upper()}_API_KEY')
        api_secret = os.getenv(f'{exchange_id.upper()}_API_SECRET')
        
        if not api_key or not api_secret:
            raise ValueError(f"Missing API credentials for {exchange_id}")
        
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'spot'}
        })
        
        await self.exchange.load_markets()
        logger.info(f"Connected to {exchange_id}")
    
    async def get_balance(self) -> float:
        """Get USDT balance."""
        balance = await self.exchange.fetch_balance()
        return balance['USDT']['free']
    
    async def get_price(self, symbol: str) -> float:
        """Get current price."""
        ticker = await self.exchange.fetch_ticker(symbol)
        return ticker['last']
    
    async def market_buy(self, symbol: str, amount_usd: float) -> Dict:
        """Execute market buy."""
        try:
            order = await self.exchange.create_market_buy_order(
                symbol, amount_usd
            )
            logger.info(f"BUY {symbol}: ${amount_usd:.2f}")
            return order
        except Exception as e:
            logger.error(f"BUY FAILED {symbol}: {e}")
            return None
    
    async def market_sell(self, symbol: str, amount: float) -> Dict:
        """Execute market sell."""
        try:
            order = await self.exchange.create_market_sell_order(
                symbol, amount
            )
            logger.info(f"SELL {symbol}: {amount:.6f}")
            return order
        except Exception as e:
            logger.error(f"SELL FAILED {symbol}: {e}")
            return None
    
    async def close(self):
        await self.exchange.close()

class CryptoBot:
    """Main bot orchestrator."""
    
    def __init__(self):
        self.config = CONFIG
        self.exchange = ExchangeConnector(CONFIG)
        self.whales = WhaleTracker(CONFIG)
        self.positions = PositionManager(CONFIG)
        self.balance = 0.0
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.consecutive_losses = 0
        self.running = False
    
    async def start(self):
        """Start the bot."""
        logger.info("=" * 50)
        logger.info("CRYPTO BOT STARTING")
        logger.info(f"Capital: ${self.config['account']['starting_balance_usd']}")
        logger.info(f"Mode: AGGRESSIVE")
        logger.info("=" * 50)
        
        await self.exchange.initialize()
        await self.whales.initialize()
        
        self.balance = await self.exchange.get_balance()
        self.running = True
        
        # Start whale monitoring
        asyncio.create_task(self.whales.monitor(self.on_whale_signal))
        
        # Start position monitoring
        asyncio.create_task(self.monitor_positions())
        
        # Start daily reporting
        asyncio.create_task(self.daily_report())
        
        logger.info("Bot running. Waiting for whale signals...")
    
    async def on_whale_signal(self, trade: Dict):
        """Handle new whale trade signal."""
        logger.info(f"WHALE SIGNAL: {trade['token']} ${trade['value_usd']:,.0f}")
        
        # Safety checks
        if self.daily_pnl <= -self.config['safety']['max_daily_loss_usd']:
            logger.warning("Daily loss limit hit. Ignoring signal.")
            return
        
        if self.consecutive_losses >= self.config['safety']['max_consecutive_losses']:
            logger.warning("Max consecutive losses. Cooling down.")
            return
        
        if len(self.positions.positions) >= self.config['account']['max_open_positions']:
            logger.warning("Max positions open. Ignoring signal.")
            return
        
        # Calculate position size
        size = self.positions.calculate_size(
            self.balance, trade, 
            risk_multiplier=2.0 if self.config['aggressive_mode']['enabled'] else 1.0
        )
        
        if size < self.config['account']['min_trade_size_usd']:
            logger.info(f"Size ${size:.2f} too small. Skipping.")
            return
        
        # Get entry price
        symbol = f"{trade['token']}/USDT"
        try:
            entry_price = await self.exchange.get_price(symbol)
        except:
            logger.warning(f"Cannot get price for {symbol}")
            return
        
        # Placeholder ICT/whale data (to be implemented)
        ict_data = {}
        whale_data = {'stop_cluster': entry_price * 0.85}  # Placeholder
        token_data = {'atr_14': entry_price * 0.08}
        
        # Open position
        position = await self.positions.open_position(
            trade['token'], entry_price, size, ict_data, whale_data, token_data
        )
        
        if position:
            # Execute buy
            order = await self.exchange.market_buy(symbol, size)
            if order:
                self.trades_today += 1
    
    async def monitor_positions(self):
        """Monitor open positions for exits."""
        while self.running:
            for token, position in list(self.positions.positions.items()):
                symbol = f"{token}/USDT"
                
                try:
                    current_price = await self.exchange.get_price(symbol)
                except:
                    continue
                
                # Check for exit
                exit_reason = self.positions.check_exits(token, current_price)
                
                if exit_reason:
                    # Determine exit size
                    tier = None
                    if 'tier' in exit_reason:
                        tier = int(exit_reason.split('_')[1])
                    
                    # Calculate exit amount
                    if tier:
                        if tier == 1:
                            exit_pct = 0.25
                        elif tier == 2:
                            exit_pct = 0.25
                        elif tier == 3:
                            exit_pct = 0.25
                        elif tier == 4:
                            exit_pct = 0.15
                        else:
                            exit_pct = 0.10  # Final trail
                        
                        exit_amount = position['size'] * exit_pct
                    else:
                        exit_amount = position['size']  # Full exit
                    
                    # Execute sell
                    order = await self.exchange.market_sell(symbol, exit_amount)
                    
                    if order:
                        # Record result
                        result = self.positions.close_position(
                            token, current_price, exit_reason
                        )
                        
                        if result:
                            self.daily_pnl += result['pnl_usd']
                            self.balance += result['pnl_usd']
                            
                            if result['pnl_usd'] < 0:
                                self.consecutive_losses += 1
                            else:
                                self.consecutive_losses = 0
                            
                            # Check pyramid
                            if (self.config['aggressive_mode']['pyramiding'] and 
                                result['pnl_pct'] > 2.0 and
                                not position.get('pyramided')):
                                await self.pyramid_add(token, position)
            
            await asyncio.sleep(10)  # 10 second position check
    
    async def pyramid_add(self, token: str, position: Dict):
        """Add to winning position."""
        if position.get('pyramid_count', 0) >= self.config['aggressive_mode']['max_pyramid_adds']:
            return
        
        add_size = position['size'] * 0.5  # Add 50% of original
        symbol = f"{token}/USDT"
        
        order = await self.exchange.market_buy(symbol, add_size)
        if order:
            position['pyramid_count'] = position.get('pyramid_count', 0) + 1
            position['size'] += add_size
            logger.info(f"PYRAMID {token}: Added ${add_size:.2f}")
    
    async def daily_report(self):
        """Send daily summary."""
        while self.running:
            await asyncio.sleep(86400)  # 24 hours
            
            report = f"""
📊 DAILY REPORT — {datetime.now().strftime('%Y-%m-%d')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Balance: ${self.balance:.2f} ({(self.balance/100-1)*100:+.0f}%)
Daily PnL: ${self.daily_pnl:+.2f}
Trades: {self.trades_today}
Open positions: {len(self.positions.positions)}
Consecutive losses: {self.consecutive_losses}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            logger.info(report)
            
            # Reset daily counters
            self.daily_pnl = 0.0
            self.trades_today = 0
    
    async def stop(self):
        """Stop the bot gracefully."""
        self.running = False
        
        # Close all positions
        for token, position in list(self.positions.positions.items()):
            symbol = f"{token}/USDT"
            try:
                price = await self.exchange.get_price(symbol)
                await self.exchange.market_sell(symbol, position['size'])
                self.positions.close_position(token, price, "bot_shutdown")
            except Exception as e:
                logger.error(f"Failed to close {token}: {e}")
        
        await self.exchange.close()
        logger.info("Bot stopped")

async def main():
    """Main entry point."""
    bot = CryptoBot()
    
    try:
        await bot.start()
        
        # Run forever
        while bot.running:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
        await bot.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        await bot.stop()

if __name__ == "__main__":
    asyncio.run(main())
