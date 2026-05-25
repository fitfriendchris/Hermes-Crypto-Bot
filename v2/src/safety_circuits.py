"""
safety_circuits.py — Hermes Solana Bot v2
Production circuit breaker system with persistent state.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATE_PATH = Path(os.getenv("SAFETY_STATE_PATH", "state/safety_state.json"))


@dataclass
class CircuitState:
    daily_loss_usd: float = 0.0
    weekly_loss_usd: float = 0.0
    consecutive_losses: int = 0
    peak_balance_usd: float = 0.0
    current_balance_usd: float = 0.0
    rug_count_7d: int = 0
    rpc_failure_count: int = 0
    rpc_total_calls: int = 0
    halt_entries_until: str | None = None
    halt_reason: str = ""
    last_trade_time: str = ""
    paper_mode_completed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SafetyCircuits:
    """Circuit breaker system for operational risk limits.

    Enforces:
    - Daily loss limit
    - Weekly loss limit
    - Max drawdown
    - Consecutive loss halt
    - Rug-pull halt
    - RPC failure rate halt
    - Paper-mode gate
    """

    def __init__(
        self,
        daily_loss_limit_usd: float = 10.0,
        weekly_loss_limit_usd: float = 20.0,
        max_drawdown_pct: float = 20.0,
        consecutive_loss_halt: int = 3,
        rug_halt_threshold: int = 2,
        rpc_failure_rate_threshold: float = 0.10,
        paper_mode_gate_hours: float = 48.0,
        state_path: str | Path = STATE_PATH,
    ) -> None:
        self.daily_limit = daily_loss_limit_usd
        self.weekly_limit = weekly_loss_limit_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.consecutive_halt = consecutive_loss_halt
        self.rug_threshold = rug_halt_threshold
        self.rpc_failure_threshold = rpc_failure_rate_threshold
        self.paper_gate_hours = paper_mode_gate_hours
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state: CircuitState | None = None

    # ------------------------------------------------------------------ #
    # State management
    # ------------------------------------------------------------------ #
    def load(self) -> CircuitState:
        if not self.state_path.exists():
            now = _now()
            self._state = CircuitState(created_at=now, updated_at=now)
            self.save()
            return self._state
        try:
            raw = json.loads(self.state_path.read_text())
            self._state = CircuitState(**raw)
        except Exception as exc:
            logger.error("Corrupt safety state: %s — creating fresh", exc)
            now = _now()
            self._state = CircuitState(created_at=now, updated_at=now)
            self.save()
        return self._state

    def save(self) -> None:
        if self._state is None:
            return
        self._state.updated_at = _now()
        self.state_path.write_text(json.dumps(asdict(self._state), indent=2))

    @property
    def state(self) -> CircuitState:
        if self._state is None:
            self.load()
        assert self._state is not None
        return self._state

    # ------------------------------------------------------------------ #
    # Checks (call before every trade)
    # ------------------------------------------------------------------ #
    def can_trade(self, balance_usd: float) -> tuple[bool, str]:
        """Check all circuits. Returns (allowed, reason)."""
        s = self.state
        s.current_balance_usd = balance_usd
        if s.peak_balance_usd == 0:
            s.peak_balance_usd = balance_usd

        # 1. Halt period active?
        if s.halt_entries_until:
            until = datetime.fromisoformat(s.halt_entries_until)
            if datetime.now(timezone.utc) < until:
                return False, f"HALT: {s.halt_reason} (until {s.halt_entries_until})"
            s.halt_entries_until = None
            s.halt_reason = ""

        # 2. Paper-mode gate
        if s.paper_mode_completed_at is None:
            return False, "HALT: Paper-mode gate not complete (48h required)"

        # 3. Daily loss limit
        if s.daily_loss_usd >= self.daily_limit:
            return False, f"HALT: Daily loss ${s.daily_loss_usd:.2f} ≥ limit ${self.daily_limit}"

        # 4. Weekly loss limit
        if s.weekly_loss_usd >= self.weekly_limit:
            return False, f"HALT: Weekly loss ${s.weekly_loss_usd:.2f} ≥ limit ${self.weekly_limit}"

        # 5. Max drawdown
        if s.peak_balance_usd > 0:
            dd_pct = (s.peak_balance_usd - balance_usd) / s.peak_balance_usd * 100
            if dd_pct >= self.max_drawdown_pct:
                self._trigger_halt(
                    f"Max drawdown {dd_pct:.1f}% ≥ {self.max_drawdown_pct}%",
                    hours=24 * 7,
                )
                return False, s.halt_reason

        # 6. Consecutive losses
        if s.consecutive_losses >= self.consecutive_halt:
            self._trigger_halt(
                f"{s.consecutive_losses} consecutive losses",
                hours=4,
            )
            return False, s.halt_reason

        # 7. Rug-pull halt
        if s.rug_count_7d >= self.rug_threshold:
            return False, f"HALT: {s.rug_count_7d} rugs this week ≥ threshold {self.rug_threshold}"

        # 8. RPC failure rate
        if s.rpc_total_calls > 10:
            failure_rate = s.rpc_failure_count / s.rpc_total_calls
            if failure_rate >= self.rpc_failure_threshold:
                return False, f"HALT: RPC failure rate {failure_rate:.1%} ≥ {self.rpc_failure_threshold:.1%}"

        return True, ""

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #
    def on_trade_pnl(self, pnl_usd: float, is_rug: bool = False) -> None:
        """Call after every trade closes."""
        s = self.state
        if pnl_usd < 0:
            s.consecutive_losses += 1
            s.daily_loss_usd += abs(pnl_usd)
            s.weekly_loss_usd += abs(pnl_usd)
            if is_rug:
                s.rug_count_7d += 1
        else:
            s.consecutive_losses = 0
        s.last_trade_time = _now()
        self.save()

    def on_rpc_call(self, success: bool) -> None:
        s = self.state
        s.rpc_total_calls += 1
        if not success:
            s.rpc_failure_count += 1
        self.save()

    def on_paper_mode_complete(self) -> None:
        s = self.state
        s.paper_mode_completed_at = _now()
        self.save()
        logger.info("✅ Paper-mode gate COMPLETE")

    # ------------------------------------------------------------------ #
    # Halt / Resume
    # ------------------------------------------------------------------ #
    def _trigger_halt(self, reason: str, hours: float) -> None:
        s = self.state
        until = datetime.now(timezone.utc) + timedelta(hours=hours)
        s.halt_entries_until = until.isoformat()
        s.halt_reason = reason
        self.save()
        logger.error("🛑 CIRCUIT TRIGGERED: %s (halt until %s)", reason, s.halt_entries_until)

    def manual_resume(self) -> None:
        """Operator override to clear halt."""
        s = self.state
        s.halt_entries_until = None
        s.halt_reason = ""
        s.consecutive_losses = 0
        self.save()
        logger.warning("🔄 Manual circuit resume")

    # ------------------------------------------------------------------ #
    # Daily / weekly resets
    # ------------------------------------------------------------------ #
    def reset_daily(self) -> None:
        s = self.state
        s.daily_loss_usd = 0.0
        s.consecutive_losses = 0
        self.save()

    def reset_weekly(self) -> None:
        s = self.state
        s.weekly_loss_usd = 0.0
        s.rug_count_7d = 0
        self.save()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def status(self) -> str:
        s = self.state
        lines = [
            "🛡️ Safety Circuits",
            f"  Daily loss: ${s.daily_loss_usd:.2f} / ${self.daily_limit}",
            f"  Weekly loss: ${s.weekly_loss_usd:.2f} / ${self.weekly_limit}",
            f"  Consecutive losses: {s.consecutive_losses} / {self.consecutive_halt}",
            f"  Rugs (7d): {s.rug_count_7d} / {self.rug_threshold}",
            f"  RPC failures: {s.rpc_failure_count}/{s.rpc_total_calls}",
            f"  Drawdown: {(s.peak_balance_usd - s.current_balance_usd) / s.peak_balance_usd * 100:.1f}% / {self.max_drawdown_pct}%",
            f"  Halt: {'YES — ' + s.halt_reason if s.halt_entries_until else 'NO'}",
            f"  Paper gate: {'✅' if s.paper_mode_completed_at else '⏳'}",
        ]
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    sc = SafetyCircuits()
    allowed, reason = sc.can_trade(90.78)
    print(f"can_trade={allowed} reason={reason}")
    print(sc.status())
