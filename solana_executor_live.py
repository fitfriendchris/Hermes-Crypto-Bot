#!/usr/bin/env python3
"""
SOLANA MOMENTUM EXECUTOR — LIVE VERSION
Real Jupiter API integration with wallet signing.

CRITICAL: This script executes real trades on Solana mainnet.
Only run if you have SOL in your wallet and understand the risks.

Author: Hermes | May 2026
"""

import os
import json
import base64
import asyncio
import logging
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('solana_live.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('SOL_LIVE')

# ─── CONFIG ───
MIN_POSITION_USD = 5.0
MAX_POSITION_USD = 50.0
MIN_LIQUIDITY_USD = 50000
MIN_MOMENTUM_1H = 10.0
MIN_MOMENTUM_6H = 0.0
MAX_SLIPPAGE_PCT = 3.0
TAKER_FEE = 0.02
HARD_STOP_PCT = -15.0
TAKE_PROFIT_PCT = 50.0
CHECK_INTERVAL_SEC = 30
HOLD_MAX_HOURS = 4

SOLANA_RPC = os.getenv('SOLANA_RPC', 'https://api.mainnet-beta.solana.com')
JUPITER_QUOTE_API = 'https://quote-api.jup.ag/v6'
JUPITER_SWAP_API = 'https://quote-api.jup.ag/v6'

# Token mints
SOL_MINT = 'So11111111111111111111111111111111111111112'  # Wrapped SOL
USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'


@dataclass
class Position:
    symbol: str
    token_address: str
    entry_price: float
    position_usd: float
    entry_time: datetime
    highest_seen: float
    
    def pnl_pct(self, current_price: float) -> float:
        return ((current_price - self.entry_price) / self.entry_price) * 100


class LiveExecutor:
    """Live Solana momentum executor with real swaps."""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.wallet_pubkey: Optional[str] = None
        self.sol_balance: float = 0.0
        self.trade_count = 0
        self.total_pnl = 0.0
    
    async def initialize(self) -> bool:
        """Setup wallet and session."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={'Accept': 'application/json'}
        )
        
        # Load wallet via Solana CLI
        try:
            result = subprocess.run(
                ['solana', 'address'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                self.wallet_pubkey = result.stdout.strip()
                logger.info(f"🗝️  Wallet: {self.wallet_pubkey}")
                await self.update_balance()
            else:
                logger.error("❌ No wallet. Run: solana-keygen new")
                return False
        except FileNotFoundError:
            logger.error("❌ Solana CLI not installed")
            return False
        
        return True
    
    async def update_balance(self):
        """Check SOL balance."""
        try:
            async with self.session.post(
                SOLANA_RPC,
                json={
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'getBalance',
                    'params': [self.wallet_pubkey]
                }
            ) as resp:
                data = await resp.json()
                lamports = data['result']['value']
                self.sol_balance = lamports / 1e9
                sol_price = await self.get_sol_price()
                usd_value = self.sol_balance * sol_price
                logger.info(f"💰 Balance: {self.sol_balance:.4f} SOL (${usd_value:.2f})")
        except Exception as e:
            logger.warning(f"Balance check failed: {e}")
    
    async def get_sol_price(self) -> float:
        """Get SOL price in USD."""
        try:
            async with self.session.get(
                'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
                timeout=5
            ) as resp:
                data = await resp.json()
                return data['solana']['usd']
        except:
            return 160.0  # Fallback
    
    async def get_trending_tokens(self) -> List[Dict]:
        """Fetch from GeckoTerminal."""
        url = 'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1'
        tokens = []
        
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for p in data.get('data', []):
                        attr = p.get('attributes', {})
                        name = attr.get('name', '')
                        parts = name.split(' / ')
                        symbol = parts[0] if len(parts) > 0 else 'UNKNOWN'
                        
                        rel = p.get('relationships', {})
                        base_token_id = rel.get('base_token', {}).get('data', {}).get('id', '')
                        mint_address = base_token_id.split('_', 1)[1] if '_' in base_token_id else ''
                        
                        token = {
                            'symbol': symbol,
                            'address': mint_address,
                            'price': float(attr.get('base_token_price_usd', 0) or 0),
                            'liquidity': float(attr.get('reserve_in_usd', 0) or 0),
                            'volume_24h': float(attr.get('volume_usd', {}).get('h24', 0) or 0),
                            'change_1h': float(attr.get('price_change_percentage', {}).get('h1', 0) or 0),
                            'change_6h': float(attr.get('price_change_percentage', {}).get('h6', 0) or 0),
                            'change_24h': float(attr.get('price_change_percentage', {}).get('h24', 0) or 0),
                            'mcap': float(attr.get('market_cap_usd', 0) or attr.get('fdv_usd', 0) or 0),
                        }
                        tokens.append(token)
        except Exception as e:
            logger.warning(f"Token fetch failed: {e}")
        
        return tokens
    
    def calculate_position_size(self, liquidity: float, momentum: float) -> float:
        """Viable position for profitable exit."""
        max_pos = liquidity * 0.02
        
        for size in [MAX_POSITION_USD, 20.0, 10.0, 5.0]:
            slip = (size / liquidity) * 100
            if slip <= MAX_SLIPPAGE_PCT and size <= max_pos:
                breakeven = (slip * 2) + (TAKER_FEE * 2 * 100)
                min_gain = breakeven + 5.0
                if momentum >= min_gain:
                    return size
        
        return 0.0
    
    async def execute_jupiter_swap(self, input_mint: str, output_mint: str, 
                                   amount_usd: float, is_buy: bool = True) -> bool:
        """
        Execute real Jupiter swap.
        Returns True if transaction submitted successfully.
        """
        direction = "BUY" if is_buy else "SELL"
        logger.info(f"🔄 {direction} ${amount_usd:.2f} of {output_mint[:8]}...")
        
        try:
            # Step 1: Get SOL price for amount conversion
            sol_price = await self.get_sol_price()
            amount_sol = amount_usd / sol_price
            amount_lamports = int(amount_sol * 1e9)
            
            # Step 2: Get Jupiter quote
            quote_params = {
                'inputMint': input_mint,
                'outputMint': output_mint,
                'amount': str(amount_lamports),
                'slippageBps': str(int(MAX_SLIPPAGE_PCT * 100)),
                'onlyDirectRoutes': 'false',
            }
            
            async with self.session.get(
                f"{JUPITER_QUOTE_API}/quote",
                params=quote_params,
                timeout=10
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Quote failed: {resp.status}")
                    return False
                quote = await resp.json()
                
                if 'error' in quote:
                    logger.error(f"Quote error: {quote['error']}")
                    return False
            
            # Step 3: Get swap transaction
            swap_body = {
                'quoteResponse': quote,
                'userPublicKey': self.wallet_pubkey,
                'wrapAndUnwrapSol': True,
                'feeAccount': None,
            }
            
            async with self.session.post(
                f"{JUPITER_SWAP_API}/swap",
                json=swap_body,
                timeout=15
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Swap request failed: {resp.status}")
                    return False
                swap_data = await resp.json()
                
                if 'error' in swap_data:
                    logger.error(f"Swap error: {swap_data['error']}")
                    return False
            
            # Step 4: Sign and send transaction
            tx_base64 = swap_data.get('swapTransaction')
            if not tx_base64:
                logger.error("No transaction returned")
                return False
            
            # Save to temp file
            tx_path = '/tmp/jupiter_swap.b64'
            with open(tx_path, 'w') as f:
                f.write(tx_base64)
            
            # Use Solana CLI to sign and send
            # NOTE: This requires the wallet to be unlocked or use keypair file
            result = subprocess.run(
                ['solana', 'transaction-sign-and-send', tx_path],
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                signature = result.stdout.strip()
                logger.info(f"✅ SWAP SUCCESS: {signature}")
                logger.info(f"   View: https://solscan.io/tx/{signature}")
                return True
            else:
                logger.error(f"❌ Swap failed: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Swap error: {e}")
            return False
    
    async def scan_and_trade(self):
        """Scan for opportunities and execute."""
        logger.info("=" * 60)
        logger.info("🔍 SCANNING")
        logger.info("=" * 60)
        
        tokens = await self.get_trending_tokens()
        
        # Check exits first
        await self.check_exits(tokens)
        
        # Look for entries
        for token in tokens:
            symbol = token['symbol']
            address = token['address']
            liq = token['liquidity']
            m1 = token['change_1h']
            m6 = token['change_6h']
            vol = token['volume_24h']
            
            if symbol in self.positions:
                continue
            if not address:
                continue
            if liq < MIN_LIQUIDITY_USD:
                continue
            if m1 < MIN_MOMENTUM_1H:
                continue
            if m6 < MIN_MOMENTUM_6H:
                continue
            if vol < 100000:
                continue
            
            pos_size = self.calculate_position_size(liq, m1)
            if pos_size < MIN_POSITION_USD:
                continue
            
            # Check we have enough SOL
            await self.update_balance()
            sol_price = await self.get_sol_price()
            if self.sol_balance * sol_price < pos_size:
                logger.warning(f"Insufficient balance for ${pos_size:.0f} trade")
                continue
            
            logger.info(f"🎯 SIGNAL | {symbol} | +{m1:.1f}% 1h | ${liq:,.0f} liq | ${pos_size:.0f} position")
            
            # Execute live swap
            success = await self.execute_jupiter_swap(
                input_mint=SOL_MINT,
                output_mint=address,
                amount_usd=pos_size,
                is_buy=True
            )
            
            if success:
                self.positions[symbol] = Position(
                    symbol=symbol,
                    token_address=address,
                    entry_price=token['price'],
                    position_usd=pos_size,
                    entry_time=datetime.now(),
                    highest_seen=token['price']
                )
                self.trade_count += 1
                logger.info(f"✅ POSITION OPEN | {symbol} @ ${token['price']:.6f} | ${pos_size:.0f}")
                break  # One position at a time
    
    async def check_exits(self, tokens: List[Dict]):
        """Manage open positions."""
        to_remove = []
        
        for symbol, pos in self.positions.items():
            current = next((t for t in tokens if t['symbol'] == symbol), None)
            if not current:
                continue
            
            current_price = current['price']
            pnl = pos.pnl_pct(current_price)
            hold_time = datetime.now() - pos.entry_time
            
            if current_price > pos.highest_seen:
                pos.highest_seen = current_price
            
            logger.info(
                f"📊 {symbol} | Entry: ${pos.entry_price:.6f} | "
                f"Now: ${current_price:.6f} | PnL: {pnl:+.1f}% | "
                f"Hold: {hold_time.total_seconds()/60:.0f}m"
            )
            
            should_exit = False
            exit_reason = ""
            
            if pnl >= TAKE_PROFIT_PCT:
                should_exit = True
                exit_reason = f"🚀 TAKE PROFIT +{pnl:.1f}%"
            elif pnl <= HARD_STOP_PCT:
                should_exit = True
                exit_reason = f"🛑 STOP LOSS {pnl:.1f}%"
            elif hold_time > timedelta(hours=HOLD_MAX_HOURS):
                should_exit = True
                exit_reason = f"⏰ TIME STOP {hold_time.total_seconds()/3600:.1f}h"
            elif current['change_1h'] < -5:
                should_exit = True
                exit_reason = f"📉 REVERSAL {current['change_1h']:+.1f}% 1h"
            
            if should_exit:
                logger.info(f"{exit_reason} | {symbol}")
                
                # Execute sell swap
                success = await self.execute_jupiter_swap(
                    input_mint=pos.token_address,
                    output_mint=SOL_MINT,
                    amount_usd=pos.position_usd * (1 + pnl/100),
                    is_buy=False
                )
                
                if success:
                    self.total_pnl += pnl
                    to_remove.append(symbol)
                    logger.info(f"✅ CLOSED {symbol} | PnL: {pnl:+.1f}% | Total PnL: {self.total_pnl:+.1f}%")
        
        for symbol in to_remove:
            del self.positions[symbol]
    
    async def run(self):
        """Main loop."""
        if not await self.initialize():
            return
        
        logger.info("=" * 60)
        logger.info("🚀 LIVE EXECUTOR STARTED")
        logger.info("=" * 60)
        logger.info(f"Min position: ${MIN_POSITION_USD}")
        logger.info(f"Max position: ${MAX_POSITION_USD}")
        logger.info(f"Take profit: +{TAKE_PROFIT_PCT}%")
        logger.info(f"Stop loss: {HARD_STOP_PCT}%")
        logger.info(f"Max hold: {HOLD_MAX_HOURS}h")
        logger.info("")
        
        while True:
            try:
                await self.scan_and_trade()
                
                logger.info(f"⏳ Sleep {CHECK_INTERVAL_SEC}s...")
                await asyncio.sleep(CHECK_INTERVAL_SEC)
                
            except KeyboardInterrupt:
                logger.info("🛑 Shutting down...")
                break
            except Exception as e:
                logger.error(f"Loop error: {e}")
                await asyncio.sleep(10)
        
        if self.session:
            await self.session.close()
        
        logger.info("=" * 60)
        logger.info("📊 SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Trades: {self.trade_count}")
        logger.info(f"Open positions: {len(self.positions)}")
        logger.info(f"Total PnL: {self.total_pnl:+.1f}%")
        
        for symbol, pos in self.positions.items():
            logger.info(f"  OPEN: {symbol} @ ${pos.entry_price:.6f} | ${pos.position_usd:.0f}")


if __name__ == '__main__':
    import shutil
    if not shutil.which('solana'):
        print("❌ Solana CLI not found")
        print("Install: sh -c '$(curl -sSfL https://release.solana.com/v1.17.0/install)'")
        exit(1)
    
    print("⚠️  LIVE TRADING — REAL MONEY AT RISK")
    print("Press Ctrl+C within 3 seconds to abort...")
    
    try:
        import time
        time.sleep(3)
    except KeyboardInterrupt:
        print("\nAborted.")
        exit(0)
    
    executor = LiveExecutor()
    asyncio.run(executor.run())
