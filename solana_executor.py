#!/usr/bin/env python3
"""
SOLANA MOMENTUM EXECUTOR — FULL LIVE MODE
Real Jupiter API v6 swaps signed with wallet keys from .env

CRITICAL: This executes real trades. Only run if funded + understand risks.
"""

import os
import json
import base64
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass

import aiohttp
import requests

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
JUPITER_SWAP_API = 'https://api.jup.ag/swap/v1'

SOL_MINT = 'So11111111111111111111111111111111111111112'
USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

# Load private key from .env
PRIV_KEY = os.getenv('PHANTOM_PRIVATE_KEY') or os.getenv('EXODUS_PRIVATE_KEY') or os.getenv('SOLANA_PRIVATE_KEY', '')


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
    """Live Solana executor with real wallet signing."""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.wallet = None
        self.wallet_pubkey: Optional[str] = None
        self.sol_balance: float = 0.0
        self.trade_count = 0
        self.total_pnl = 0.0
    
    def load_wallet(self):
        """Load wallet from private key string in .env."""
        if not PRIV_KEY:
            logger.error("No private key found in .env")
            logger.error("Set PHANTOM_PRIVATE_KEY or EXODUS_PRIVATE_KEY")
            return False
        try:
            import base58
            from solders.keypair import Keypair
            key_bytes = base58.b58decode(PRIV_KEY)
            self.wallet = Keypair.from_bytes(key_bytes)
            self.wallet_pubkey = str(self.wallet.pubkey())
            logger.info(f"🗝️  Wallet: {self.wallet_pubkey}")
            return True
        except Exception as e:
            logger.error(f"Failed to load wallet: {e}")
            return False
    
    async def initialize(self) -> bool:
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15),
            headers={'Accept': 'application/json'}
        )
        if not self.load_wallet():
            return False
        await self.update_balance()
        return True
    
    async def update_balance(self):
        try:
            from solana.rpc.api import Client
            rpc = Client(SOLANA_RPC)
            resp = rpc.get_balance(self.wallet.pubkey())
            if resp.value is not None:
                self.sol_balance = resp.value / 1e9
                sol_price = await self.get_sol_price()
                logger.info(f"💰 Balance: {self.sol_balance:.4f} SOL (${self.sol_balance * sol_price:.2f})")
        except Exception as e:
            logger.warning(f"Balance check failed: {e}")
    
    async def get_sol_price(self) -> float:
        try:
            async with self.session.get(
                'https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd',
                timeout=5
            ) as resp:
                data = await resp.json()
                return data['solana']['usd']
        except:
            return 160.0
    
    async def get_trending_tokens(self) -> List[Dict]:
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
        max_pos = liquidity * 0.02
        for size in [MAX_POSITION_USD, 20.0, 10.0, 5.0]:
            slip = (size / liquidity) * 100
            if slip <= MAX_SLIPPAGE_PCT and size <= max_pos:
                breakeven = (slip * 2) + (TAKER_FEE * 2 * 100)
                if momentum >= breakeven + 5.0:
                    return size
        return 0.0
    
    async def execute_jupiter_swap(self, input_mint: str, output_mint: str, 
                                   amount_usd: float, is_buy: bool = True) -> bool:
        """Execute Jupiter swap via API v6 with real signing."""
        direction = "BUY" if is_buy else "SELL"
        logger.info(f"🔄 {direction} ${amount_usd:.2f}...")
        
        try:
            import base58
            from solders.transaction import VersionedTransaction
            from solana.rpc.api import Client
            from solana.rpc.types import TxOpts
        except ImportError as e:
            logger.error(f"Missing deps: {e}. Run: pip install base58 solders solana")
            return False
        
        # Convert USD amount to lamports
        sol_price = await self.get_sol_price()
        amount_sol = amount_usd / sol_price
        amount_lamports = int(amount_sol * 1e9)
        
        # Step 1: Get Jupiter quote
        quote_params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': str(amount_lamports),
            'slippageBps': str(int(MAX_SLIPPAGE_PCT * 100)),
        }
        
        try:
            async with self.session.get(
                f"{JUPITER_QUOTE_API}/quote", params=quote_params, timeout=10
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Quote failed: HTTP {resp.status}")
                    return False
                quote = await resp.json()
                if 'error' in quote:
                    logger.error(f"Quote error: {quote['error']}")
                    return False
        except Exception as e:
            logger.error(f"Quote request failed: {e}")
            return False
        
        # Step 2: Build swap transaction
        swap_body = {
            'quoteResponse': quote,
            'userPublicKey': self.wallet_pubkey,
            'wrapAndUnwrapSol': True,
            'dynamicComputeUnitLimit': True,
            'dynamicSlippage': True,
        }
        
        try:
            async with self.session.post(
                f"{JUPITER_SWAP_API}/swap", json=swap_body, timeout=15
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Swap build failed: HTTP {resp.status}")
                    return False
                swap_data = await resp.json()
                if 'error' in swap_data:
                    logger.error(f"Swap build error: {swap_data['error']}")
                    return False
        except Exception as e:
            logger.error(f"Swap build request failed: {e}")
            return False
        
        # Step 3: Deserialize, sign, and send
        try:
            tx_base64 = swap_data['swapTransaction']
            tx_bytes = base64.b64decode(tx_base64)
            raw_tx = VersionedTransaction.from_bytes(tx_bytes)
            
            # Find wallet index in account keys
            account_keys = list(raw_tx.message.account_keys)
            wallet_idx = account_keys.index(self.wallet.pubkey())
            
            # Replace placeholder signature
            signers = list(raw_tx.signatures)
            signers[wallet_idx] = self.wallet
            
            signed_tx = VersionedTransaction(raw_tx.message, signers)
            
            # Send via RPC
            rpc = Client(SOLANA_RPC)
            result = rpc.send_transaction(signed_tx, opts=TxOpts(skip_preflight=False))
            
            signature = str(result.value)
            logger.info(f"✅ SWAP EXECUTED | {direction} | Tx: {signature}")
            logger.info(f"   View: https://solscan.io/tx/{signature}")
            return True
            
        except Exception as e:
            logger.error(f"Swap sign/send failed: {e}")
            return False
    
    async def scan_and_trade(self):
        """Main scan + trade loop."""
        logger.info("=" * 60)
        logger.info("🔍 SCANNING")
        logger.info("=" * 60)
        
        tokens = await self.get_trending_tokens()
        await self.check_exits(tokens)
        
        for token in tokens:
            symbol = token['symbol']
            address = token['address']
            liq = token['liquidity']
            m1 = token['change_1h']
            m6 = token['change_6h']
            vol = token['volume_24h']
            
            if symbol in self.positions or not address:
                continue
            if liq < MIN_LIQUIDITY_USD or m1 < MIN_MOMENTUM_1H or m6 < MIN_MOMENTUM_6H or vol < 100000:
                continue
            
            pos_size = self.calculate_position_size(liq, m1)
            if pos_size < MIN_POSITION_USD:
                continue
            
            await self.update_balance()
            sol_price = await self.get_sol_price()
            if self.sol_balance * sol_price < pos_size:
                logger.warning(f"Insufficient balance for ${pos_size:.0f} trade")
                continue
            
            logger.info(f"🎯 SIGNAL | {symbol} | +{m1:.1f}% 1h | ${liq:,.0f} liq | ${pos_size:.0f}")
            
            success = await self.execute_jupiter_swap(
                input_mint=SOL_MINT, output_mint=address, amount_usd=pos_size, is_buy=True
            )
            
            if success:
                self.positions[symbol] = Position(
                    symbol=symbol, token_address=address,
                    entry_price=token['price'], position_usd=pos_size,
                    entry_time=datetime.now(), highest_seen=token['price']
                )
                self.trade_count += 1
                logger.info(f"✅ POSITION OPEN | {symbol} @ ${token['price']:.6f}")
                break
    
    async def check_exits(self, tokens: List[Dict]):
        """Check and execute exits."""
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
            
            logger.info(f"📊 {symbol} | Entry: ${pos.entry_price:.6f} | Now: ${current_price:.6f} | PnL: {pnl:+.1f}%")
            
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
                exit_reason = f"⏰ TIME STOP"
            elif current['change_1h'] < -5:
                should_exit = True
                exit_reason = f"📉 REVERSAL"
            
            if should_exit:
                logger.info(f"{exit_reason} | {symbol}")
                success = await self.execute_jupiter_swap(
                    input_mint=pos.token_address, output_mint=SOL_MINT,
                    amount_usd=pos.position_usd * (1 + pnl/100), is_buy=False
                )
                if success:
                    self.total_pnl += pnl
                    to_remove.append(symbol)
                    logger.info(f"✅ CLOSED {symbol} | PnL: {pnl:+.1f}% | Total: {self.total_pnl:+.1f}%")
        
        for symbol in to_remove:
            del self.positions[symbol]
    
    async def run(self):
        if not await self.initialize():
            return
        
        logger.info("=" * 60)
        logger.info("🚀 LIVE EXECUTOR STARTED")
        logger.info("=" * 60)
        logger.info(f"Min position: ${MIN_POSITION_USD} | Max: ${MAX_POSITION_USD}")
        logger.info(f"TP: +{TAKE_PROFIT_PCT}% | SL: {HARD_STOP_PCT}% | Max hold: {HOLD_MAX_HOURS}h")
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
        logger.info(f"📊 SESSION | Trades: {self.trade_count} | Open: {len(self.positions)} | PnL: {self.total_pnl:+.1f}%")


if __name__ == '__main__':
    if not PRIV_KEY:
        print("❌ No wallet key found in .env")
        print("Set PHANTOM_PRIVATE_KEY or EXODUS_PRIVATE_KEY")
        exit(1)
    
    print("⚠️  LIVE MODE — REAL MONEY AT RISK")
    print("Press Ctrl+C within 3s to abort...")
    try:
        import time
        time.sleep(3)
    except KeyboardInterrupt:
        print("\nAborted.")
        exit(0)
    
    executor = LiveExecutor()
    asyncio.run(executor.run())
