#!/usr/bin/env python3
"""
WALLET LEADERBOARD — Pre-scored seeds from public sources
Bypasses complex on-chain parsing by using verified public data.

Sources:
- Subglow 30d leaderboard (scraped 2026-05-27)
- Kolscan.io rankings

These wallets have proven track records. The bot monitors them for new buys.
"""

# Top wallets from Subglow 30d leaderboard (verified 2026-05-27)
# Format: wallet, name, 30d_pnl_sol, win_rate, trades, tier
LEADERBOARD = [
    {
        "wallet": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        "name": "Cented",
        "pnl_30d_sol": 252.75,
        "pnl_30d_usd": 20804,
        "win_rate": 0.58,
        "wins": 137,
        "losses": 99,
        "tier": "A+",
        "source": "subglow",
        "rank": 1,
    },
    {
        "wallet": "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
        "name": "theo",
        "pnl_30d_sol": 150.3,
        "pnl_30d_usd": 12600,
        "win_rate": 0.55,
        "wins": 89,
        "losses": 73,
        "tier": "A+",
        "source": "subglow",
        "rank": 2,
    },
    {
        "wallet": "525LueqAyZJueCoiisfWy6nyh4MTvmF4X9jSqi6efXJT",
        "name": "Joji",
        "pnl_30d_sol": 98.5,
        "pnl_30d_usd": 8250,
        "win_rate": 0.52,
        "wins": 67,
        "losses": 62,
        "tier": "A",
        "source": "subglow",
        "rank": 3,
    },
    {
        "wallet": "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",
        "name": "Jijo",
        "pnl_30d_sol": 76.2,
        "pnl_30d_usd": 6390,
        "win_rate": 0.51,
        "wins": 54,
        "losses": 52,
        "tier": "A",
        "source": "subglow",
        "rank": 4,
    },
    {
        "wallet": "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        "name": "decu",
        "pnl_30d_sol": 62.1,
        "pnl_30d_usd": 5210,
        "win_rate": 0.49,
        "wins": 48,
        "losses": 50,
        "tier": "B+",
        "source": "subglow",
        "rank": 5,
    },
]


def get_mirrors(min_pnl: float = 50.0, min_win_rate: float = 0.50) -> list:
    """Return wallets that meet mirror criteria."""
    return [
        w for w in LEADERBOARD
        if w["pnl_30d_sol"] >= min_pnl and w["win_rate"] >= min_win_rate
    ]


def get_all() -> list:
    """Return all tracked wallets."""
    return LEADERBOARD


def get_by_wallet(wallet: str) -> dict:
    """Get wallet info by address."""
    for w in LEADERBOARD:
        if w["wallet"] == wallet:
            return w
    return {}
