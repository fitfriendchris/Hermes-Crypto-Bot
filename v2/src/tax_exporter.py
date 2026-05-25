"""
tax_exporter.py — Hermes Solana Bot v2
Taxable event recording and monthly CSV export.
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXPORT_DIR = Path(os.getenv("TAX_EXPORT_DIR", "state/tax"))


@dataclass
class TaxableEvent:
    timestamp: str
    tx_hash: str
    event_type: str  # "swap" | "fee" | "reward"
    input_token: str
    input_token_symbol: str
    input_amount: float
    input_usd_value: float
    output_token: str
    output_token_symbol: str
    output_amount: float
    output_usd_value: float
    fee_usd: float
    gain_loss_usd: float
    cost_basis_usd: float
    wallet_address: str
    strategy: str = ""
    source_wallet: str = ""
    notes: str = ""


class TaxExporter:
    """Records every taxable event and exports monthly CSV for CPA import.

    Every swap is a taxable disposition of the input token.
    Cost basis = what you paid for the input token.
    Gain/Loss = output USD value - cost basis.
    """

    def __init__(self, export_dir: str | Path = EXPORT_DIR) -> None:
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self._events: list[TaxableEvent] = []
        self._cost_basis: dict[str, float] = {}  # token_mint -> avg cost per unit

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record_swap(
        self,
        tx_hash: str,
        input_token: str,
        input_symbol: str,
        input_amount: float,
        input_usd: float,
        output_token: str,
        output_symbol: str,
        output_amount: float,
        output_usd: float,
        fee_usd: float,
        wallet_address: str,
        strategy: str = "",
        source_wallet: str = "",
    ) -> TaxableEvent:
        """Record a swap as a taxable event.

        Gain/Loss = output_usd - cost_basis_of_input
        """
        # Calculate gain/loss
        cost_basis = self._get_cost_basis(input_token, input_amount)
        gain_loss = output_usd - cost_basis

        event = TaxableEvent(
            timestamp=_now(),
            tx_hash=tx_hash,
            event_type="swap",
            input_token=input_token,
            input_token_symbol=input_symbol,
            input_amount=input_amount,
            input_usd_value=input_usd,
            output_token=output_token,
            output_token_symbol=output_symbol,
            output_amount=output_amount,
            output_usd_value=output_usd,
            fee_usd=fee_usd,
            gain_loss_usd=gain_loss,
            cost_basis_usd=cost_basis,
            wallet_address=wallet_address,
            strategy=strategy,
            source_wallet=source_wallet,
        )
        self._events.append(event)
        self._append_to_file(event)
        self._update_cost_basis(output_token, output_amount, output_usd)
        logger.info("Tax event recorded: %s gain_loss=$%.2f", tx_hash[:16], gain_loss)
        return event

    def record_fee(
        self,
        tx_hash: str,
        fee_usd: float,
        wallet_address: str,
        notes: str = "",
    ) -> TaxableEvent:
        """Record a standalone fee (gas, Jito tip, etc)."""
        event = TaxableEvent(
            timestamp=_now(),
            tx_hash=tx_hash,
            event_type="fee",
            input_token="SOL",
            input_token_symbol="SOL",
            input_amount=0.0,
            input_usd_value=0.0,
            output_token="",
            output_token_symbol="",
            output_amount=0.0,
            output_usd_value=0.0,
            fee_usd=fee_usd,
            gain_loss_usd=-fee_usd,
            cost_basis_usd=0.0,
            wallet_address=wallet_address,
            notes=notes,
        )
        self._events.append(event)
        self._append_to_file(event)
        return event

    # ------------------------------------------------------------------ #
    # Cost basis tracking (FIFO simplified)
    # ------------------------------------------------------------------ #
    def _get_cost_basis(self, token_mint: str, amount: float) -> float:
        """Get cost basis for a given amount of a token."""
        basis_per_unit = self._cost_basis.get(token_mint, 0.0)
        return basis_per_unit * amount

    def _update_cost_basis(self, token_mint: str, amount: float, usd_value: float) -> None:
        """Update running average cost basis for a token."""
        if amount <= 0:
            return
        current_basis = self._cost_basis.get(token_mint, 0.0)
        # Running weighted average
        self._cost_basis[token_mint] = (
            (current_basis + usd_value / amount) / 2
            if current_basis > 0
            else usd_value / amount
        )

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def _append_to_file(self, event: TaxableEvent) -> None:
        """Append event to monthly JSONL file."""
        now = datetime.now(timezone.utc)
        filename = f"tax_events_{now.year}-{now.month:02d}.jsonl"
        filepath = self.export_dir / filename
        with open(filepath, "a") as f:
            f.write(json.dumps(asdict(event), default=str) + "\n")

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def export_csv(self, year: int | None = None, month: int | None = None) -> Path:
        """Export monthly events to CSV for CPA import.

        Returns path to exported file.
        """
        now = datetime.now(timezone.utc)
        year = year or now.year
        month = month or now.month

        # Read all events for the month
        filename = f"tax_events_{year}-{month:02d}.jsonl"
        filepath = self.export_dir / filename
        events: list[TaxableEvent] = []
        if filepath.exists():
            for line in filepath.read_text().splitlines():
                if line.strip():
                    events.append(TaxableEvent(**json.loads(line)))

        # Export to CSV
        csv_filename = f"tax_report_{year}-{month:02d}.csv"
        csv_path = self.export_dir / csv_filename
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "timestamp", "tx_hash", "event_type",
                "input_token", "input_token_symbol", "input_amount", "input_usd_value",
                "output_token", "output_token_symbol", "output_amount", "output_usd_value",
                "fee_usd", "gain_loss_usd", "cost_basis_usd",
                "wallet_address", "strategy", "source_wallet", "notes",
            ])
            writer.writeheader()
            for e in events:
                writer.writerow(asdict(e))

        logger.info("Tax CSV exported: %s (%d events)", csv_path, len(events))
        return csv_path

    def summary(self, year: int | None = None, month: int | None = None) -> dict[str, Any]:
        """Return summary stats for a month."""
        now = datetime.now(timezone.utc)
        year = year or now.year
        month = month or now.month
        filename = f"tax_events_{year}-{month:02d}.jsonl"
        filepath = self.export_dir / filename

        total_gain = 0.0
        total_loss = 0.0
        total_fees = 0.0
        swap_count = 0
        if filepath.exists():
            for line in filepath.read_text().splitlines():
                if not line.strip():
                    continue
                e = json.loads(line)
                gl = float(e.get("gain_loss_usd", 0))
                if gl > 0:
                    total_gain += gl
                else:
                    total_loss += abs(gl)
                total_fees += float(e.get("fee_usd", 0))
                if e.get("event_type") == "swap":
                    swap_count += 1

        net = total_gain - total_loss
        return {
            "year": year,
            "month": month,
            "swaps": swap_count,
            "total_gain": round(total_gain, 2),
            "total_loss": round(total_loss, 2),
            "total_fees": round(total_fees, 2),
            "net_pnl": round(net, 2),
            "taxable_events": swap_count + (1 if total_fees > 0 else 0),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    tx = TaxExporter()
    tx.record_swap(
        tx_hash="abc123",
        input_token="SOL",
        input_symbol="SOL",
        input_amount=1.0,
        input_usd=84.5,
        output_token="USDC",
        output_symbol="USDC",
        output_amount=84.0,
        output_usd=84.0,
        fee_usd=0.05,
        wallet_address="So11111111111111111111111111111111111111112",
    )
    print(tx.summary())
    csv_path = tx.export_csv()
    print(f"CSV: {csv_path}")
