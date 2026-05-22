"""
TELEGRAM ALERT MODULE
Direct Bot API via httpx — no python-telegram-bot dependency.
Author: Hermes | May 2026
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger('CryptoBot')


class TelegramAlertManager:
    """Sends alerts via direct Telegram Bot API (httpx)."""

    def __init__(self, token: str, chat_id: str):
        self.token   = token
        self.chat_id = str(chat_id)
        self._base   = f"https://api.telegram.org/bot{token}"
        self._ok     = bool(token and chat_id)
        if self._ok:
            logger.info("Telegram alerts ready (httpx)")
        else:
            logger.warning("Telegram alerts disabled — missing TOKEN or CHAT_ID")

    async def _send(self, text: str):
        if not self._ok:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self._base}/sendMessage",
                    json={
                        "chat_id":                   self.chat_id,
                        "text":                      text,
                        "parse_mode":                "HTML",
                        "disable_web_page_preview":  True,
                    },
                )
        except Exception as e:
            logger.debug(f"Telegram send error: {e}")

    # ── Bot lifecycle ──────────────────────────────────────────

    async def send_startup(self):
        live = os.getenv("LIVE_MODE", "false").lower() == "true"
        mode = "🔴 <b>LIVE</b>" if live else "📄 PAPER"
        await self._send(
            f"🚀 <b>Hermes Crypto Bot Started</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Mode: {mode}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )

    async def send_info(self, text: str):
        """Generic info message — text passed as-is."""
        await self._send(text)

    # ── Trade events ───────────────────────────────────────────

    async def send_position_opened(self, position: Dict):
        sym    = position.get('token', '?')
        entry  = position.get('entry', 0)
        size   = position.get('invested', 0)
        stop   = position.get('stop', 0)
        risk   = position.get('risk_pct', 0) * 100
        score  = position.get('score', 0)
        chain  = position.get('chain', 'solana')
        source = position.get('source', 'scanner')
        mode   = position.get('mode_at_entry', '?')
        live   = os.getenv("LIVE_MODE", "false").lower() == "true"
        tag    = "🔴 LIVE" if (live and chain == 'solana') else "📄 PAPER"

        # Account snapshot fields embedded into the entry message itself, so
        # the user sees balance impact + portfolio totals without waiting for
        # the follow-up portfolio snapshot.
        cash       = position.get('_cash_after', None)
        total      = position.get('_total_value', None)
        daily      = position.get('_daily_pnl', None)
        positions  = position.get('_open_count', None)

        balance_block = ""
        if cash is not None:
            d_emoji = "🟢" if (daily or 0) >= 0 else "🔴"
            balance_block = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Cash: <b>${cash:.2f}</b>   "
                f"💎 Total: <b>${total:.2f}</b>\n"
                f"{d_emoji} Day: <b>${daily:+.2f}</b>   "
                f"📊 Open: <b>{positions}</b>"
            )

        await self._send(
            f"✅ <b>ENTRY [{tag}] · {mode}</b>\n"
            f"Token:  <code>{sym}</code>  ({chain})\n"
            f"Entry:  <b>${entry:.8f}</b>\n"
            f"Size:   <b>${size:.2f}</b>\n"
            f"Stop:   <b>${stop:.8f}</b>  (risk {risk:.1f}%)\n"
            f"Score:  <b>{score}</b> | via {source}"
            + (f"\n{balance_block}" if balance_block else "")
        )

    async def send_position_closed(self, result: Dict):
        sym     = result.get('token', '?')
        pnl_pct = result.get('pnl_pct', 0) * 100
        pnl_usd = result.get('pnl_usd', 0)
        reason  = result.get('reason', '?')
        portion = result.get('portion', 1.0)
        mode    = result.get('mode_at_entry', '?')
        emoji   = "🟢" if pnl_usd > 0 else "🔴"
        ptag    = f" [{portion:.0%}]" if portion < 0.99 else ""

        # Embed full account-state line: cash, total value (cash + unrealized),
        # cumulative return vs starting capital, today's PnL.
        cash       = result.get('_cash_after', None)
        total      = result.get('_total_value', None)
        total_ret  = result.get('_total_return_pct', None)
        daily      = result.get('_daily_pnl', None)
        positions  = result.get('_open_count', None)

        balance_block = ""
        if cash is not None:
            d_emoji = "🟢" if (daily or 0) >= 0 else "🔴"
            ret_str = f"  ({total_ret:+.1%})" if total_ret is not None else ""
            balance_block = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 Cash: <b>${cash:.2f}</b>   "
                f"💎 Total: <b>${total:.2f}</b>{ret_str}\n"
                f"{d_emoji} Day: <b>${daily:+.2f}</b>   "
                f"📊 Open: <b>{positions}</b>"
            )

        await self._send(
            f"{emoji} <b>EXIT{ptag} · {mode}</b>\n"
            f"Token:  <code>{sym}</code>\n"
            f"PnL:    <b>${pnl_usd:+.2f}</b>  ({pnl_pct:+.1f}%)\n"
            f"Reason: <code>{reason}</code>"
            + (f"\n{balance_block}" if balance_block else "")
        )

    async def send_trade_summary(self, snap: Dict):
        """Full portfolio snapshot after every trade."""
        bal        = snap.get('balance', 0)
        total      = snap.get('total_value', bal)
        start      = snap.get('starting_balance', 100.0)
        unrealized = snap.get('unrealized_pnl_usd', 0)
        daily      = snap.get('daily_pnl', 0)
        dd         = snap.get('max_drawdown', 0)
        consec     = snap.get('consecutive_losses', 0)
        positions  = snap.get('positions', {})
        history    = snap.get('history', [])

        total_ret  = (total - start) / start if start > 0 else 0
        net_real   = sum(t.get('pnl', 0) for t in history)

        pos_lines = []
        for sym, pos in positions.items():
            entry = pos.get('entry', 0)
            last  = pos.get('last_price', entry)
            inv   = pos.get('invested', 0)
            pct   = (last - entry) / entry if entry > 0 else 0
            usd   = inv * pct
            pos_lines.append(f"  • {sym}: ${usd:+.2f} ({pct:+.1%})")

        pos_text = "\n".join(pos_lines) if pos_lines else "  (none)"
        d_emoji  = "🟢" if daily >= 0 else "🔴"
        u_emoji  = "🟢" if unrealized >= 0 else "🔴"

        await self._send(
            f"📊 <b>PORTFOLIO</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Cash: <b>${bal:.2f}</b>\n"
            f"💎 Value: <b>${total:.2f}</b>  ({total_ret:+.1%})\n"
            f"{d_emoji} Daily: <b>${daily:+.2f}</b>\n"
            f"{u_emoji} Unrealized: <b>${unrealized:+.2f}</b>\n"
            f"📊 Realized: <b>${net_real:+.2f}</b>\n"
            f"📉 DD: <b>{dd:.1%}</b> | Losses: <b>{consec}/5</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Open:</b>\n{pos_text}"
        )

    # ── Daily / alerts ─────────────────────────────────────────

    async def send_daily_report(
        self,
        balance: float,
        daily_pnl: float,
        trades: int,
        open_positions: int,
        win_rate: float,
    ):
        emoji = "🟢" if daily_pnl >= 0 else "🔴"
        await self._send(
            f"📊 <b>DAILY — {datetime.now().strftime('%Y-%m-%d')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Balance: <b>${balance:.2f}</b>\n"
            f"{emoji} PnL: <b>${daily_pnl:+.2f}</b>\n"
            f"Trades: <b>{trades}</b> | Open: <b>{open_positions}</b>\n"
            f"Win Rate: <b>{win_rate:.0%}</b>"
        )

    async def send_rug_pull_alert(self, token: str, signals: List[str]):
        sig_text = "\n".join(f"  • {s}" for s in signals)
        await self._send(
            f"🚨 <b>RUG PULL ALERT</b>\n"
            f"Token: <code>{token}</code>\n"
            f"Signals:\n{sig_text}\n"
            f"Action: <b>EMERGENCY EXIT</b>"
        )

    async def send_whale_signal(self, trade: Dict):
        token  = trade.get('token', '?')
        amount = trade.get('value_usd', 0)
        wallet = trade.get('wallet', 'Unknown')[:20]
        await self._send(
            f"🐋 <b>WHALE SIGNAL</b>\n"
            f"Token:  <code>{token}</code>\n"
            f"Amount: <b>${amount:,.0f}</b>\n"
            f"Wallet: <code>{wallet}...</code>\n"
            f"Time:   {datetime.now().strftime('%H:%M:%S')}"
        )

    async def send_sweep(self, usd: float, amount: float, token: str, tx_sig: str, cold: str):
        await self._send(
            f"💸 <b>PROFIT SWEEP</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Swept: <b>${usd:.2f}</b> → <b>{amount:.4f} {token}</b>\n"
            f"Cold:  <code>{cold[:20]}...</code>\n"
            f"Tx:    <code>{tx_sig[:20]}...</code>"
        )


async def _test():
    t = TelegramAlertManager("", "")
    await t.send_startup()
    print("TelegramAlertManager OK (no token configured)")

if __name__ == "__main__":
    asyncio.run(_test())
