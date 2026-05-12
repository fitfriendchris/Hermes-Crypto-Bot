# HERMES CRYPTO BOT — DATA CAPTURE PROTOCOL
## Started: 2026-05-11 23:42 CDT
## Objective: Capture 48-72h of trading data for optimization

---

## METRICS TO TRACK

### Per Trade
```json
{
  "timestamp": "ISO-8601",
  "symbol": "TOKEN",
  "entry_price": 0.001,
  "exit_price": 0.0012,
  "position_size_usd": 10.0,
  "pnl_usd": 2.0,
  "pnl_pct": 20.0,
  "hold_time_hours": 4.5,
  "exit_reason": "tier_1_2.0R",
  "stop_type": "volatility",
  "source": "momentum",
  "momentum_score": 75,
  "volume_ratio": 3.5,
  "ict_bos": true,
  "ict_sweep": true,
  "amd_phase": "manipulation",
  "fvg_bullish": true,
  "market_regime": "bullish"
}
```

### Per Symbol (Lifetime)
```json
{
  "symbol": "TOKEN",
  "total_trades": 1,
  "total_pnl": 2.0,
  "first_trade": "2026-05-11T23:42:00Z",
  "last_trade": "2026-05-11T23:42:00Z",
  "status": "one_time_done"
}
```

### Hourly Snapshot
```json
{
  "timestamp": "2026-05-11T23:00:00Z",
  "balance": 61.68,
  "open_positions": 4,
  "daily_pnl": 15.03,
  "win_rate_24h": 0.35,
  "profit_factor": 0.91,
  "max_drawdown": 0.169,
  "symbols_scanned": 1800,
  "signals_generated": 45,
  "trades_executed": 12
}
```

---

## DATA FILES

| File | Purpose | Rotation |
|------|---------|----------|
| `logs/HERMES_CRYPTO_BOT.log` | Raw trade logs | Daily |
| `state/HERMES_CRYPTO_STATE.json` | Portfolio state | Every 60s |
| `state/symbol_cooldowns.json` | Cooldown tracking | Every trade |
| `state/symbol_lifetime.json` | Lifetime tracking | Every trade |
| `data/trades_YYYYMMDD.jsonl` | Structured trade data | Daily |
| `data/signals_YYYYMMDD.jsonl` | All signals (even rejected) | Daily |
| `data/hourly_snapshots.jsonl` | Portfolio snapshots | Hourly |

---

## AUTOMATED ANALYSIS (Every 6 Hours)

```python
def analyze_performance():
    """Run every 6 hours during data capture."""
    
    # 1. Win rate by symbol
    # 2. Win rate by signal type (momentum vs copy vs scanner)
    # 3. Win rate by momentum score bucket (60-70, 70-80, 80-90, 90+)
    # 4. Win rate by AMD phase
    # 5. Win rate by volume ratio
    # 6. Average hold time for winners vs losers
    # 7. Optimal stop loss width (backtest 20%, 25%, 30%, 35%, 40%)
    # 8. Optimal take profit levels (1.5R, 2R, 3R, 5R)
    # 9. Best performing symbols (for potential whitelist)
    # 10. Worst performing symbols (for permanent blacklist)
    
    # Generate report
    report = {
        'period': '6h',
        'trades': [],
        'win_rate': 0.0,
        'profit_factor': 0.0,
        'recommendations': []
    }
    
    return report
```

---

## OPTIMIZATION TRIGGERS

Auto-optimize when:
1. **30 trades completed** → Analyze symbol performance
2. **50 trades completed** → Analyze signal quality
3. **100 trades completed** → Full strategy review
4. **24h elapsed** → Daily report + parameter adjustment
5. **48h elapsed** → Final optimization plan

---

## SUCCESS CRITERIA

| Metric | Current | Target 48h | Target 7d |
|--------|---------|------------|-----------|
| Win Rate | 35% | >45% | >50% |
| Profit Factor | 0.91 | >1.2 | >1.5 |
| Expectancy | -$0.16 | >+$0.50 | >+$1.00 |
| Max Drawdown | 16.9% | <15% | <12% |
| Unique Symbols | 100 | 50+ | 200+ |

---

## NOTES

- Bot is in PAPER mode — no real money at risk
- One-and-done rule prevents churn
- ICT + AMD filters should improve entry quality
- Data will reveal which filters work and which don't

---

**Next Check-in: 6 hours (2026-05-12 05:42 CDT)**
