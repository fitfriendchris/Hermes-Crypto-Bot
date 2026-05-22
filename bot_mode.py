"""Bot mode state machine — single source of truth for which strategy is active.

Modes:
  OFF      — no entries; only monitors open positions
  SNIPER   — fast-scalp: momentum + launch-sniper entries, tight take-profits
  COPY     — mirrors verified whale wallets, wide stops + 2x principal recovery

State lives in state/bot_mode.json (separate from main bot state so the
Telegram dashboard can flip the mode atomically without racing the main
bot's state save loop).

Open positions are tagged with `mode_at_entry` on creation. Their exit rules
are dispatched from that field, so flipping the mode does NOT change exits
for live trades — only future entries.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger('CryptoBot')

_HERE = os.path.dirname(os.path.abspath(__file__))
MODE_PATH = os.path.join(_HERE, 'state', 'bot_mode.json')

MODE_OFF = "OFF"
MODE_SNIPER = "SNIPER"
MODE_COPY = "COPY"
MODE_HIGH_ATTENTION = "HIGH_ATTENTION"
VALID_MODES = (MODE_OFF, MODE_SNIPER, MODE_COPY, MODE_HIGH_ATTENTION)


def _atomic_write(path: str, data: dict):
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def get_mode() -> str:
    """Read the current active mode. Defaults to OFF if file missing/corrupt."""
    if not os.path.exists(MODE_PATH):
        return MODE_OFF
    try:
        with open(MODE_PATH) as f:
            data = json.load(f)
        m = data.get('mode', MODE_OFF)
        return m if m in VALID_MODES else MODE_OFF
    except Exception:
        return MODE_OFF


def set_mode(mode: str, reason: str = "") -> dict:
    """Set the active mode. Returns the new state dict. Validates."""
    if mode not in VALID_MODES:
        raise ValueError(f"invalid mode {mode!r}; expected {VALID_MODES}")
    data = {
        'mode': mode,
        'changed_at': datetime.utcnow().isoformat(),
        'reason': reason,
    }
    _atomic_write(MODE_PATH, data)
    logger.info(f"🎚️ MODE → {mode} ({reason or 'manual'})")
    return data


def is_active(mode: str) -> bool:
    """True if the given mode is currently active. Convenience for loop gates."""
    return get_mode() == mode


def can_enter() -> bool:
    """True if the bot may open new positions in *any* mode."""
    return get_mode() != MODE_OFF


def get_mode_label() -> str:
    """Human-readable mode label."""
    mode = get_mode()
    labels = {
        MODE_OFF: "🔴 OFF",
        MODE_SNIPER: "🎯 SNIPER",
        MODE_COPY: "🐋 COPY",
        MODE_HIGH_ATTENTION: "🔥 HIGH ATTENTION",
    }
    return labels.get(mode, mode)


def is_high_attention() -> bool:
    """True if currently in HIGH_ATTENTION mode."""
    return get_mode() == MODE_HIGH_ATTENTION
