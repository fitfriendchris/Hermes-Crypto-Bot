"""Hermes Crypto Bot strategy modules.

Each module exports a single async coroutine `find_opportunities(state, config)` that
returns a list of opportunity dicts the main bot can evaluate. Strategies share the
unified entry/exit pipeline in HERMES_CRYPTO_BOT.py — they do not place trades
directly.
"""
