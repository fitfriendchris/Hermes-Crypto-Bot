"""
Configuration — loads from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Config:
    # Spot Exchange
    spot_exchange: str = "binance"          # or "coinbase"
    primary_pair: str = "BTCUSDT"
    spot_ws_url: str = "wss://stream.binance.com:9443/ws/btcusdt@trade"

    # Polymarket
    polymarket_api: str = "https://clob.polymarket.com"
    polymarket_ws_url: str = "wss://ws-subscriber.polymarket.com"
    polymarket_poll_sec: float = 2.0

    # Candle Timeframes
    timeframes: tuple = ("5m", "15m", "1d")

    # Signal Thresholds
    min_edge_bps: int = 150                 # Minimum 1.5% edge to trigger
    max_slippage_bps: int = 50              # Abort if slippage > 0.5%
    confidence_threshold: float = 0.6

    # Risk
    max_position_usd: Decimal = Decimal("500")
    max_daily_loss_usd: Decimal = Decimal("100")
    circuit_breaker_drawdown: float = 0.05  # 5% daily drawdown kills trading
    risk_monitor_sec: float = 5.0

    # Execution
    signal_interval_sec: float = 0.5
    use_testnet: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        """Override defaults with environment variables."""
        def _get(name: str, default):
            val = os.getenv(name)
            return type(default)(val) if val is not None else default

        return cls(
            spot_exchange=_get("ARB_SPOT_EXCHANGE", cls.spot_exchange),
            primary_pair=_get("ARB_PRIMARY_PAIR", cls.primary_pair),
            spot_ws_url=_get("ARB_SPOT_WS_URL", cls.spot_ws_url),
            polymarket_api=_get("ARB_POLY_API", cls.polymarket_api),
            polymarket_poll_sec=_get("ARB_POLY_POLL_SEC", cls.polymarket_poll_sec),
            min_edge_bps=_get("ARB_MIN_EDGE_BPS", cls.min_edge_bps),
            max_slippage_bps=_get("ARB_MAX_SLIPPAGE_BPS", cls.max_slippage_bps),
            max_position_usd=Decimal(_get("ARB_MAX_POSITION_USD", str(cls.max_position_usd))),
            max_daily_loss_usd=Decimal(_get("ARB_MAX_DAILY_LOSS_USD", str(cls.max_daily_loss_usd))),
            use_testnet=_get("ARB_USE_TESTNET", cls.use_testnet),
        )
