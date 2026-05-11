# Hermes Crypto Trading Bot

Autonomous DEX crypto trading bot for Solana + EVM chains.

## Architecture

```
Discovery Loop (30–60s)
  └─ DEXConnector → DexScreener / Pump.fun / Birdeye → score & filter

Monitor Loop (5–10s)
  └─ For each open position → get_price() → check_exit()

Report Loop (5min)
  └─ Portfolio value, drawdown, daily PnL, Telegram alert

Save Loop (60s)
  └─ Persist state to bot_state.json
```

## Files

| File | Purpose |
|------|---------|
| `crypto_bot.py` | Main runner |
| `CRYPTO_BOT_CONFIG.yaml` | All settings |
| `CRYPTO_BOT_MAIN.py` | Engine (SmartStops, PositionManager, ExchangeConnector) |
| `dex_connector.py` | DEX discovery |
| `wallet_integration.py` | Phantom + MetaMask + Exodus |
| `telegram_alerts.py` | Real-time alerts |
| `profit_protection.py` | Auto-transfer to cold storage |
| `bot_state.json` | Persisted portfolio |
| `.env` | Secrets — NEVER committed |
| `TODO.md` | Project backlog |

## Security

- **NEVER commit .env**
- **NEVER paste seed phrases in chat**
- Use `.env.example` as template

## Mode

| Mode | How |
|------|-----|
| Paper | `LIVE_MODE=false` (default) |
| Live | `LIVE_MODE=true` + wallet key |

## Quick Start

```bash
# Install
cd ~/Desktop/Hermes-Crypto-Bot
pip install -r requirements.txt

# Configure
cp .env.example .env
nano .env  # Add your keys

# Run (paper mode)
python3 crypto_bot.py

# Run (live mode)
LIVE_MODE=true python3 crypto_bot.py
```

## Integration

- **AI4Trade:** Publish signals to `https://ai4trade.ai`
- **Sovereign:** Treasury allocation via `sovereign /treasury`
- **Omni:** Mirror signals for XAUUSD/XAGUSD correlation

## Status

- [x] Architecture designed
- [x] Config written
- [ ] Code implemented
- [ ] Wallets configured
- [ ] Funded
- [ ] Live tested
