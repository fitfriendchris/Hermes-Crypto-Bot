# HERMES CRYPTO BOT v2.1 — ARCHIVE
## Continuous Use Documentation

---

## WHAT'S ARCHIVED

| Component | Status | File |
|-----------|--------|------|
| Main Bot | ✅ Active | `HERMES_CRYPTO_BOT.py` |
| Launch Sniper | ✅ Active | `launch_sniper.py` |
| Anti-Rug Suite | ✅ Active | `anti_rug_suite.py` |
| Momentum Scanner | ✅ Active | `momentum_scanner.py` |
| Copy Trader | ✅ Active | `copy_trader.py` |
| Symbol Lifetime | ✅ Active | `HERMES_CRYPTO_BOT.py` |
| Telegram Dashboard | ✅ Active | `HERMES_TELEGRAM_DASHBOARD.py` |

---

## QUICK START

```bash
# Start bot (paper mode)
cd ~/Hermes-Crypto-Bot
./start_hermes.sh paper

# Monitor
tail -f logs/HERMES_CRYPTO_BOT.log

# Stop
pkill -f HERMES_CRYPTO_BOT.py
```

---

## DATA CAPTURE

**Active tracking:**
- All trades (win/loss/exit reason)
- All signals (even rejected)
- Hourly portfolio snapshots
- Symbol lifetime history
- Cooldown/churn data

**Analysis schedule:**
- 6 hours → Performance snapshot
- 24 hours → Full daily report
- 48 hours → Optimization recommendations

---

## KEY METRICS (Baseline)

| Metric | Before v2.1 | Target |
|--------|-------------|--------|
| Win Rate | 35% | 50%+ |
| Profit Factor | 0.91 | 1.5+ |
| Expectancy | -$0.16 | +$1.00+ |
| Avg Hold | <1 hour | 4+ hours |

---

## MODULES EXPLAINED

### 1. Launch Sniper
- Monitors Pump.fun + Raydium
- Snipes at $10K liquidity
- Auto-exit at 2R
- 4-hour time stop

### 2. Anti-Rug Suite
- 9 safety checks
- Score: 0-100 (70+ = safe)
- Blocks honeypots, mints, unlocked LP

### 3. Momentum Scanner
- ICT market structure
- AMD cycle detection
- Fair Value Gap
- Volume >2x requirement

### 4. Copy Trader
- Tracks whale wallets
- Proportional sizing
- Only active wallets (>60% WR)

### 5. Symbol Lifetime
- One-and-done micro-caps
- Permanent blacklist (10 symbols)
- Dynamic blacklist (7d after 2 losses)
- Profit cooldown (72h)

---

## FILES REFERENCE

```
~/Hermes-Crypto-Bot/
├── HERMES_CRYPTO_BOT.py              # Main bot
├── launch_sniper.py                    # Launch sniper
├── anti_rug_suite.py                 # Anti-rug checks
├── momentum_scanner.py               # ICT + AMD filters
├── copy_trader.py                    # Whale copy trading
├── HERMES_TELEGRAM_DASHBOARD.py      # Telegram alerts
├── HERMES_wallet_integration.py      # Wallet manager
├── HERMES_swap_executor.py           # DEX executor
├── config/
│   └── HERMES_CRYPTO_CONFIG.yaml     # Configuration
├── logs/
│   └── HERMES_CRYPTO_BOT.log         # Trade logs
├── state/
│   ├── HERMES_CRYPTO_STATE.json      # Portfolio
│   ├── symbol_cooldowns.json         # Cooldowns
│   ├── symbol_lifetime.json           # Trade history
│   └── wallet_performance.json       # Copy trader data
├── start_hermes.sh                    # Startup script
├── ARCHIVE.md                         # This file
├── UPGRADE_COMPLETE.md                # Upgrade log
├── OPTIMIZATION_PLAN.md               # Analysis + plan
├── DATA_CAPTURE_PROTOCOL.md           # Metrics tracking
└── VERIFIED_STRATEGIES_RESEARCH.md   # Research
```

---

## GITHUB BACKUP

**Repo:** `https://github.com/yuhfriendchris/hermes-crypto-bot`
**Status:** Not created yet
**To create:**
1. GitHub → New Repository → `hermes-crypto-bot`
2. Make private
3. Run: `git push -u origin main`

---

## SUPPORT

**Issues?** Check logs:
```bash
tail -50 ~/Hermes-Crypto-Bot/logs/HERMES_CRYPTO_BOT.log
```

**State corrupt?** Restore from backup:
```bash
cp ~/Hermes-Crypto-Bot/state/HERMES_CRYPTO_STATE.json.backup \
   ~/Hermes-Crypto-Bot/state/HERMES_CRYPTO_STATE.json
```

**Kill switch:**
```bash
pkill -f HERMES_CRYPTO_BOT.py
```

---

## VERSION HISTORY

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | 2026-05-10 | Initial bot |
| v2.0 | 2026-05-11 | Stop fixes, cooldowns, churn detection |
| v2.1 | 2026-05-12 | Launch sniper, anti-rug, momentum, copy trader |

---

**Last updated:** 2026-05-12 00:48 CDT
**Bot status:** Running (PID 71196)
**Mode:** Paper
**Balance:** $69.86
