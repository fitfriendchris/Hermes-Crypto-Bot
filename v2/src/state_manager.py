"""
state_manager.py — Hermes Solana Bot v2
Atomic, versioned state with backup rotation.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

STATE_SCHEMA_VERSION = 2
MAX_BACKUPS = 10


@dataclass
class Position:
    token_mint: str
    token_symbol: str
    entry_price_usd: float
    quantity: float
    invested_usd: float
    highest_price_usd: float
    opened_at: str
    strategy: str = ""
    source_wallet: str = ""  # For copy-trades: which wallet we copied


@dataclass
class TradeRecord:
    token_mint: str
    token_symbol: str
    side: str  # "buy" | "sell"
    price_usd: float
    quantity: float
    usd_value: float
    tx_hash: str = ""
    fee_usd: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    timestamp: str = ""
    strategy: str = ""
    source_wallet: str = ""


@dataclass
class HermesState:
    schema_version: int = STATE_SCHEMA_VERSION
    balance_usd: float = 0.0
    peak_balance_usd: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[TradeRecord] = field(default_factory=list)
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    max_drawdown_pct: float = 0.0
    total_protected: float = 0.0
    halt_entries_until: str | None = None
    halt_reason: str = ""
    day_start_balance: float = 0.0
    week_start_balance: float = 0.0
    daily_loss_usd: float = 0.0
    weekly_loss_usd: float = 0.0
    rug_count_7d: int = 0
    rpc_failure_count: int = 0
    rpc_total_calls: int = 0
    mode: str = "paper"
    timestamp: str = ""
    # Copy-trade tracking
    copied_wallets: dict[str, dict] = field(default_factory=dict)
    wallet_pnl: dict[str, float] = field(default_factory=dict)
    wallet_consecutive_losses: dict[str, int] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateManager:
    """Atomic state persistence with backup rotation."""

    def __init__(self, state_path: str | Path = "state/hermes_state.json") -> None:
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir = self.state_path.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._state: HermesState | None = None

    # ------------------------------------------------------------------ #
    # Load / Save
    # ------------------------------------------------------------------ #
    def load(self) -> HermesState:
        """Load state from disk. Validate schema. Create default if missing."""
        if not self.state_path.exists():
            logger.warning("State file missing — creating default")
            self._state = HermesState(timestamp=_now())
            self.save()
            return self._state

        try:
            raw = json.loads(self.state_path.read_text())
        except json.JSONDecodeError as exc:
            logger.error("Corrupt state file: %s — attempting backup recovery", exc)
            recovered = self._recover_from_backup()
            if recovered:
                self._state = recovered
                self.save()
                return self._state
            raise RuntimeError("State corrupt and no valid backup found") from exc

        # Schema migration
        schema = raw.get("schema_version", 1)
        if schema != STATE_SCHEMA_VERSION:
            raw = self._migrate(raw, schema)

        self._state = self._deserialize(raw)
        return self._state

    def save(self) -> None:
        """Atomic write: temp file → rename. Backup rotation."""
        if self._state is None:
            raise RuntimeError("No state loaded — call load() first")

        self._state.timestamp = _now()
        data = self._serialize(self._state)

        # Rotate backups before write
        self._rotate_backups()

        # Atomic write
        temp_path = self.state_path.with_suffix(".tmp")
        try:
            temp_path.write_text(json.dumps(data, indent=2, default=str))
            temp_path.replace(self.state_path)
            logger.debug("State saved atomically to %s", self.state_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    # ------------------------------------------------------------------ #
    # Backup / Recovery
    # ------------------------------------------------------------------ #
    def _rotate_backups(self) -> None:
        """Keep last MAX_BACKUPS as timestamped copies."""
        if not self.state_path.exists():
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"hermes_state_{ts}.json"
        shutil.copy2(self.state_path, backup_path)

        # Prune old backups
        backups = sorted(self.backup_dir.glob("hermes_state_*.json"))
        for old in backups[:-MAX_BACKUPS]:
            old.unlink()
            logger.debug("Pruned old backup: %s", old.name)

    def _recover_from_backup(self) -> HermesState | None:
        """Try to load most recent valid backup."""
        backups = sorted(self.backup_dir.glob("hermes_state_*.json"), reverse=True)
        for backup in backups:
            try:
                raw = json.loads(backup.read_text())
                state = self._deserialize(raw)
                logger.warning("Recovered state from backup: %s", backup.name)
                return state
            except Exception:
                continue
        return None

    # ------------------------------------------------------------------ #
    # Schema Migration
    # ------------------------------------------------------------------ #
    def _migrate(self, raw: dict, old_version: int) -> dict:
        """Migrate older state schemas to current version."""
        logger.info("Migrating state from schema v%s → v%s", old_version, STATE_SCHEMA_VERSION)
        if old_version < 2:
            # v1 → v2: add copy-trade fields
            raw.setdefault("copied_wallets", {})
            raw.setdefault("wallet_pnl", {})
            raw.setdefault("wallet_consecutive_losses", {})
            raw.setdefault("rug_count_7d", 0)
            raw.setdefault("rpc_failure_count", 0)
            raw.setdefault("rpc_total_calls", 0)
        raw["schema_version"] = STATE_SCHEMA_VERSION
        return raw

    # ------------------------------------------------------------------ #
    # Serialization
    # ------------------------------------------------------------------ #
    @staticmethod
    def _serialize(state: HermesState) -> dict:
        return asdict(state)

    @staticmethod
    def _deserialize(raw: dict) -> HermesState:
        # Rebuild dataclass from dict
        raw["positions"] = {
            k: Position(**v) for k, v in raw.get("positions", {}).items()
        }
        raw["history"] = [TradeRecord(**r) for r in raw.get("history", [])]
        return HermesState(**raw)

    # ------------------------------------------------------------------ #
    # Convenience helpers
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> HermesState:
        if self._state is None:
            self.load()
        assert self._state is not None
        return self._state

    def record_buy(
        self,
        token_mint: str,
        token_symbol: str,
        price_usd: float,
        quantity: float,
        invested_usd: float,
        tx_hash: str = "",
        fee_usd: float = 0.0,
        strategy: str = "",
        source_wallet: str = "",
    ) -> None:
        """Record a buy and open a position."""
        s = self.state
        s.positions[token_mint] = Position(
            token_mint=token_mint,
            token_symbol=token_symbol,
            entry_price_usd=price_usd,
            quantity=quantity,
            invested_usd=invested_usd,
            highest_price_usd=price_usd,
            opened_at=_now(),
            strategy=strategy,
            source_wallet=source_wallet,
        )
        s.history.append(
            TradeRecord(
                token_mint=token_mint,
                token_symbol=token_symbol,
                side="buy",
                price_usd=price_usd,
                quantity=quantity,
                usd_value=invested_usd,
                tx_hash=tx_hash,
                fee_usd=fee_usd,
                timestamp=_now(),
                strategy=strategy,
                source_wallet=source_wallet,
            )
        )
        s.trades_today += 1
        s.balance_usd -= invested_usd
        self.save()

    def record_sell(
        self,
        token_mint: str,
        price_usd: float,
        quantity: float,
        proceeds_usd: float,
        tx_hash: str = "",
        fee_usd: float = 0.0,
        reason: str = "",
        strategy: str = "",
        source_wallet: str = "",
    ) -> float:
        """Record a sell, close position, return PnL."""
        s = self.state
        pos = s.positions.pop(token_mint, None)
        if pos is None:
            logger.error("Sell attempted for unknown position: %s", token_mint)
            return 0.0

        pnl = proceeds_usd - pos.invested_usd
        pnl_pct = pnl / pos.invested_usd if pos.invested_usd else 0.0
        s.history.append(
            TradeRecord(
                token_mint=token_mint,
                token_symbol=pos.token_symbol,
                side="sell",
                price_usd=price_usd,
                quantity=quantity,
                usd_value=proceeds_usd,
                tx_hash=tx_hash,
                fee_usd=fee_usd,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                reason=reason,
                timestamp=_now(),
                strategy=strategy or pos.strategy,
                source_wallet=source_wallet or pos.source_wallet,
            )
        )
        s.balance_usd += proceeds_usd
        s.daily_pnl += pnl
        s.weekly_pnl += pnl
        if pnl < 0:
            s.consecutive_losses += 1
            s.daily_loss_usd += abs(pnl)
            s.weekly_loss_usd += abs(pnl)
        else:
            s.consecutive_losses = 0
        if s.balance_usd > s.peak_balance_usd:
            s.peak_balance_usd = s.balance_usd
        dd = (s.peak_balance_usd - s.balance_usd) / s.peak_balance_usd if s.peak_balance_usd else 0.0
        s.max_drawdown_pct = max(s.max_drawdown_pct, dd)
        if pos.source_wallet:
            s.wallet_pnl[pos.source_wallet] = s.wallet_pnl.get(pos.source_wallet, 0.0) + pnl
            if pnl < 0:
                s.wallet_consecutive_losses[pos.source_wallet] = s.wallet_consecutive_losses.get(pos.source_wallet, 0) + 1
            else:
                s.wallet_consecutive_losses[pos.source_wallet] = 0
        self.save()
        return pnl

    def reset_daily(self) -> None:
        """Call at day boundary."""
        s = self.state
        s.day_start_balance = s.balance_usd
        s.daily_pnl = 0.0
        s.daily_loss_usd = 0.0
        s.trades_today = 0
        self.save()

    def reset_weekly(self) -> None:
        """Call at week boundary."""
        s = self.state
        s.week_start_balance = s.balance_usd
        s.weekly_pnl = 0.0
        s.weekly_loss_usd = 0.0
        s.rug_count_7d = 0
        self.save()


if __name__ == "__main__":
    sm = StateManager("state/test_state.json")
    st = sm.load()
    print(f"schema={st.schema_version} balance=${st.balance_usd} positions={len(st.positions)}")
