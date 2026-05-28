#!/usr/bin/env python3
"""
COPY TRADER — 90-day verified smart-money copy.

Edge thesis: top-leaderboard wallets ranked by 7-day PnL are survivorship-
biased lottery winners. Only wallets with ≥90 days of activity AND a sustained
profit factor >1.4 AND max DD <40% AND median hold >30 min show empirical
edge worth following.

Architecture:
  - strategies/whale_discovery.py builds and maintains the scoreboard
    (state/whale_scoreboard.json) on a weekly cycle.
  - This module is the runtime read path: it consumes the scoreboard, watches
    qualifying wallets for buys, and emits signals.

Public API (kept compatible with the existing main bot):
  - scan_whale_wallets()        → list of buy signals from qualified wallets
  - evaluate_copy_signal(sig, balance) → position dict or None
  - init_copy_trader()           → init hook

What's different from the old module:
  - DEFAULT_WALLETS stub removed; tracked wallets come exclusively from
    the scoreboard built by whale_discovery.
  - Pre-trade rug check + creator-behavior filter applied via anti_rug_suite
    before any copy buy is emitted.
  - Sizing capped at 1.5% bankroll AND 0.5× whale's allocation %, whichever
    is smaller (one whale concentration ≠ ours).
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger('CryptoBot')

_HERE = os.path.dirname(os.path.abspath(__file__))
SCOREBOARD_PATH = os.path.join(_HERE, 'state', 'whale_scoreboard.json')
SIGNAL_CACHE_PATH = os.path.join(_HERE, 'state', 'whale_signals_seen.json')

# Don't re-emit the same wallet→token signal within this window
SIGNAL_DEDUPE_MIN = 10
# Max copy-position size as fraction of our bankroll
MAX_COPY_PCT_OF_BANKROLL = 0.015
# Multiplier on whale's allocation% (we never go heavier than half what they did)
WHALE_ALLOCATION_MULTIPLIER = 0.5

_signals_seen: Dict[str, str] = {}  # f"{wallet}:{token}" → ISO timestamp


def _load_scoreboard() -> Dict:
    if not os.path.exists(SCOREBOARD_PATH):
        return {'wallets': {}}
    try:
        with open(SCOREBOARD_PATH) as f:
            return json.load(f)
    except Exception:
        return {'wallets': {}}


def _load_signals_cache():
    global _signals_seen
    if os.path.exists(SIGNAL_CACHE_PATH):
        try:
            with open(SIGNAL_CACHE_PATH) as f:
                _signals_seen = json.load(f)
        except Exception:
            _signals_seen = {}


def _save_signals_cache():
    os.makedirs(os.path.dirname(SIGNAL_CACHE_PATH), exist_ok=True)
    with open(SIGNAL_CACHE_PATH, 'w') as f:
        json.dump(_signals_seen, f)


def _dedupe_key(wallet: str, token: str) -> str:
    return f"{wallet}:{token}"


def _already_emitted(wallet: str, token: str) -> bool:
    key = _dedupe_key(wallet, token)
    ts_str = _signals_seen.get(key)
    if not ts_str:
        return False
    try:
        ts = datetime.fromisoformat(ts_str)
    except Exception:
        return False
    return datetime.now() - ts < timedelta(minutes=SIGNAL_DEDUPE_MIN)


def _mark_emitted(wallet: str, token: str):
    _signals_seen[_dedupe_key(wallet, token)] = datetime.now().isoformat()
    _save_signals_cache()


async def _fetch_wallet_recent_buys(session: aiohttp.ClientSession,
                                     wallet: str, lookback_min: int = 5) -> List[Dict]:
    """Fetch the wallet's most recent buys within the lookback window.

    Returns list of dicts with at least {token, token_address, price, size_usd, ts}.
    Uses Solscan public API; degrades gracefully on failure.
    """
    try:
        url = f"https://public-api.solscan.io/account/splTransfers"
        params = {'account': wallet, 'limit': 20}
        async with session.get(url, params=params, timeout=6) as resp:
            if resp.status != 200:
                return []
            txs = await resp.json()
            if not isinstance(txs, list):
                return []

        cutoff = datetime.now() - timedelta(minutes=lookback_min)
        buys = []
        for tx in txs:
            ts_raw = tx.get('blockTime') or tx.get('timestamp')
            try:
                ts = datetime.fromtimestamp(int(ts_raw))
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            # We're looking for "in" transfers (received tokens) which indicate buys
            if (tx.get('changeType') or '').lower() != 'inc':
                continue
            buys.append({
                'token': tx.get('tokenSymbol') or tx.get('symbol') or 'UNKNOWN',
                'token_address': tx.get('tokenAddress') or tx.get('mint'),
                'price': float(tx.get('priceUsd', 0) or 0),
                'size_usd': float(tx.get('changeAmount', 0)) * float(tx.get('priceUsd', 0) or 0),
                'ts': ts.isoformat(),
            })
        return buys
    except Exception as e:
        logger.debug(f"_fetch_wallet_recent_buys({wallet[:8]}…) failed: {e}")
        return []


async def scan_whale_wallets() -> List[Dict]:
    """
    Public API: returns list of buy-side signals from currently qualified wallets.
    Empty list means no qualifying activity in the lookback window.
    """
    board = _load_scoreboard()
    qualified = board.get('wallets', {})
    if not qualified:
        return []

    signals: List[Dict] = []
    async with aiohttp.ClientSession() as session:
        for wallet_addr, stats in qualified.items():
            buys = await _fetch_wallet_recent_buys(session, wallet_addr, lookback_min=5)
            for b in buys:
                token = b.get('token')
                if not token or b.get('price', 0) <= 0:
                    continue
                if _already_emitted(wallet_addr, token):
                    continue
                signals.append({
                    'wallet_id': wallet_addr,
                    'wallet_name': f"verified_{wallet_addr[:6]}",
                    'wallet_stats': stats,
                    'token': token,
                    'token_address': b.get('token_address'),
                    'price': b['price'],
                    'trade_size_usd': b.get('size_usd', 1000),
                    'ts': b.get('ts'),
                })
                _mark_emitted(wallet_addr, token)

    if signals:
        logger.info(f"🐋 copy_trader: {len(signals)} signal(s) from {len(qualified)} verified wallets")
    return signals


def _proportional_size(whale_allocation_pct: float, our_balance: float) -> float:
    """Half the whale's allocation, capped at MAX_COPY_PCT_OF_BANKROLL of our bankroll."""
    target_pct = whale_allocation_pct * WHALE_ALLOCATION_MULTIPLIER
    cap_pct = MAX_COPY_PCT_OF_BANKROLL
    return our_balance * min(target_pct, cap_pct)


async def evaluate_copy_signal(signal: Dict, our_balance: float) -> Optional[Dict]:
    """
    Evaluate a whale signal. Runs the rug-check pipeline before emitting.
    Returns a position dict ready for the main bot's entry path, or None.
    """
    token = signal.get('token')
    token_address = signal.get('token_address')
    if not token or not token_address or signal.get('price', 0) <= 0:
        return None

    # Rug-check the token before copying (the whale might be exit liquidity for a scam)
    try:
        from anti_rug_suite import run_full_rug_check
        rug = await run_full_rug_check(token_address)
        if not rug.get('safe'):
            logger.info(f"🚫 copy_trader skip {token}: rug flags={rug.get('flags', [])}")
            return None
    except ImportError:
        # Anti-rug unavailable → fail closed for the copy sleeve (the whole
        # point of this sleeve is high-quality entries)
        logger.warning("anti_rug_suite unavailable — skipping copy signal")
        return None

    # Heuristic: whale's allocation% assumed at 2% per trade for a 100K-portfolio whale
    # buying ~$2000 worth. Until we have on-chain portfolio sizing, default to a
    # conservative 1% whale allocation → 0.5% our allocation → capped at 1.5%.
    whale_size = signal.get('trade_size_usd', 0) or 1000
    assumed_whale_balance = 100_000.0
    whale_alloc = min(whale_size / assumed_whale_balance, 0.05)
    our_size = _proportional_size(whale_alloc, our_balance)
    if our_size < 1.0:
        return None

    return {
        'token': token,
        'address': token_address,
        'entry': signal['price'],
        'invested': our_size,
        'quantity': our_size / signal['price'] if signal['price'] > 0 else 0,
        'source': 'copy_trader',
        'wallet_id': signal.get('wallet_id'),
        'wallet_name': signal.get('wallet_name'),
        'whale_allocation': whale_alloc,
        'our_allocation': our_size / our_balance if our_balance > 0 else 0,
    }


async def init_copy_trader():
    """Init hook called by main bot. Loads signals cache; ensures scoreboard exists."""
    _load_signals_cache()
    board = _load_scoreboard()
    n_wallets = len(board.get('wallets', {}))
    if n_wallets == 0:
        logger.warning("copy_trader: 0 verified wallets — run strategies.whale_discovery.discover_and_score() "
                       "or add manual seeds to state/whale_scoreboard.json {'manual_seeds': [...]}.")
    else:
        logger.info(f"copy_trader initialized: {n_wallets} verified wallet(s) on scoreboard")


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sigs = asyncio.run(scan_whale_wallets())
    print(f"Signals: {len(sigs)}")
    for s in sigs[:5]:
        print(f"  {s['wallet_name']} bought {s['token']} @ ${s['price']:.6f}")
