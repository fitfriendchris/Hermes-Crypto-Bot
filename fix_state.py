#!/usr/bin/env python3
import json
from datetime import datetime, timedelta

# Load state
with open('state/HERMES_CRYPTO_STATE.json', 'r') as f:
    state = json.load(f)

# ONLY reset circuit breaker — DO NOT touch balance tracking
state['halt_entries_until'] = None
state['halt_reason'] = ''
state['weekly_pnl'] = 0.0
state['consecutive_losses'] = 0

# Save
with open('state/HERMES_CRYPTO_STATE.json', 'w') as f:
    json.dump(state, f, indent=2)

print('✅ Circuit breaker RESET')
print(f'   Balance: ${state["balance"]:.2f}')
print(f'   Weekly PnL: ${state["weekly_pnl"]:.2f}')
print(f'   Consecutive losses: {state["consecutive_losses"]}')
print(f'   Halt cleared: {state["halt_entries_until"]}')
