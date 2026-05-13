#!/usr/bin/env python3
"""
HERMES CRYPTO TELEGRAM DASHBOARD v3
Interactive command center + health checks + clean restart + true-balance PnL
Author: Hermes | May 2026
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, '.env'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(_HERE, 'logs', 'HERMES_DASHBOARD.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('HermesDashboard')

# ── CONFIG ──
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
ADMIN_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
STATE_PATH = os.path.join(_HERE, 'state', 'HERMES_CRYPTO_STATE.json')
LAUNCHD_BOT = 'com.hermes.crypto-bot'
LAUNCHD_DASHBOARD = 'com.hermes.crypto-dashboard'
BOT_PY = os.path.join(_HERE, 'HERMES_CRYPTO_BOT.py')

if not TOKEN or not ADMIN_ID:
    logger.error("TOKEN or CHAT_ID missing")
    sys.exit(1)

BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

# ── STATE CACHE ──
_state_cache: Optional[Dict] = None
_state_mtime: float = 0


def load_bot_state() -> Optional[Dict]:
    """Load state with caching."""
    global _state_cache, _state_mtime
    try:
        st = os.stat(STATE_PATH)
        if st.st_mtime != _state_mtime:
            with open(STATE_PATH, 'r') as f:
                _state_cache = json.load(f)
            _state_mtime = st.st_mtime
        return _state_cache
    except Exception as e:
        logger.error(f"State load failed: {e}")
        return None


# ── HEALTH CHECKS ──
def check_bot_process() -> Tuple[bool, str]:
    """Check if bot Python process is running."""
    try:
        result = subprocess.run(['pgrep', '-f', 'HERMES_CRYPTO_BOT.py'],
                                  capture_output=True, text=True)
        if result.returncode == 0:
            pid = result.stdout.strip().split('\n')[0]
            return True, f"PID {pid}"
        return False, "Not running"
    except Exception as e:
        return False, str(e)


def check_dashboard_process() -> Tuple[bool, str]:
    """Check if dashboard is running."""
    try:
        result = subprocess.run(['pgrep', '-f', 'HERMES_TELEGRAM_DASHBOARD.py'],
                                  capture_output=True, text=True)
        if result.returncode == 0:
            pid = result.stdout.strip().split('\n')[0]
            return True, f"PID {pid}"
        return False, "Not running"
    except Exception as e:
        return False, str(e)


def check_launchd_status(label: str) -> str:
    """Check launchd status."""
    try:
        result = subprocess.run(['launchctl', 'list'],
                                capture_output=True, text=True)
        for line in result.stdout.split('\n'):
            if label in line:
                parts = line.split('\t')
                if len(parts) >= 2:
                    pid = parts[0]
                    return f"🟢 PID {pid}" if pid != '0' else "🟡 Loaded (not running)"
        return "🔴 Not loaded"
    except Exception as e:
        return f"⚪ Error: {e}"


def check_disk_space() -> Tuple[float, str]:
    """Check free disk space in GB."""
    try:
        stat = os.statvfs(_HERE)
        free_gb = stat.f_frsize * stat.f_bavail / (1024**3)
        if free_gb < 2:
            return free_gb, "🔴 LOW"
        return free_gb, "🟢 OK"
    except Exception as e:
        return 0, f"⚪ Error: {e}"


def check_log_health() -> Tuple[int, str]:
    """Check recent error count in logs."""
    try:
        log_path = os.path.join(_HERE, 'logs', 'HERMES_CRYPTO_BOT.log')
        if not os.path.exists(log_path):
            return 0, "🟢 No logs yet"
        with open(log_path, 'r') as f:
            lines = f.readlines()[-200:]
        errors = sum(1 for line in lines if any(lvl in line for lvl in ['ERROR', 'CRITICAL', 'FATAL']))
        if errors == 0:
            return 0, "🟢 Clean"
        elif errors < 5:
            return errors, f"🟡 {errors} errors (recent)"
        else:
            return errors, f"🔴 {errors} errors"
    except Exception as e:
        return 0, f"⚪ Error: {e}"


def full_health_check() -> Dict:
    """Run all health checks."""
    bot_run, bot_pid = check_bot_process()
    dash_run, dash_pid = check_dashboard_process()
    bot_ld = check_launchd_status(LAUNCHD_BOT)
    dash_ld = check_launchd_status(LAUNCHD_DASHBOARD)
    free_gb, disk_status = check_disk_space()
    err_count, log_status = check_log_health()

    overall = "🟢 HEALTHY"
    issues = []
    if not bot_run and bot_ld == "🔴 Not loaded":
        overall = "🔴 CRITICAL"
        issues.append("Bot not running and not loaded")
    elif not bot_run:
        overall = "🟡 WARNING"
        issues.append("Bot process down")
    if not dash_run:
        overall = "🟡 WARNING"
        issues.append("Dashboard down")
    if free_gb < 2:
        overall = "🔴 CRITICAL"
        issues.append("Disk space critical")
    if err_count >= 5:
        overall = "🟡 WARNING"
        issues.append("Recent errors detected")

    return {
        'overall': overall,
        'issues': issues,
        'bot_process': bot_run,
        'bot_pid': bot_pid,
        'dash_process': dash_run,
        'dash_pid': dash_pid,
        'bot_launchd': bot_ld,
        'dash_launchd': dash_ld,
        'free_gb': free_gb,
        'disk_status': disk_status,
        'error_count': err_count,
        'log_status': log_status,
    }


# ── SYSTEM CONTROL ──
def bot_start() -> str:
    """Start the crypto bot."""
    try:
        subprocess.run(['launchctl', 'start', LAUNCHD_BOT], check=True, capture_output=True)
        return "🟢 Bot started successfully"
    except subprocess.CalledProcessError as e:
        return f"❌ Start failed: {e.stderr.decode()[:200]}"


def bot_stop() -> str:
    """Stop the crypto bot."""
    try:
        subprocess.run(['launchctl', 'stop', LAUNCHD_BOT], check=True, capture_output=True)
        return "🔴 Bot stopped"
    except subprocess.CalledProcessError as e:
        return f"❌ Stop failed: {e.stderr.decode()[:200]}"


def bot_restart_clean() -> str:
    """Clean restart: stop, save state, reset, restart."""
    results = []

    # Step 1: Stop bot
    try:
        subprocess.run(['launchctl', 'stop', LAUNCHD_BOT], check=True, capture_output=True)
        results.append("1️⃣ Bot stopped")
    except Exception as e:
        results.append(f"1️⃣ Stop failed: {e}")

    # Step 2: Save state backup
    if os.path.exists(STATE_PATH):
        backup = os.path.join(_HERE, 'state', f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        import shutil
        try:
            shutil.copy2(STATE_PATH, backup)
            results.append(f"2️⃣ State backed up to {os.path.basename(backup)}")
        except Exception as e:
            results.append(f"2️⃣ Backup failed: {e}")
    else:
        results.append("2️⃣ No state to backup")

    # Step 3: Clear stale PIDs
    try:
        subprocess.run(['pkill', '-f', 'HERMES_CRYPTO_BOT.py'],
                       capture_output=True, timeout=5)
        results.append("3️⃣ Stale processes killed")
    except Exception:
        results.append("3️⃣ No stale processes")

    # Step 4: Wait for clean shutdown
    import time
    time.sleep(2)

    # Step 5: Start fresh
    try:
        subprocess.run(['launchctl', 'start', LAUNCHD_BOT], check=True, capture_output=True)
        results.append("4️⃣ Bot restarted")
    except Exception as e:
        results.append(f"4️⃣ Restart failed: {e}")

    # Step 6: Verify
    time.sleep(2)
    ok, pid = check_bot_process()
    if ok:
        results.append(f"5️⃣ ✅ Verified running (PID {pid})")
    else:
        results.append("5️⃣ ❌ Bot not detected after restart")

    return "\n".join(results)


def dashboard_restart() -> str:
    """Restart dashboard itself via launchd."""
    try:
        subprocess.run(['launchctl', 'stop', LAUNCHD_DASHBOARD], capture_output=True)
        import time
        time.sleep(1)
        subprocess.run(['launchctl', 'start', LAUNCHD_DASHBOARD], check=True, capture_output=True)
        return "🔄 Dashboard restarting..."
    except Exception as e:
        return f"❌ Dashboard restart failed: {e}"


# ── TELEGRAM API ──
async def tg_post(method: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{BASE_URL}/{method}", json=payload)
            return r.json()
        except Exception as e:
            logger.error(f"TG API {method}: {e}")
            return {"ok": False}


async def send_message(chat_id: str, text: str, keyboard=None, parse_mode="HTML"):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    return await tg_post("sendMessage", payload)


async def edit_message(chat_id: str, message_id: int, text: str, keyboard=None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    return await tg_post("editMessageText", payload)


async def answer_callback(query_id: str, text: str):
    await tg_post("answerCallbackQuery", {"callback_query_id": query_id, "text": text[:200]})


# ── KEYBOARDS ──
def main_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "📊 STATUS", "callback_data": "cmd_status"},
                {"text": "📈 PnL", "callback_data": "cmd_pnl"}
            ],
            [
                {"text": "📋 POSITIONS", "callback_data": "cmd_positions"},
                {"text": "📜 TRADES", "callback_data": "cmd_trades"}
            ],
            [
                {"text": "🏥 HEALTH", "callback_data": "cmd_health"},
                {"text": "🔄 REFRESH", "callback_data": "cmd_refresh"}
            ],
            [
                {"text": "🟢 START BOT", "callback_data": "cmd_start"},
                {"text": "🔴 STOP BOT", "callback_data": "cmd_stop"}
            ],
            [
                {"text": "🧹 CLEAN RESTART", "callback_data": "cmd_restart"},
                {"text": "⚙️ CONFIG", "callback_data": "cmd_config"}
            ],
            [
                {"text": "💰 WITHDRAW", "callback_data": "cmd_withdraw"}
            ]
        ]
    }


def health_menu_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🔍 CHECK AGAIN", "callback_data": "cmd_health"},
                {"text": "🧹 CLEAN RESTART", "callback_data": "cmd_restart"}
            ],
            [
                {"text": "📊 FULL STATUS", "callback_data": "cmd_fullstatus"},
                {"text": "🔄 DASH RESTART", "callback_data": "cmd_dashrestart"}
            ],
            [
                {"text": "◀️ BACK TO MENU", "callback_data": "cmd_menu"}
            ]
        ]
    }


def back_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "🏠 MENU", "callback_data": "cmd_menu"},
                {"text": "🔄 REFRESH", "callback_data": "cmd_refresh"}
            ]
        ]
    }


# ── FORMATTERS ──
def format_sweeper_status() -> str:
    """Load sweeper state and return a one-line status string."""
    sweeper_path = os.path.join(_HERE, 'state', 'sweeper_state.json')
    try:
        if not os.path.exists(sweeper_path):
            return "💤 Not active"
        with open(sweeper_path, 'r') as f:
            data = json.load(f)
        mode = data.get('mode', '?')
        accrued = data.get('accumulated_pnl', 0)
        total = data.get('total_swept_usd', 0)
        count = data.get('total_swept_count', 0)
        if mode == 'disabled':
            return "🔒 Disabled"
        if total > 0:
            return f"🧹 ${accrued:.0f} accrued | ${total:.0f} swept ({count}x)"
        return f"⏳ ${accrued:.0f} accrued (thresh: $50)"
    except Exception:
        return "💤 No sweeper state"


def format_true_balance() -> float:
    """Calculate true balance = cash + unrealized PnL."""
    state = load_bot_state()
    if not state:
        return 0.0
    bal = state.get('balance', 0)
    positions = state.get('positions', {})
    unrealized = 0.0
    for sym, pos in positions.items():
        entry = pos.get('entry', 0)
        last = pos.get('last_price', entry)
        invested = pos.get('invested', 0)
        if entry > 0:
            unrealized += invested * ((last - entry) / entry)
    return bal + unrealized


def format_status(state: Dict, health: Dict) -> str:
    bal = state.get('balance', 0)
    pnl = state.get('daily_pnl', 0)
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"
    pos_count = len(state.get('positions', {}))
    trades = state.get('trades_today', 0)
    dd = state.get('max_drawdown', 0)
    cl = state.get('consecutive_losses', 0)
    start = 100.0
    total = format_true_balance()
    total_return = (total - start) / start

    overall = health.get('overall', '⚪ UNKNOWN')

    return (
        f"🏛️ <b>HERMES CRYPTO COMMAND CENTER</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Balance: <b>${bal:.2f}</b>\n"
        f"💎 True Value: <b>${total:.2f}</b>\n"
        f"📈 Total Return: <b>{total_return:+.1%}</b>\n"
        f"{pnl_emoji} Daily PnL: <b>${pnl:+.2f}</b>\n"
        f"📊 Positions: <b>{pos_count}</b>\n"
        f"🔄 Trades Today: <b>{trades}</b>\n"
        f"📉 Max DD: <b>{dd:.1%}</b>\n"
        f"🔥 Consec Losses: <b>{cl}/5</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Health: <b>{overall}</b>\n"
        f"Mode: <b>{'🔴 LIVE' if os.getenv('LIVE_MODE','false').lower()=='true' else '📄 PAPER'}</b>\n"
        f"💸 Sweeper: <b>{format_sweeper_status()}</b>\n"
        f"⏰ <code>{datetime.now().strftime('%H:%M:%S')}</code>"
    )


def format_positions(state: Dict) -> str:
    positions = state.get('positions', {})
    if not positions:
        return "📋 <b>NO OPEN POSITIONS</b>\n\nBot is scanning for entries..."

    lines = ["📋 <b>OPEN POSITIONS</b>", "━━━━━━━━━━━━━━━━━━━━━━"]
    total_unrealized = 0.0
    for sym, pos in positions.items():
        entry = pos.get('entry', 0)
        last = pos.get('last_price', entry)
        invested = pos.get('invested', 0)
        stop = pos.get('stop', 0)
        score = pos.get('score', 0)
        highest = pos.get('highest_price', entry)

        if entry > 0:
            pnl_pct = (last - entry) / entry
            pnl_usd = invested * pnl_pct
        else:
            pnl_pct = 0
            pnl_usd = 0

        total_unrealized += pnl_usd
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        rr = (entry - stop) if (entry - stop) > 0 else 0.0001
        current_r = (last - entry) / rr if rr > 0 else 0

        lines.append(
            f"{emoji} <b>{sym}</b>  (score:{score})\n"
            f"   Entry: ${entry:.6f} | Last: ${last:.6f}\n"
            f"   Invested: ${invested:.2f} | PnL: ${pnl_usd:+.2f} ({pnl_pct:+.1%})\n"
            f"   Stop: ${stop:.6f} | Peak: ${highest:.6f} | R:{current_r:.1f}"
        )

    ue = "🟢" if total_unrealized >= 0 else "🔴"
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"{ue} Total Unrealized: <b>${total_unrealized:+.2f}</b>")

    return "\n\n".join(lines)


def format_trades(state: Dict) -> str:
    """Clean trade history — newest-first with cumulative PnL and summary."""
    history = state.get('history', [])
    if not history:
        return "📜 <b>NO TRADES YET</b>\n\nBot is waiting for first entry..."

    # Pre-compute cumulative PnL for every trade index (oldest->newest)
    cum_pnl = []
    running = 0.0
    for t in history:
        running += t.get('pnl', 0)
        cum_pnl.append(running)

    lines = ["📜 <b>TRADE HISTORY LOG</b>", "━━━━━━━━━━━━━━━━━━━━━━"]

    # Show newest first, but use pre-computed cumulative (correct chronological total)
    for idx in range(len(history) - 1, -1, -1):
        t = history[idx]
        pnl = t.get('pnl', 0)
        pnl_pct = t.get('pnl_pct', 0)
        reason = t.get('reason', '?')
        portion = t.get('portion', 1.0)
        portion_tag = " (P)" if portion < 0.99 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"

        # Compact: limit width so Telegram never wraps weirdly
        token_str = f"{t.get('token', '?')}{portion_tag}"
        # Truncate insane returns for display
        pct_str = f"{pnl_pct:+.1f}%"
        if abs(pnl_pct) >= 10000:
            pct_str = f"{pnl_pct:+.2e}"
        pnl_str = f"${pnl:+.2f}"
        cum_str = f"${cum_pnl[idx]:+.2f}"

        line = (
            f"{emoji} <b>{token_str}</b>\n"
            f"   └ {pnl_str}  ({pct_str})  →  Cum: <code>{cum_str}</code>\n"
            f"      {reason}"
        )
        lines.append(line)

    # ── Summary block ──
    total_trades = len(history)
    wins = sum(1 for t in history if t.get('pnl', 0) > 0)
    losses = total_trades - wins
    win_rate = wins / total_trades if total_trades > 0 else 0
    total_pnl = sum(t.get('pnl', 0) for t in history)
    avg_win = sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) > 0) / wins if wins else 0
    avg_loss = sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) <= 0) / losses if losses else 0
    best = max(history, key=lambda x: x.get('pnl', 0))
    worst = min(history, key=lambda x: x.get('pnl', 0))

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"Trades: <b>{total_trades}</b> | "
        f"Wins: <b>{wins}</b> | "
        f"Losses: <b>{losses}</b>\n"
        f"Win Rate: <b>{win_rate:.0%}</b> | "
        f"Avg Win: <b>${avg_win:+.2f}</b> | "
        f"Avg Loss: <b>${avg_loss:+.2f}</b>"
    )
    lines.append(
        f"🏆 Best: <b>{best['token']}</b> {best['pnl']:+.2f} | "
        f"💀 Worst: <b>{worst['token']}</b> {worst['pnl']:+.2f}"
    )
    lines.append(f"\u003cb>📊 NET REALIZED PnL: ${total_pnl:+.2f}</b>")

    text = "\n\n".join(lines)
    if len(text) > 4000:
        cutoff = text[:4000].rfind('\n\n')
        text = text[:cutoff] + "\n\n... (truncated — too many trades for one message)"
    return text


def format_pnl(state: Dict) -> str:
    bal = state.get('balance', 0)
    start = 100.0
    total = format_true_balance()
    total_return = (total - start) / start
    pnl = state.get('daily_pnl', 0)
    history = state.get('history', [])

    # Calculate stats
    total_pnl = sum(t.get('pnl', 0) for t in history)
    wins = sum(1 for t in history if t.get('pnl', 0) > 0)
    losses = len(history) - wins
    winning_pct = wins / len(history) if history else 0

    avg_win = sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) > 0) / wins if wins else 0
    avg_loss = sum(t.get('pnl', 0) for t in history if t.get('pnl', 0) <= 0) / losses if losses else 0

    # Best / worst
    if history:
        best = max(history, key=lambda x: x.get('pnl', 0))
        worst = min(history, key=lambda x: x.get('pnl', 0))
    else:
        best = None
        worst = None

    return (
        f"📈 <b>PROFIT & LOSS (TRUE BALANCE)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Current Value: <b>${total:.2f}</b>\n"
        f"💵 Cash: <b>${bal:.2f}</b>\n"
        f"📊 Starting: <b>${start:.2f}</b>\n"
        f"📈 Total Return: <b>{total_return:+.1%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Daily PnL: <b>${pnl:+.2f}</b>\n"
        f"Total Realized: <b>${total_pnl:+.2f}</b>\n"
        f"Wins: <b>{wins}</b> | Losses: <b>{losses}</b>\n"
        f"Win Rate: <b>{winning_pct:.0%}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Avg Win: <b>${avg_win:.2f}</b>\n"
        f"Avg Loss: <b>${avg_loss:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 Best:  {best['token'] if best else 'N/A'} ${best['pnl']:.2f if best else ''}\n"
        f"💀 Worst: {worst['token'] if worst else 'N/A'} ${worst['pnl']:.2f if worst else ''}"
    )


def format_health(health: Dict) -> str:
    issues = health.get('issues', [])
    issues_text = "None  🎉" if not issues else "\n".join(f"  ❌ {i}" for i in issues)

    return (
        f"🏥 <b>SYSTEM HEALTH REPORT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Overall: <b>{health.get('overall', '⚪ UNKNOWN')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Bot Process: {'🟢' if health.get('bot_process') else '🔴'} {health.get('bot_pid', 'N/A')}\n"
        f"📡 Dashboard: {'🟢' if health.get('dash_process') else '🔴'} {health.get('dash_pid', 'N/A')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Bot LaunchD: {health.get('bot_launchd', '?')}\n"
        f"📦 Dash LaunchD: {health.get('dash_launchd', '?')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💾 Disk Free: {health.get('free_gb', 0):.1f} GB {health.get('disk_status', '')}\n"
        f"📝 Logs: {health.get('log_status', '')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Issues:\n{issues_text}"
    )


def format_full_status(state: Dict, health: Dict) -> str:
    """Combined status + health."""
    return format_status(state, health) + "\n\n" + format_health(health)


def format_config() -> str:
    try:
        with open(os.path.join(_HERE, 'config', 'HERMES_CRYPTO_CONFIG.yaml'), 'r') as f:
            cfg = f.read()[:1400]
    except:
        cfg = "Config not found"
    return f"⚙️ <b>BOT CONFIG</b>\n━━━━━━━━━━━━━━━━━━━━━━\n<pre>{cfg}</pre>"


def format_withdraw(state: Dict) -> str:
    """Show withdraw info — wallet addresses and current balance."""
    if not state:
        return "❌ <b>STATE UNAVAILABLE</b>\n\nCannot show withdraw info."

    bal = state.get('balance', 0)
    positions = state.get('positions', {})
    total_unrealized = 0.0
    for sym, pos in positions.items():
        entry = pos.get('entry', 0)
        last = pos.get('last_price', entry)
        invested = pos.get('invested', 0)
        if entry > 0:
            pnl_pct = (last - entry) / entry
            pnl_usd = invested * pnl_pct
            total_unrealized += pnl_usd

    total_value = bal + total_unrealized

    # Get wallet addresses from .env or config
    wallet_info = []
    cold_sol  = os.getenv('COLD_WALLET_SOL', '')
    cold_eth  = os.getenv('COLD_WALLET_ETH', '')
    exodus_pk = os.getenv('EXODUS_PRIVATE_KEY', '')

    # Show public addresses only — never show private key or prefix
    if cold_sol:
        wallet_info.append(f"❄️ Cold SOL: <code>{cold_sol}</code>")
    if cold_eth:
        wallet_info.append(f"❄️ Cold ETH: <code>{cold_eth}</code>")
    if exodus_pk:
        # Derive public key from private key — never expose the private key
        try:
            from solders.keypair import Keypair
            kp = Keypair.from_base58_string(exodus_pk)
            wallet_info.append(f"👛 Hot SOL: <code>{str(kp.pubkey())}</code>")
        except Exception:
            wallet_info.append("👛 Hot SOL: <i>key loaded (address unavailable)</i>")

    wallet_text = "\n".join(wallet_info) if wallet_info else "⚠️ No wallets configured in .env"

    return (
        f"💰 <b>WITHDRAWAL INFO</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Available Cash: <b>${bal:.2f}</b>\n"
        f"Unrealized PnL: <b>${total_unrealized:+.2f}</b>\n"
        f"Total Value: <b>${total_value:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>WALLETS:</b>\n{wallet_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>To withdraw:</i>\n"
        f"1. Stop bot (🔴 STOP BOT)\n"
        f"2. Export keys from .env\n"
        f"3. Use wallet app to transfer\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Configure wallets in .env:</i>\n"
        f"COLD_WALLET_SOL=your_address\n"
        f"COLD_WALLET_ETH=your_address"
    )


# ── COMMAND HANDLERS ──
async def handle_command(chat_id: str, text: str):
    state = load_bot_state()
    cmd = text.lower().replace('/', '')
    health = full_health_check()

    if cmd == 'start':
        await send_message(chat_id, "🏛️ Hermes Crypto Command Center\n\nUse the buttons below:", main_menu_keyboard())
    elif cmd == 'status':
        msg = format_status(state, health) if state else "❌ State unavailable"
        await send_message(chat_id, msg, main_menu_keyboard())
    elif cmd == 'positions':
        msg = format_positions(state) if state else "❌ State unavailable"
        await send_message(chat_id, msg, back_keyboard())
    elif cmd == 'trades':
        msg = format_trades(state) if state else "❌ State unavailable"
        await send_message(chat_id, msg, back_keyboard())
    elif cmd == 'pnl':
        msg = format_pnl(state) if state else "❌ State unavailable"
        await send_message(chat_id, msg, back_keyboard())
    elif cmd == 'health':
        await send_message(chat_id, format_health(health), health_menu_keyboard())
    elif cmd == 'config':
        await send_message(chat_id, format_config(), back_keyboard())
    elif cmd == 'menu':
        await send_message(chat_id, "🏛️ Hermes Crypto Command Center", main_menu_keyboard())
    elif cmd == 'restart':
        result = bot_restart_clean()
        await send_message(chat_id, f"🧹 <b>CLEAN RESTART</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{result}", main_menu_keyboard())
    elif cmd == 'withdraw':
        msg = format_withdraw(state) if state else "❌ State unavailable"
        await send_message(chat_id, msg, main_menu_keyboard())
    else:
        await send_message(chat_id, f"Unknown: {text}\nTry: /start /status /positions /pnl /trades /health /restart /config /withdraw")


async def handle_callback(query: dict):
    data = query.get('data', '')
    chat_id = str(query['message']['chat']['id'])
    msg_id = query['message']['message_id']
    query_id = query['id']

    state = load_bot_state()
    health = full_health_check()

    if data == 'cmd_menu':
        await edit_message(chat_id, msg_id, "🏛️ Hermes Crypto Command Center", main_menu_keyboard())
        await answer_callback(query_id, "Menu opened")

    elif data == 'cmd_status':
        msg = format_status(state, health) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, main_menu_keyboard())
        await answer_callback(query_id, "Status refreshed")

    elif data == 'cmd_positions':
        msg = format_positions(state) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, back_keyboard())
        await answer_callback(query_id, "Positions loaded")

    elif data == 'cmd_trades':
        msg = format_trades(state) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, back_keyboard())
        await answer_callback(query_id, "Trades loaded")

    elif data == 'cmd_pnl':
        msg = format_pnl(state) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, back_keyboard())
        await answer_callback(query_id, "PnL loaded")

    elif data == 'cmd_health':
        await edit_message(chat_id, msg_id, format_health(health), health_menu_keyboard())
        await answer_callback(query_id, "Health check done")

    elif data == 'cmd_fullstatus':
        if state:
            msg = format_full_status(state, health)
        else:
            msg = "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, health_menu_keyboard())
        await answer_callback(query_id, "Full status loaded")

    elif data == 'cmd_config':
        await edit_message(chat_id, msg_id, format_config(), back_keyboard())
        await answer_callback(query_id, "Config loaded")

    elif data == 'cmd_start':
        result = bot_start()
        await edit_message(chat_id, msg_id, result, main_menu_keyboard())
        await answer_callback(query_id, "Bot starting...")

    elif data == 'cmd_stop':
        result = bot_stop()
        await edit_message(chat_id, msg_id, result, main_menu_keyboard())
        await answer_callback(query_id, "Bot stopped")

    elif data == 'cmd_restart':
        result = bot_restart_clean()
        await edit_message(chat_id, msg_id, f"🧹 <b>CLEAN RESTART</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{result}", health_menu_keyboard())
        await answer_callback(query_id, "Restarting...")

    elif data == 'cmd_dashrestart':
        result = dashboard_restart()
        await edit_message(chat_id, msg_id, result, health_menu_keyboard())
        await answer_callback(query_id, "Dashboard restarting...")

    elif data == 'cmd_refresh':
        msg = format_status(state, health) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, main_menu_keyboard())
        await answer_callback(query_id, "Refreshed")

    elif data == 'cmd_withdraw':
        msg = format_withdraw(state) if state else "❌ State unavailable"
        await edit_message(chat_id, msg_id, msg, main_menu_keyboard())
        await answer_callback(query_id, "Withdraw info loaded")

    else:
        await answer_callback(query_id, f"Unknown: {data}")


# ── POLLING LOOP ──
offset = 0


async def poll_updates():
    global offset
    while True:
        try:
            resp = await tg_post("getUpdates", {"offset": offset, "limit": 10})
            if not resp.get('ok'):
                await asyncio.sleep(2)
                continue

            for update in resp.get('result', []):
                offset = update['update_id'] + 1

                if 'message' in update:
                    msg = update['message']
                    chat_id = str(msg['chat']['id'])
                    text = msg.get('text', '')
                    user_id = str(msg['from']['id'])

                    if user_id != ADMIN_ID:
                        await send_message(chat_id, "⛔ Unauthorized")
                        continue

                    if text.startswith('/'):
                        await handle_command(chat_id, text)

                elif 'callback_query' in update:
                    query = update['callback_query']
                    user_id = str(query['from']['id'])
                    if user_id != ADMIN_ID:
                        await answer_callback(query['id'], "⛔ Unauthorized")
                        continue
                    await handle_callback(query)

        except Exception as e:
            logger.error(f"Poll error: {e}")
            await asyncio.sleep(5)


# ── SIGNAL HANDLER ──
shutdown_event = asyncio.Event()


def signal_handler(sig, frame):
    logger.info("Shutting down...")
    shutdown_event.set()


# ── MAIN ──
async def main():
    logger.info("=" * 50)
    logger.info("🏛️ HERMES CRYPTO DASHBOARD v3 STARTING")
    logger.info(f"Admin: {ADMIN_ID}")
    logger.info("=" * 50)

    # Health check at startup
    h = full_health_check()
    logger.info(f"Health: {h['overall']}")

    await send_message(ADMIN_ID,
        f"🏛️ <b>Hermes Dashboard v3 Online</b>\n"
        f"Health: {h['overall']}\n"
        f"Bot: {'ok' if h['bot_process'] else 'down'} | "
        f"Dash: {'ok' if h['dash_process'] else 'down'}\n"
        f"Use /start to open command center",
        main_menu_keyboard())

    try:
        await poll_updates()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Dashboard stopped")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    asyncio.run(main())
