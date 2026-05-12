"""
TELEGRAM ALERT MODULE
Real-time notifications for bot events
Author: Hermes | March 2026 SEC/CFTC compliant
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Optional

try:
    from telegram import Bot, Update
    from telegram.ext import Application, CommandHandler, ContextTypes
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logging.warning("python-telegram-bot not installed. Telegram alerts disabled.")

logger = logging.getLogger('CryptoBot')

class TelegramAlertManager:
    """Sends alerts via Telegram for bot events."""
    
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.bot = None
        self.application = None
        
        if TELEGRAM_AVAILABLE and token and chat_id:
            self.bot = Bot(token=token)
            logger.info("Telegram alerts initialized")
        else:
            logger.warning("Telegram alerts not available")
    
    async def send_startup(self):
        message = (
            f"🚀 <b>Crypto Bot Started</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Mode: <b>AGGRESSIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        await self._send(message)
    
    async def send_whale_signal(self, trade: Dict):
        token = trade.get('token', 'Unknown')
        amount = trade.get('value_usd', 0)
        whale = trade.get('wallet', 'Unknown')[:20]
        
        message = (
            f"🐋 <b>WHALE SIGNAL</b>\n"
            f"Token: <code>{token}</code>\n"
            f"Amount: <b>${amount:,.0f}</b>\n"
            f"Whale: <code>{whale}...</code>\n"
            f"Time: {datetime.now().strftime('%H:%M:%S')}"
        )
        await self._send(message)
    
    async def send_position_opened(self, position: Dict):
        token = position['token']
        entry = position['entry']
        size = position['size']
        stop = position['stop']
        risk = position['risk_pct'] * 100
        
        message = (
            f"✅ <b>POSITION OPENED</b>\n"
            f"Token: <code>{token}</code>\n"
            f"Entry: <b>${entry:.6f}</b>\n"
            f"Size: <b>${size:.2f}</b>\n"
            f"Stop: <b>${stop:.6f}</b>\n"
            f"Risk: <b>{risk:.1f}%</b>"
        )
        await self._send(message)
    
    async def send_position_closed(self, result: Dict):
        token = result['token']
        pnl_pct = result['pnl_pct'] * 100
        pnl_usd = result['pnl_usd']
        reason = result['reason']
        
        emoji = "🟢" if pnl_usd > 0 else "🔴"
        
        message = (
            f"{emoji} <b>POSITION CLOSED</b>\n"
            f"Token: <code>{token}</code>\n"
            f"PnL: <b>${pnl_usd:+.2f}</b> ({pnl_pct:+.1f}%)\n"
            f"Reason: <code>{reason}</code>"
        )
        await self._send(message)
    
    async def send_rug_pull_alert(self, token: str, signals: list):
        signal_str = '\n'.join([f"  • {s}" for s in signals])
        
        message = (
            f"🚨 <b>RUG PULL ALERT</b>\n"
            f"Token: <code>{token}</code>\n"
            f"Signals:\n{signal_str}\n"
            f"Action: <b>EMERGENCY EXIT</b>"
        )
        await self._send(message)
    
    async def send_daily_report(self, balance: float, daily_pnl: float, trades: int, open_positions: int, win_rate: float):
        emoji = "🟢" if daily_pnl >= 0 else "🔴"
        
        message = (
            f"📊 <b>DAILY REPORT</b>\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"Balance: <b>${balance:.2f}</b>\n"
            f"Daily PnL: {emoji} <b>${daily_pnl:+.2f}</b>\n"
            f"Trades: <b>{trades}</b>\n"
            f"Open: <b>{open_positions}</b>\n"
            f"Win Rate: <b>{win_rate:.0%}</b>"
        )
        await self._send(message)
    
    async def _send(self, message: str):
        if not self.bot:
            logger.debug(f"Telegram not available")
            return
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

async def test():
    print("=== TELEGRAM ALERT TEST ===")
    alerts = TelegramAlertManager("", "")
    await alerts.send_startup()
    await alerts.send_whale_signal({'token': 'TEST', 'value_usd': 5000, 'wallet': '0x1234'})
    print("Install python-telegram-bot for real alerts")

if __name__ == "__main__":
    asyncio.run(test())
