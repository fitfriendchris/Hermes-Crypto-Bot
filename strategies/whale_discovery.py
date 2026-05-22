"""Sleeve B — Smart-money copy-trade source: 90-day verified whale discovery.

Why this matters:
  Public memecoin trader leaderboards are *survivorship-biased*. A wallet
  ranked top by 7-day PnL is mostly a lucky bagholder of one moonshot.
  The empirical edge survives only when whales are filtered by ≥90-day
  verified PnL, sustained profit factor, and behavioral signals that
  exclude MEV bots.

Pipeline:
  1. Source candidate wallets — top traders for each liquid pair from
     DexScreener / Birdeye (free tier).
  2. For each candidate, pull on-chain transaction history (Solscan).
  3. Roll up to a 90-day PnL ledger (rough — full PnL reconstruction is
     hard without a paid indexer; we approximate using closed positions).
  4. Filter: ≥90 days active, ≥30 closed trades, profit factor >1.4,
     max drawdown <40%, median hold time >30 min (excludes MEV bots).
  5. Score and write to state/whale_scoreboard.json. Re-score weekly.

This module is run as a background task by the main bot. It does NOT
place trades. The runtime read path (copy_trader.py) reads the scoreboard
and emits buy signals when a tracked wallet buys.
"""

import asyncio
import json
import logging
import os
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger('CryptoBot')

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCOREBOARD_PATH = os.path.join(_HERE, 'state', 'whale_scoreboard.json')
SCAN_PAIRS = [
    ('SOL',  'So11111111111111111111111111111111111111112'),
    ('JUP',  'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN'),
    ('BONK', 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263'),
    ('WIF',  'EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm'),
]

# Filter thresholds — survivorship-biased leaderboards fail at least one of these
MIN_ACTIVE_DAYS = 90
MIN_CLOSED_TRADES = 30
MIN_PROFIT_FACTOR = 1.4
MAX_DRAWDOWN_PCT = 0.40
MIN_MEDIAN_HOLD_MIN = 30   # MEV bots cycle in seconds — excludes them


async def _fetch_top_traders_dexscreener(session: aiohttp.ClientSession,
                                          token_address: str) -> List[str]:
    """DexScreener doesn't expose a per-token top-traders endpoint publicly.
    We approximate by pulling recent trades from the boosted-pairs endpoint
    and extracting unique large-buyer addresses. Returns a list of candidate
    wallet addresses (deduped).
    """
    candidates = set()
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with session.get(url, timeout=8) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            pairs = data.get('pairs', []) or []
            # DexScreener doesn't include trader addresses in the public feed,
            # so this scaffold returns an empty list. Real production deployment
            # would substitute Birdeye's /trader/top endpoint (paid) or
            # a Helius/Triton webhook over the pair's address.
            _ = pairs  # placeholder
    except Exception as e:
        logger.debug(f"dexscreener top-traders fetch failed: {e}")
    return list(candidates)


async def _fetch_wallet_history(session: aiohttp.ClientSession,
                                 wallet_address: str, limit: int = 200) -> List[Dict]:
    """Pull recent transactions for a wallet from Solscan public API.

    Returns parsed tx list; on failure returns []. We trade completeness for
    not needing a paid indexer — the 200-tx window covers ~30-180 days for
    most active wallets.
    """
    try:
        url = f"https://public-api.solscan.io/account/transactions"
        params = {'account': wallet_address, 'limit': limit}
        async with session.get(url, params=params, timeout=8) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.debug(f"solscan history fetch failed for {wallet_address[:8]}: {e}")
        return []


def _score_wallet(history: List[Dict]) -> Optional[Dict]:
    """
    Compute a wallet's score from its tx history. Returns None if the wallet
    fails any hard filter (not enough days, too few trades, MEV-bot pattern).

    NOTE: PnL accounting from raw Solscan tx lists is approximate — for
    production-grade PnL you want a paid indexer (Allium/Dune/Helius enriched).
    This scaffold establishes the FILTER pipeline. Until on-chain PnL is wired,
    wallets are emitted with `pnl_method = 'approximate'` and downstream caps
    sizing tighter (see copy_trader.py).
    """
    if not history:
        return None

    # Compute active-days span from first→last tx timestamp
    timestamps = []
    for tx in history:
        ts = tx.get('blockTime') or tx.get('timestamp')
        if ts:
            try:
                timestamps.append(int(ts))
            except (TypeError, ValueError):
                continue
    if not timestamps:
        return None
    first = min(timestamps)
    last = max(timestamps)
    active_days = (last - first) / 86400

    if active_days < MIN_ACTIVE_DAYS:
        return None
    if len(history) < MIN_CLOSED_TRADES:
        return None

    # Approximate hold time: median gap between consecutive timestamps
    sorted_ts = sorted(timestamps)
    gaps = [(sorted_ts[i+1] - sorted_ts[i]) / 60 for i in range(len(sorted_ts) - 1)]
    median_hold_min = statistics.median(gaps) if gaps else 0
    if median_hold_min < MIN_MEDIAN_HOLD_MIN:
        return None  # MEV-bot signature

    # Approximate PnL from fee + amount fields (rough — assumes most txs are swaps)
    # If pnl signs cancel, profit factor floors to 1.0 and the wallet won't qualify.
    realized = []
    for tx in history:
        # Solscan tx schema varies; extract any signed lamport delta
        change = tx.get('lamport') or tx.get('lamportChange') or 0
        try:
            realized.append(float(change) / 1e9)  # SOL units
        except (TypeError, ValueError):
            continue

    gains = sum(r for r in realized if r > 0)
    losses = abs(sum(r for r in realized if r < 0))
    if losses <= 0:
        profit_factor = float('inf') if gains > 0 else 0
    else:
        profit_factor = gains / losses

    if profit_factor < MIN_PROFIT_FACTOR:
        return None

    # Approximate max drawdown from running PnL
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in realized:
        running += r
        if running > peak:
            peak = running
        if peak > 0:
            dd = (peak - running) / peak
            if dd > max_dd:
                max_dd = dd
    if max_dd > MAX_DRAWDOWN_PCT:
        return None

    return {
        'active_days': active_days,
        'tx_count': len(history),
        'median_hold_min': median_hold_min,
        'profit_factor': profit_factor,
        'max_drawdown_pct': max_dd,
        'approx_pnl_sol': sum(realized),
        'pnl_method': 'approximate',
        'scored_at': datetime.utcnow().isoformat(),
    }


def _load_scoreboard() -> Dict:
    if not os.path.exists(SCOREBOARD_PATH):
        return {'wallets': {}, 'last_scan': None}
    try:
        with open(SCOREBOARD_PATH) as f:
            return json.load(f)
    except Exception:
        return {'wallets': {}, 'last_scan': None}


def _save_scoreboard(board: Dict):
    os.makedirs(os.path.dirname(SCOREBOARD_PATH), exist_ok=True)
    with open(SCOREBOARD_PATH, 'w') as f:
        json.dump(board, f, indent=2)


async def discover_and_score(seed_wallets: Optional[List[str]] = None) -> Dict:
    """
    Main discovery entry point. Pull candidate wallets, score them, write
    qualifying ones to scoreboard.

    seed_wallets: explicit list of wallets to evaluate (useful for backtest /
    manual curation). If None, pulls from PAIR_UNIVERSE — but note the
    DexScreener path returns empty until you wire Birdeye paid endpoint
    or your own indexer.
    """
    candidates: List[str] = list(seed_wallets or [])

    async with aiohttp.ClientSession() as session:
        if not candidates:
            for _sym, mint in SCAN_PAIRS:
                ws = await _fetch_top_traders_dexscreener(session, mint)
                candidates.extend(ws)
            candidates = list(dict.fromkeys(candidates))  # dedupe

        if not candidates:
            logger.warning("whale_discovery: no candidates — paid trader-leaderboard endpoint required for autodiscovery. "
                           "Add seed wallets manually to state/whale_scoreboard.json under 'manual_seeds'.")

        board = _load_scoreboard()
        # Merge manually-curated seeds
        manual = board.get('manual_seeds', []) or []
        for w in manual:
            if w not in candidates:
                candidates.append(w)

        qualified = {}
        for wallet in candidates:
            history = await _fetch_wallet_history(session, wallet)
            scored = _score_wallet(history)
            if scored:
                qualified[wallet] = scored
                logger.info(f"🐋 whale_discovery: qualified {wallet[:8]}… "
                            f"PF={scored['profit_factor']:.2f} "
                            f"DD={scored['max_drawdown_pct']:.0%} "
                            f"hold={scored['median_hold_min']:.0f}m "
                            f"days={scored['active_days']:.0f}")

    board['wallets'] = qualified
    board['last_scan'] = datetime.utcnow().isoformat()
    _save_scoreboard(board)
    return board


def get_active_wallets() -> Dict[str, Dict]:
    """Read-side accessor used by copy_trader at runtime."""
    return _load_scoreboard().get('wallets', {})


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    board = asyncio.run(discover_and_score())
    print(f"Scoreboard: {len(board['wallets'])} qualified wallet(s)")
    for addr, stats in board['wallets'].items():
        print(f"  {addr[:8]}…: PF={stats['profit_factor']:.2f} "
              f"DD={stats['max_drawdown_pct']:.0%} txs={stats['tx_count']}")
