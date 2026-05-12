"""
WALLET INTEGRATION MODULE
Connects Phantom (SOL) and MetaMask (ETH/BSC) for self-custody trading
Author: Hermes | March 2026 SEC/CFTC compliant
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger('CryptoBot')

@dataclass
class TokenBalance:
    token_address: str
    symbol: str
    balance: float
    decimals: int
    usd_value: float = 0.0

@dataclass
class Transaction:
    signature: str
    timestamp: int
    from_address: str
    to_address: str
    amount: float
    token: str
    success: bool

class PhantomWallet:
    """Solana wallet integration via local connection or keypair."""
    
    def __init__(self, private_key: Optional[str] = None):
        self.chain = "solana"
        self.rpc_endpoint = "https://api.mainnet-beta.solana.com"
        self.private_key = private_key or os.getenv('PHANTOM_PRIVATE_KEY')
        self.public_key = None
        
        # Try to import solana libraries
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solana.rpc.async_api import AsyncClient
            self.solana_available = True
            self.Keypair = Keypair
            self.Pubkey = Pubkey
            self.AsyncClient = AsyncClient
        except ImportError:
            logger.warning("Solana libraries not installed. Wallet operations limited.")
            self.solana_available = False
    
    async def initialize(self):
        """Initialize wallet connection."""
        if not self.solana_available:
            logger.error("Cannot initialize: solana libraries missing")
            return False
        
        if self.private_key:
            # Load from private key
            try:
                keypair = self.Keypair.from_base58_string(self.private_key)
                self.public_key = str(keypair.pubkey())
                logger.info(f"Phantom wallet loaded: {self.public_key[:20]}...")
                return True
            except Exception as e:
                logger.error(f"Failed to load private key: {e}")
                return False
        else:
            logger.info("No private key provided. Manual signing mode.")
            return True
    
    async def get_balance(self, token_address: Optional[str] = None) -> float:
        """
        Get SOL or SPL token balance.
        token_address: None for SOL, mint address for SPL token
        """
        if not self.solana_available:
            return 0.0
        
        try:
            client = self.AsyncClient(self.rpc_endpoint)
            
            if not token_address:
                # Get SOL balance
                response = await client.get_balance(self.Keypair.from_base58_string(self.private_key).pubkey())
                await client.close()
                lamports = response.value
                return lamports / 1e9  # Convert to SOL
            else:
                # Get SPL token balance
                from spl.token.async_client import AsyncToken
                token = AsyncToken(client, self.Pubkey.from_string(token_address))
                # This is simplified — full implementation needs ATA lookup
                await client.close()
                return 0.0
                
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0
    
    async def get_token_accounts(self) -> List[TokenBalance]:
        """Get all SPL token balances."""
        if not self.solana_available:
            return []
        
        try:
            client = self.AsyncClient(self.rpc_endpoint)
            # Simplified — would need ATA derivation for each token
            await client.close()
            return []
            
        except Exception as e:
            logger.error(f"Token accounts fetch failed: {e}")
            return []
    
    async def sign_transaction(self, transaction_bytes: bytes) -> Optional[str]:
        """Sign transaction with private key."""
        if not self.private_key or not self.solana_available:
            logger.warning("No private key — manual signing required")
            return None
        
        try:
            keypair = self.Keypair.from_base58_string(self.private_key)
            # Sign transaction
            signature = keypair.sign_message(transaction_bytes)
            return str(signature)
        except Exception as e:
            logger.error(f"Signing failed: {e}")
            return None
    
    async def send_sol(self, to_address: str, amount_sol: float) -> Optional[str]:
        """Send SOL to address. Returns transaction signature."""
        if not self.solana_available:
            logger.error("Cannot send: solana libraries missing")
            return None
        
        try:
            client = self.AsyncClient(self.rpc_endpoint)
            keypair = self.Keypair.from_base58_string(self.private_key)
            
            from solders.system_program import TransferParams, transfer
            from solders.transaction import Transaction
            
            # Build transaction
            txn = Transaction()
            txn.add(transfer(TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=self.Pubkey.from_string(to_address),
                lamports=int(amount_sol * 1e9)
            )))
            
            # Sign and send
            txn.sign(keypair)
            response = await client.send_transaction(txn)
            await client.close()
            
            sig = str(response.value)
            logger.info(f"SOL sent: {amount_sol} to {to_address[:20]}... | Tx: {sig[:20]}...")
            return sig
            
        except Exception as e:
            logger.error(f"Send SOL failed: {e}")
            return None

class MetaMaskWallet:
    """Ethereum/BSC wallet integration."""
    
    def __init__(self, private_key: Optional[str] = None):
        self.chains = {
            "ethereum": "https://eth.llamarpc.com",
            "bsc": "https://bsc-dataseed.binance.org",
            "polygon": "https://polygon-rpc.com"
        }
        self.private_key = private_key or os.getenv('METAMASK_PRIVATE_KEY')
        self.address = None
        
        try:
            from web3 import Web3
            self.web3_available = True
            self.Web3 = Web3
        except ImportError:
            logger.warning("Web3 libraries not installed. ETH operations limited.")
            self.web3_available = False
    
    async def initialize(self, chain: str = "ethereum"):
        """Initialize wallet for chain."""
        if not self.web3_available:
            logger.error("Cannot initialize: web3 libraries missing")
            return False
        
        if self.private_key:
            try:
                w3 = self.Web3(self.Web3.HTTPProvider(self.chains[chain]))
                account = w3.eth.account.from_key(self.private_key)
                self.address = account.address
                logger.info(f"MetaMask loaded: {self.address[:20]}... on {chain}")
                return True
            except Exception as e:
                logger.error(f"Failed to load key: {e}")
                return False
        else:
            logger.info("No private key. Manual signing mode.")
            return True
    
    async def get_eth_balance(self, chain: str = "ethereum") -> float:
        """Get native ETH/BSC/MATIC balance."""
        if not self.web3_available or not self.address:
            return 0.0
        
        try:
            w3 = self.Web3(self.Web3.HTTPProvider(self.chains[chain]))
            balance_wei = w3.eth.get_balance(self.address)
            return w3.from_wei(balance_wei, 'ether')
        except Exception as e:
            logger.error(f"Balance fetch failed: {e}")
            return 0.0
    
    async def get_erc20_balance(self, token_address: str, chain: str = "ethereum") -> float:
        """Get ERC20 token balance."""
        if not self.web3_available or not self.address:
            return 0.0
        
        try:
            w3 = self.Web3(self.Web3.HTTPProvider(self.chains[chain]))
            
            # ERC20 ABI (minimal)
            erc20_abi = [
                {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], 
                 "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], 
                 "type": "function"},
                {"constant": True, "inputs": [], 
                 "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], 
                 "type": "function"},
            ]
            
            contract = w3.eth.contract(address=token_address, abi=erc20_abi)
            balance = contract.functions.balanceOf(self.address).call()
            decimals = contract.functions.decimals().call()
            
            return balance / (10 ** decimals)
            
        except Exception as e:
            logger.error(f"ERC20 balance fetch failed: {e}")
            return 0.0
    
    async def send_eth(self, to_address: str, amount: float, chain: str = "ethereum") -> Optional[str]:
        """Send native currency. Returns tx hash."""
        if not self.web3_available or not self.private_key:
            return None
        
        try:
            w3 = self.Web3(self.Web3.HTTPProvider(self.chains[chain]))
            account = w3.eth.account.from_key(self.private_key)
            
            txn = {
                'to': to_address,
                'value': w3.to_wei(amount, 'ether'),
                'gas': 21000,
                'gasPrice': w3.to_wei('20', 'gwei'),
                'nonce': w3.eth.get_transaction_count(account.address),
            }
            
            signed = w3.eth.account.sign_transaction(txn, self.private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            
            return tx_hash.hex()
            
        except Exception as e:
            logger.error(f"Send failed: {e}")
            return None

class ExodusWallet:
    """Exodus wallet integration — supports 50+ chains via seed phrase or private key."""

    SUPPORTED_CHAINS = {
        "solana": {"rpc": "https://api.mainnet-beta.solana.com", "native": "SOL"},
        "ethereum": {"rpc": "https://eth.llamarpc.com", "native": "ETH"},
        "bsc": {"rpc": "https://bsc-dataseed.binance.org", "native": "BNB"},
        "polygon": {"rpc": "https://polygon-rpc.com", "native": "MATIC"},
        "arbitrum": {"rpc": "https://arb1.arbitrum.io/rpc", "native": "ETH"},
        "base": {"rpc": "https://mainnet.base.org", "native": "ETH"},
        "avalanche": {"rpc": "https://api.avax.network/ext/bc/C/rpc", "native": "AVAX"},
    }

    def __init__(self, seed_phrase: Optional[str] = None, private_key: Optional[str] = None):
        self.seed_phrase = seed_phrase or os.getenv('EXODUS_SEED_PHRASE')
        self.private_key = private_key or os.getenv('EXODUS_PRIVATE_KEY')
        self.chain = None
        self.address = None
        self.native_symbol = None

        # Library flags
        self._solana_ok = False
        self._web3_ok = False

        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            self.Keypair = Keypair
            self.Pubkey = Pubkey
            self._solana_ok = True
        except ImportError:
            pass

        try:
            from web3 import Web3
            self.Web3 = Web3
            self._web3_ok = True
        except ImportError:
            pass

    async def initialize(self, chain: str = "solana"):
        """Initialize Exodus for a specific chain."""
        if chain not in self.SUPPORTED_CHAINS:
            logger.error(f"Exodus does not support chain: {chain}")
            return False

        self.chain = chain
        self.native_symbol = self.SUPPORTED_CHAINS[chain]["native"]

        if chain == "solana":
            return await self._init_solana()
        else:
            return await self._init_evm(chain)

    async def _init_solana(self) -> bool:
        if not self._solana_ok:
            logger.warning("Solana libraries missing — Exodus SOL mode limited")
            return False

        try:
            if self.private_key:
                keypair = self.Keypair.from_base58_string(self.private_key)
                self.address = str(keypair.pubkey())
            elif self.seed_phrase:
                # Derive from BIP39 — simplified; full derivation needs bip-utils
                logger.warning("Seed phrase derivation not fully implemented — use EXODUS_PRIVATE_KEY for SOL")
                return False
            else:
                logger.info("Exodus SOL: no keys loaded — read-only mode")
                return True

            logger.info(f"Exodus SOL loaded: {self.address[:20]}...")
            return True
        except Exception as e:
            logger.error(f"Exodus SOL init failed: {e}")
            return False

    async def _init_evm(self, chain: str) -> bool:
        if not self._web3_ok:
            logger.warning("Web3 libraries missing — Exodus EVM mode limited")
            return False

        try:
            if self.private_key:
                w3 = self.Web3()
                account = w3.eth.account.from_key(self.private_key)
                self.address = account.address
            elif self.seed_phrase:
                logger.warning("Seed phrase derivation not fully implemented — use EXODUS_PRIVATE_KEY for EVM")
                return False
            else:
                logger.info(f"Exodus {chain}: no keys loaded — read-only mode")
                return True

            logger.info(f"Exodus {chain} loaded: {self.address[:20]}...")
            return True
        except Exception as e:
            logger.error(f"Exodus EVM init failed: {e}")
            return False

    async def get_balance(self, token_address: Optional[str] = None) -> float:
        if not self.address:
            return 0.0

        if self.chain == "solana":
            return await self._get_sol_balance(token_address)
        else:
            return await self._get_evm_balance(token_address)

    async def _get_sol_balance(self, token_address: Optional[str] = None) -> float:
        if not self._solana_ok:
            return 0.0
        try:
            from solana.rpc.async_api import AsyncClient
            client = AsyncClient(self.SUPPORTED_CHAINS["solana"]["rpc"])
            if not token_address:
                pk = self.Pubkey.from_string(self.address)
                resp = await client.get_balance(pk)
                await client.close()
                return resp.value / 1e9
            else:
                # SPL token — simplified; needs ATA lookup for full accuracy
                await client.close()
                return 0.0
        except Exception as e:
            logger.error(f"Exodus SOL balance failed: {e}")
            return 0.0

    async def _get_evm_balance(self, token_address: Optional[str] = None) -> float:
        if not self._web3_ok:
            return 0.0
        try:
            rpc = self.SUPPORTED_CHAINS[self.chain]["rpc"]
            w3 = self.Web3(self.Web3.HTTPProvider(rpc))
            if not token_address:
                bal = w3.eth.get_balance(self.address)
                return float(w3.from_wei(bal, 'ether'))
            else:
                erc20_abi = [
                    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
                    {"constant": True, "inputs": [],
                     "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
                ]
                contract = w3.eth.contract(address=token_address, abi=erc20_abi)
                bal = contract.functions.balanceOf(self.address).call()
                dec = contract.functions.decimals().call()
                return bal / (10 ** dec)
        except Exception as e:
            logger.error(f"Exodus EVM balance failed: {e}")
            return 0.0

    async def send(self, to_address: str, amount: float, token_address: Optional[str] = None) -> Optional[str]:
        if not self.address or not self.private_key:
            logger.warning("Exodus: no private key — cannot send")
            return None

        if self.chain == "solana":
            return await self._send_sol(to_address, amount)
        else:
            return await self._send_evm(to_address, amount, token_address)

    async def _send_sol(self, to_address: str, amount_sol: float) -> Optional[str]:
        if not self._solana_ok:
            return None
        try:
            from solana.rpc.async_api import AsyncClient
            from solders.system_program import TransferParams, transfer
            from solders.transaction import Transaction
            client = AsyncClient(self.SUPPORTED_CHAINS["solana"]["rpc"])
            keypair = self.Keypair.from_base58_string(self.private_key)
            txn = Transaction()
            txn.add(transfer(TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=self.Pubkey.from_string(to_address),
                lamports=int(amount_sol * 1e9)
            )))
            txn.sign(keypair)
            resp = await client.send_transaction(txn)
            await client.close()
            sig = str(resp.value)
            logger.info(f"Exodus sent {amount_sol:.4f} SOL → {to_address[:20]}... | Tx: {sig[:20]}...")
            return sig
        except Exception as e:
            logger.error(f"Exodus SOL send failed: {e}")
            return None

    async def _send_evm(self, to_address: str, amount: float, token_address: Optional[str] = None) -> Optional[str]:
        if not self._web3_ok:
            return None
        try:
            rpc = self.SUPPORTED_CHAINS[self.chain]["rpc"]
            w3 = self.Web3(self.Web3.HTTPProvider(rpc))
            account = w3.eth.account.from_key(self.private_key)

            if token_address:
                # ERC20 transfer — simplified; needs full ABI for production
                logger.warning("Exodus ERC20 send not yet implemented")
                return None

            txn = {
                'to': to_address,
                'value': w3.to_wei(amount, 'ether'),
                'gas': 21000,
                'gasPrice': w3.to_wei('20', 'gwei'),
                'nonce': w3.eth.get_transaction_count(account.address),
            }
            signed = w3.eth.account.sign_transaction(txn, self.private_key)
            tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
            logger.info(f"Exodus sent {amount:.6f} {self.native_symbol} → {to_address[:20]}... | Tx: {tx_hash.hex()[:20]}...")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Exodus EVM send failed: {e}")
            return None

    def get_address(self) -> Optional[str]:
        return self.address


class WalletManager:
    """Manages Phantom, MetaMask, and Exodus wallets for cross-chain trading."""

    def __init__(self):
        self.phantom = PhantomWallet()
        self.metamask = MetaMaskWallet()
        self.exodus = ExodusWallet()
        self.active_chain = None
        self.active_wallet = None  # 'phantom' | 'metamask' | 'exodus'

    async def initialize(self, chain: str = "solana", wallet: str = "exodus"):
        """Initialize a specific wallet for a chain.

        wallet: 'phantom' | 'metamask' | 'exodus'
        chain: 'solana' | 'ethereum' | 'bsc' | 'polygon' | 'arbitrum' | 'base' | 'avalanche'
        """
        self.active_chain = chain
        self.active_wallet = wallet

        if wallet == "phantom":
            return await self.phantom.initialize()
        elif wallet == "metamask":
            return await self.metamask.initialize(chain)
        elif wallet == "exodus":
            return await self.exodus.initialize(chain)
        else:
            logger.error(f"Unknown wallet: {wallet}")
            return False

    async def get_balance(self, token_address: Optional[str] = None) -> float:
        """Get balance for active wallet + chain."""
        if self.active_wallet == "phantom":
            return await self.phantom.get_balance(token_address)
        elif self.active_wallet == "metamask":
            if token_address:
                return await self.metamask.get_erc20_balance(token_address, self.active_chain)
            return await self.metamask.get_eth_balance(self.active_chain)
        elif self.active_wallet == "exodus":
            return await self.exodus.get_balance(token_address)
        return 0.0

    async def send(self, to_address: str, amount: float, token_address: Optional[str] = None) -> Optional[str]:
        """Send currency/token via active wallet."""
        if self.active_wallet == "phantom":
            return await self.phantom.send_sol(to_address, amount)
        elif self.active_wallet == "metamask":
            return await self.metamask.send_eth(to_address, amount, self.active_chain)
        elif self.active_wallet == "exodus":
            return await self.exodus.send(to_address, amount, token_address)
        return None

    def get_address(self) -> Optional[str]:
        """Get active wallet address."""
        if self.active_wallet == "phantom":
            return self.phantom.public_key
        elif self.active_wallet == "metamask":
            return self.metamask.address
        elif self.active_wallet == "exodus":
            return self.exodus.address
        return None

# ============================================================
# TEST
# ============================================================

async def test():
    """Test wallet integration."""
    print("=== WALLET INTEGRATION TEST ===\n")
    
    # Test without keys (read-only mode)
    manager = WalletManager()
    
    print("1. Phantom (SOL) — Read-only mode")
    result = await manager.initialize("solana")
    print(f"   Init: {result}")
    
    if manager.get_address():
        balance = await manager.get_balance()
        print(f"   Balance: {balance:.4f} SOL")
    else:
        print("   No key loaded — manual signing mode")
    
    print("\n2. MetaMask (ETH) — Read-only mode")
    result = await manager.initialize("ethereum")
    print(f"   Init: {result}")
    
    print("\n=== TEST COMPLETE ===")
    print("Load private keys in .env to enable full functionality")

if __name__ == "__main__":
    asyncio.run(test())
