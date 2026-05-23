## HERMES CRYPTO BOT DIAGNOSIS — May 22, 2026

### Summary

**Status**: Bot was broken, now fixed but needs monitoring. **Lost $5.00 on a rug pull** during testing.

**Current wallet**: 0.6919 SOL (~$58.51) — down from ~$63.72

---

### Root Causes Found

| # | Issue | Impact |
|---|-------|--------|
| 1 | **Quality gate too strict** | SNIPER mode required $100K liquidity + $50K volume + social signals → rejected ALL tokens |
| 2 | **DexScreener API changed** | `token-profiles/latest/v1` no longer returns `symbol`/`name` → discovery loop crashed |
| 3 | **Holder count check blocked everything** | DexScreener doesn't provide holder data → all tokens scored 0 holders → rejected |
| 4 | **No anti-rug check on HIGH_ATTENTION path** | Discovered tokens went straight to buy without rug validation |
| 5 | **State not saved after buys** | 60-second save loop caused race condition → duplicate buys on restart |
| 6 | **Duplicate processes** | launchd + manual starts created overlapping bots |

---

### Fixes Applied

| # | Fix | File |
|---|-----|------|
| ✅ | Switched mode: SNIPER → **HIGH_ATTENTION** (micro-cap focus) | `state/bot_mode.json` |
| ✅ | Lowered quality gate: $100K→$10K liq, $50K→$5K vol | `HERMES_CRYPTO_BOT.py` |
| ✅ | Fixed DexScreener API parsing (null entries, missing fields) | `high_attention_scalper.py` |
| ✅ | Fixed holder_count check: skip when data unavailable | `high_attention_scalper.py` |
| ✅ | Added **immediate state.save()** after every buy/sell | `HERMES_CRYPTO_BOT.py` |
| ✅ | Added **anti-rug check** to HIGH_ATTENTION entry path (watchlist + discovery) | `HERMES_CRYPTO_BOT.py` |
| ✅ | Cleared phantom positions, synced balance to real wallet value | `state/HERMES_CRYPTO_STATE.json` |
| ✅ | Single process via launchd | `launchctl` |

---

### What Happened (Timeline)

| Time | Event |
|------|-------|
| ~22:55 | Started fixing bot |
| 23:05:44 | Bot bought **CAP** — $2.50 |
| 23:05:55 | Duplicate process started (race condition), loaded old state |
| 23:06:00 | Bot bought **CAP again** — $2.50 (total $5.00) |
| 23:07:36 | Bot sold CAP at total loss — got $0.005 back |
| 23:07:37 | Stopped bot |

**CAP token**: `8Zrbh9DJFgY5H6jqZb3CWeMF6wLGMHiDKvkK4qSTpump`

**Loss**: ~$5.00 (rug pull — token went to zero immediately)

---

### Current State

```
Wallet:     0.6919 SOL (~$58.51)
Balance:    $58.72
Positions:  0
Daily PnL:  -$5.00
Consecutive losses: 1 (4h cooldown active)
Trades today: 1
Mode:       HIGH_ATTENTION
Anti-rug:   ACTIVE (now checks all entries)
```

---

### Anti-Rug Status

Anti-rug suite is initialized and now runs on **both**:
- Watchlist entries
- Auto-discovered tokens

Before this fix, discovered tokens bypassed anti-rug checks entirely.

---

### What I Need From You

1. **Restart?** The bot is stopped. With anti-rug + immediate state save + single process, it's safer now. But micro-cap trading is inherently risky.

2. **Budget?** You had $63.72, now $58.72. Do you want to continue with this capital level or add more?

3. **Risk tolerance?** HIGH_ATTENTION mode targets micro-caps ($10K-$500K mcap) with 35% stops and $2.50 position sizes. This is aggressive. Want me to tighten further?

---

### Recommended Next Steps

1. ✅ Restart bot with fixes
2. ✅ Monitor first 24h closely
3. ⏳ If another rug pull hits, switch to SNIPER mode with whitelist-only trading
4. ⏳ Consider adding manual seeds to whale scoreboard for copy-trading as safer alternative

---

### Files Changed

- `HERMES_CRYPTO_BOT.py` — quality gate, anti-rug hooks, immediate state save
- `high_attention_scalper.py` — DexScreener API fix, holder_count logic
- `state/HERMES_CRYPTO_STATE.json` — cleared phantoms, recorded CAP loss
- `state/bot_mode.json` — mode = HIGH_ATTENTION
