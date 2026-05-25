"""
config_loader.py — Hermes Solana Bot v2
Env-based secrets, mode-aware config loading.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RPCConfig:
    primary_url: str
    fallback_url: str
    public_url: str
    timeout_seconds: float = 30.0
    health_check_interval_seconds: float = 30.0
    max_retries: int = 3
    retry_backoff_base: float = 1.0


@dataclass(frozen=True)
class WalletConfig:
    private_key_base58: str
    public_key: str


@dataclass(frozen=True)
class SafetyConfig:
    daily_loss_limit_usd: float
    weekly_loss_limit_usd: float
    max_drawdown_pct: float
    consecutive_loss_halt: int
    rug_halt_threshold: int
    paper_mode_gate_hours: float = 48.0
    rpc_failure_rate_threshold: float = 0.10


@dataclass(frozen=True)
class PositionConfig:
    base_size_usd: float
    max_size_usd: float
    max_pct_of_capital: float
    default_slippage_bps: int = 300
    max_slippage_bps: int = 500
    min_liquidity_usd: float = 10_000.0
    min_volume_24h_usd: float = 5_000.0


@dataclass(frozen=True)
class RiskConfig:
    max_rug_score: int = 30
    min_holder_count: int = 50
    max_top_holder_pct: float = 30.0
    min_lp_lock_days: int = 30
    max_deployer_rug_count: int = 3


@dataclass(frozen=True)
class CopyTradeConfig:
    min_trades: int = 500
    min_win_rate: float = 0.55
    max_drawdown_pct: float = 2.0
    trust_ladder: list[str] = field(default_factory=lambda: ["paper", "0.5x", "1.0x"])
    max_wallets: int = 5
    mirror_timeout_seconds: int = 30
    kill_consecutive_losses: int = 3
    kill_drawdown_pct: float = 15.0
    kill_rug_count: int = 2


@dataclass(frozen=True)
class TaxConfig:
    export_dir: str = "state/tax"
    dedicated_wallet_public_key: str = ""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    chat_id: str


@dataclass(frozen=True)
class HermesConfig:
    mode: str  # "paper" | "live" | "test"
    capital_usd: float
    rpc: RPCConfig
    wallet: WalletConfig
    safety: SafetyConfig
    position: PositionConfig
    risk: RiskConfig
    copy_trade: CopyTradeConfig
    tax: TaxConfig
    telegram: TelegramConfig
    log_level: str = "INFO"
    log_file: str = "logs/hermes_v2.log"


def _load_env() -> None:
    """Load .env file if present."""
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _get(key: str, default: str = "") -> str:
    v = os.environ.get(key, default).strip()
    if not v:
        raise ValueError(f"Required env var {key} is missing")
    return v


def _get_float(key: str, default: float) -> float:
    return float(os.environ.get(key, str(default)))


def _get_int(key: str, default: int) -> int:
    return int(os.environ.get(key, str(default)))


def load_config(env_path: str | None = None) -> HermesConfig:
    """Load configuration from environment variables.

    Args:
        env_path: Optional path to .env file. If None, loads from cwd.

    Returns:
        HermesConfig dataclass with all settings validated.
    """
    if env_path:
        import dotenv
        dotenv.load_dotenv(env_path)
    else:
        _load_env()

    mode = os.environ.get("BOT_MODE", "paper").lower()
    if mode not in ("paper", "live", "test"):
        raise ValueError(f"BOT_MODE must be paper/live/test, got {mode}")

    if mode == "live":
        # Warn if using dummy keys
        pk = os.environ.get("WALLET_PRIVATE_KEY_BASE58", "")
        if not pk or "your" in pk.lower():
            raise ValueError(
                "LIVE mode requires a real WALLET_PRIVATE_KEY_BASE58 in .env"
            )

    return HermesConfig(
        mode=mode,
        capital_usd=_get_float("BOT_CAPITAL_USD", 90.78),
        rpc=RPCConfig(
            primary_url=_get("RPC_PRIMARY_URL"),
            fallback_url=_get("RPC_FALLBACK_URL"),
            public_url=_get("RPC_PUBLIC_URL"),
            timeout_seconds=_get_float("RPC_TIMEOUT_SECONDS", 30.0),
            health_check_interval_seconds=_get_float(
                "RPC_HEALTH_CHECK_INTERVAL_SECONDS", 30.0
            ),
            max_retries=_get_int("RPC_MAX_RETRIES", 3),
            retry_backoff_base=_get_float("RPC_RETRY_BACKOFF_BASE", 1.0),
        ),
        wallet=WalletConfig(
            private_key_base58=os.environ.get("WALLET_PRIVATE_KEY_BASE58", ""),
            public_key=os.environ.get("WALLET_PUBLIC_KEY", ""),
        ),
        safety=SafetyConfig(
            daily_loss_limit_usd=_get_float("DAILY_LOSS_LIMIT_USD", 10.0),
            weekly_loss_limit_usd=_get_float("WEEKLY_LOSS_LIMIT_USD", 20.0),
            max_drawdown_pct=_get_float("MAX_DRAWDOWN_PCT", 20.0),
            consecutive_loss_halt=_get_int("CONSECUTIVE_LOSS_HALT", 3),
            rug_halt_threshold=_get_int("RUG_HALT_THRESHOLD", 2),
            paper_mode_gate_hours=_get_float("PAPER_MODE_GATE_HOURS", 48.0),
            rpc_failure_rate_threshold=_get_float(
                "RPC_FAILURE_RATE_THRESHOLD", 0.10
            ),
        ),
        position=PositionConfig(
            base_size_usd=_get_float("BASE_POSITION_SIZE_USD", 2.5),
            max_size_usd=_get_float("MAX_POSITION_SIZE_USD", 5.0),
            max_pct_of_capital=_get_float("MAX_POSITION_PCT_OF_CAPITAL", 0.05),
            default_slippage_bps=_get_int("DEFAULT_SLIPPAGE_BPS", 300),
            max_slippage_bps=_get_int("MAX_SLIPPAGE_BPS", 500),
            min_liquidity_usd=_get_float("MIN_LIQUIDITY_USD", 10_000.0),
            min_volume_24h_usd=_get_float("MIN_VOLUME_24H_USD", 5_000.0),
        ),
        risk=RiskConfig(
            max_rug_score=_get_int("MAX_RUG_SCORE", 30),
            min_holder_count=_get_int("MIN_HOLDER_COUNT", 50),
            max_top_holder_pct=_get_float("MAX_TOP_HOLDER_PCT", 30.0),
            min_lp_lock_days=_get_int("MIN_LP_LOCK_DAYS", 30),
            max_deployer_rug_count=_get_int("MAX_DEPLOYER_RUG_COUNT", 3),
        ),
        copy_trade=CopyTradeConfig(
            min_trades=_get_int("COPY_WALLET_MIN_TRADES", 500),
            min_win_rate=_get_float("COPY_WALLET_MIN_WIN_RATE", 0.55),
            max_drawdown_pct=_get_float("COPY_WALLET_MAX_DRAWDOWN_PCT", 2.0),
            trust_ladder=os.environ.get("COPY_TRUST_LADDER", "paper,0.5x,1.0x").split(","),
            max_wallets=_get_int("COPY_MAX_WALLETS", 5),
            mirror_timeout_seconds=_get_int("COPY_MIRROR_TIMEOUT_SECONDS", 30),
            kill_consecutive_losses=_get_int("COPY_KILL_CONSECUTIVE_LOSSES", 3),
            kill_drawdown_pct=_get_float("COPY_KILL_DRAWDOWN_PCT", 15.0),
            kill_rug_count=_get_int("COPY_KILL_RUG_COUNT", 2),
        ),
        tax=TaxConfig(
            export_dir=os.environ.get("TAX_EXPORT_DIR", "state/tax"),
            dedicated_wallet_public_key=os.environ.get(
                "DEDICATED_WALLET_PUBLIC_KEY", ""
            ),
        ),
        telegram=TelegramConfig(
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", "5786598754"),
        ),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        log_file=os.environ.get("LOG_FILE", "logs/hermes_v2.log"),
    )


if __name__ == "__main__":
    cfg = load_config()
    print(f"mode={cfg.mode} capital=${cfg.capital_usd} slippage={cfg.position.default_slippage_bps}bps")
