#!/usr/bin/env python3
"""
WALLET LEADERBOARD — Pre-scored seeds from public sources
Bypasses complex on-chain parsing by using verified public data.

Sources:
- Subglow 30d + 7d leaderboards (scraped 2026-05-28)
- Kolscan.io rankings

These wallets have proven track records. The bot monitors them for new buys.
"""

# Top wallets from Subglow leaderboards (verified 2026-05-28)
LEADERBOARD = [
    {
        "wallet": "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o",
        "name": "Cented",
        "pnl_30d_sol": 4954.29,
        "pnl_7d_sol": 1273.90,
        "pnl_30d_usd": 415418,
        "win_rate": 0.58,
        "wins": 137,
        "losses": 99,
        "tier": "A+",
        "source": "subglow",
        "rank_30d": 1,
        "rank_7d": 1,
    },
    {
        "wallet": "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
        "name": "theo",
        "pnl_30d_sol": 3219.31,
        "pnl_7d_sol": 704.72,
        "pnl_30d_usd": 269939,
        "win_rate": 0.55,
        "wins": 89,
        "losses": 73,
        "tier": "A+",
        "source": "subglow",
        "rank_30d": 2,
        "rank_7d": 2,
    },
    {
        "wallet": "DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm",
        "name": "Gake",
        "pnl_30d_sol": 1502.95,
        "pnl_7d_sol": 480.08,
        "pnl_30d_usd": 126022,
        "win_rate": 0.52,
        "wins": 67,
        "losses": 62,
        "tier": "A",
        "source": "subglow",
        "rank_30d": 16,
        "rank_7d": 3,
    },
    {
        "wallet": "AuPp4YTMTyqxYXQnHc5KUc6pUuCSsHQpBJhgnD45yqrf",
        "name": "Dani",
        "pnl_30d_sol": 472.56,
        "pnl_7d_sol": 479.45,
        "pnl_30d_usd": 39625,
        "win_rate": 0.51,
        "wins": 54,
        "losses": 52,
        "tier": "A",
        "source": "subglow",
        "rank_30d": 13,
        "rank_7d": 4,
    },
    {
        "wallet": "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9",
        "name": "decu",
        "pnl_30d_sol": 1253.24,
        "pnl_7d_sol": 425.40,
        "pnl_30d_usd": 105084,
        "win_rate": 0.49,
        "wins": 48,
        "losses": 50,
        "tier": "B+",
        "source": "subglow",
        "rank_30d": 5,
        "rank_7d": 5,
    },
    {
        "wallet": "5t9xBNuDdGTGpjaPTx6hKd7sdRJbvtKS8Mhq6qVbo8Qz",
        "name": "Smokez",
        "pnl_30d_sol": 419.37,
        "pnl_7d_sol": 416.97,
        "pnl_30d_usd": 35165,
        "win_rate": 0.49,
        "wins": 48,
        "losses": 50,
        "tier": "B+",
        "source": "subglow",
        "rank_30d": 14,
        "rank_7d": 6,
    },
    {
        "wallet": "5ZuV8eqkvzYFVEKbLvGBdexL2tFv7E5BCd2HZpjqbdg",
        "name": "Doji",
        "pnl_30d_sol": 1184.76,
        "pnl_7d_sol": 361.92,
        "pnl_30d_usd": 99342,
        "win_rate": 0.48,
        "wins": 45,
        "losses": 49,
        "tier": "B+",
        "source": "subglow",
        "rank_30d": 6,
        "rank_7d": 7,
    },
    {
        "wallet": "4BdKaxN8G6ka4GYtQQWk4G4dZRUTX2vQH9GcXdBREFUk",
        "name": "Jijo",
        "pnl_30d_sol": 1331.48,
        "pnl_7d_sol": 310.91,
        "pnl_30d_usd": 111645,
        "win_rate": 0.51,
        "wins": 54,
        "losses": 52,
        "tier": "A",
        "source": "subglow",
        "rank_30d": 4,
        "rank_7d": 8,
    },
    {
        "wallet": "6S8GezkxYUfZy9JPtYnanbcZTMB87Wjt1qx3c6ELajKC",
        "name": "Nyhrox",
        "pnl_30d_sol": 261.33,
        "pnl_7d_sol": 261.33,
        "pnl_30d_usd": 21092,
        "win_rate": 0.48,
        "wins": 42,
        "losses": 46,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 20,
        "rank_7d": 9,
    },
    {
        "wallet": "8MaVa9kdt3NW4Q5HyNAm1X5LbR8PQRVDc1W8NMVK88D5",
        "name": "Daumen",
        "pnl_30d_sol": 563.64,
        "pnl_7d_sol": 248.39,
        "pnl_30d_usd": 47262,
        "win_rate": 0.47,
        "wins": 40,
        "losses": 45,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 9,
        "rank_7d": 10,
    },
    {
        "wallet": "7bsTkeWcSPG6nzsbXucxV89YUULoSExNJdX2WqfLHwZ4",
        "name": "BIGWARZ",
        "pnl_30d_sol": 186.14,
        "pnl_7d_sol": 186.14,
        "pnl_30d_usd": 15023,
        "win_rate": 0.46,
        "wins": 38,
        "losses": 44,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 25,
        "rank_7d": 11,
    },
    {
        "wallet": "9iaawVBEsFG35PSwd4PahwT8fYNQe9XYuRdWm872dUqY",
        "name": "Meechie",
        "pnl_30d_sol": 159.01,
        "pnl_7d_sol": 159.01,
        "pnl_30d_usd": 12833,
        "win_rate": 0.46,
        "wins": 38,
        "losses": 44,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 28,
        "rank_7d": 12,
    },
    {
        "wallet": "831yhv67QpKqLBJjbmw2xoDUeeFHGUx8RnuRj9imeoEs",
        "name": "Trey",
        "pnl_30d_sol": 144.36,
        "pnl_7d_sol": 144.36,
        "pnl_30d_usd": 11651,
        "win_rate": 0.45,
        "wins": 36,
        "losses": 44,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 30,
        "rank_7d": 13,
    },
    {
        "wallet": "JDd3hy3gQn2V982mi1zqhNqUw1GfV2UL6g76STojCJPN",
        "name": "West",
        "pnl_30d_sol": 138.08,
        "pnl_7d_sol": 138.08,
        "pnl_30d_usd": 11144,
        "win_rate": 0.45,
        "wins": 36,
        "losses": 44,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 32,
        "rank_7d": 14,
    },
    {
        "wallet": "99xnE2zEFi8YhmKDaikc1EvH6ELTQJppnqUwMzmpLXrs",
        "name": "Coler",
        "pnl_30d_sol": 137.97,
        "pnl_7d_sol": 137.97,
        "pnl_30d_usd": 11135,
        "win_rate": 0.45,
        "wins": 36,
        "losses": 44,
        "tier": "B",
        "source": "subglow",
        "rank_30d": 33,
        "rank_7d": 15,
    },
]


def get_mirrors(min_pnl: float = 50.0, min_win_rate: float = 0.45) -> list:
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
