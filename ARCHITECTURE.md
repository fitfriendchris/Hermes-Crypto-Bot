# Hermes Crypto Trading Bot — Architecture Plan

## Purpose

**Fund Sovereign businesses through autonomous crypto trading.**

Not a product to sell. A capital-generating engine. Every dollar of profit goes to the Treasury.

## Design Principles

1. **Risk-first** — Preservation of capital above all else
2. **Edge-seeking** — Multi-source intelligence for asymmetric opportunities
3. **Hedging** — Never directional without a hedge
4. **Backtested** — Every strategy validated before live deployment
5. **Automated** — Hands-off execution, hands-on monitoring

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          MARKET INTELLIGENCE LAYER                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │ DexScreener  │  │  Pump.fun    │  │   Birdeye   │  │  AI4Trade  │ │
│  │ (micro-caps) │  │ (new tokens) │  │ (analytics)  │  │ (signals)  │ │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘ │
│         │                  │                  │                │        │
└─────────┼──────────────────┼──────────────────┼────────────────┼────────┘
          │                  │                  │                │
          ▼                  ▼                  ▼                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      STRATEGY ENGINE                                  │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────────┐ │
│  │ Backtester │  │  Scoring   │  │  Hedging   │  │ Portfolio       │ │
│  │  (hist)    │  │  (real)    │  │ Analyzer   │  │ Optimizer       │ │
│  └────────────┘  └────────────┘  └────────────┘  └────────────────┘ │
│                                                                       │
│  Strategies:                                                          │
│  1. Micro-cap Momentum — New listings, volume spikes, early liquidity │
│  2. BTC Correlation Hedge — Long BTC, hedge with alt exposure        │
│  3. AI4Trade Signal Copy — Follow top agents, validate with on-chain │
│  4. Arbitrage — Cross-DEX price differences (Jupiter vs Raydium)    │
│  5. Liquidity Mining — Yield farming with impermanent loss hedging    │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     EXECUTION LAYER                                   │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │  Jupiter   │  │  Raydium   │  │  Orca      │  │  Phoenix   │    │
│  │  (swap)    │  │  (swap)    │  │  (swap)    │  │  (orderbook)│   │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘    │
│                                                                       │
│  Paper Mode → Simulated (default)                                     │
│  Live Mode  → Real transactions (LIVE_MODE=true)                      │
└──────────────────────────────┬────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      RISK & TREASURY LAYER                            │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐    │
│  │  Smart     │  │  Position  │  │  Drawdown  │  │  Profit    │    │
│  │  Stops     │  │  Sizing    │  │  Circuit   │  │  Transfer  │    │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘    │
│                                                                       │
│  Max 2% risk/trade | 5% daily loss limit | 10% max drawdown          │
│  Auto-transfer 50% profits to cold storage after $100 threshold       │
└──────────────────────────────────────────────────────────────────────┘
```

## Hedging Strategy

### 1. Cross-DEX Arbitrage
- Monitor same token across Jupiter, Raydium, Orca
- Execute when price divergence > 0.5% after fees
- Risk: Low (near market-neutral)

### 2. BTC-Alt Correlation Hedge
- Long BTC as "safe" anchor
- Short correlated alts when BTC dominance spikes
- Risk: Medium (correlation breakdown)

### 3. Options Hedge (if available)
- Buy OTM puts on BTC for catastrophic protection
- Cost: ~2% monthly premium
- Risk: Low (defined cost)

### 4. Stablecoin Yield Hedge
- Park profits in USDC yield (Kamino, MarginFi)
- Earn 5-15% APY while waiting for setups
- Risk: Very low (smart contract risk)

### 5. AI4Trade Signal Validation
- Follow top traders, but validate with on-chain data
- If signal says "buy" but wallets are selling = ignore
- Risk: Medium (signal lag)

## Multi-Source Intelligence

| Source | Data | Frequency | Cost |
|--------|------|-----------|------|
| DexScreener | All DEX pairs | Real-time | Free |
| Pump.fun | New token launches | Real-time | Free |
| Birdeye | Advanced analytics | Real-time | Free tier |
| Jupiter | Swap quotes | Real-time | Free |
| Solscan | Wallet tracking | On-demand | Free |
| AI4Trade | Agent signals | Real-time | Free |
| Helius | RPC + webhooks | Real-time | Free tier |

## Strategy Backtesting

```python
# Pseudocode
class Backtester:
    def __init__(self, strategy, start_date, end_date):
        self.strategy = strategy
        self.data = load_historical_data(start_date, end_date)
        
    def run(self):
        for candle in self.data:
            signal = self.strategy.evaluate(candle)
            if signal:
                self.execute(signal)
                
    def report(self):
        return {
            "total_return": self.returns,
            "sharpe_ratio": self.sharpe,
            "max_drawdown": self.max_dd,
            "win_rate": self.wins / self.total,
            "profit_factor": self.gross_profit / self.gross_loss
        }
```

## Implementation Roadmap

### Phase 1: Foundation (Day 1-2)
- [x] Bot skeleton
- [x] Wallet integration (Phantom, Exodus)
- [x] DEX discovery (DexScreener, Pump.fun)
- [ ] Paper trading loop
- [ ] State persistence

### Phase 2: Intelligence (Day 3-5)
- [ ] Birdeye integration
- [ ] Jupiter swap quotes
- [ ] Whale wallet tracking
- [ ] AI4Trade signal feed

### Phase 3: Strategy (Day 6-10)
- [ ] Micro-cap momentum strategy
- [ ] BTC correlation hedge
- [ ] Cross-DEX arbitrage
- [ ] Backtesting engine

### Phase 4: Risk (Day 11-14)
- [ ] Smart stops
- [ ] Position sizing
- [ ] Drawdown circuit breakers
- [ ] Profit protection (auto-transfer)

### Phase 5: Live (Day 15+)
- [ ] $100 test fund
- [ ] Paper vs live validation
- [ ] Scale to $1K
- [ ] Treasury integration

## Capital Allocation

| Phase | Capital | Purpose |
|-------|---------|---------|
| Test | $100 | Validate execution, fees, slippage |
| Scale 1 | $500 | Test all strategies together |
| Scale 2 | $2,000 | Full deployment |
| Growth | Profits | Reinvest 70%, cash out 30% |

## Success Metrics

- **Daily**: $5-20 profit target (0.5-2% of $1K)
- **Weekly**: $25-100
- **Monthly**: $100-400
- **ROI Target**: 10-40% monthly (crypto is volatile)

## Failure Conditions (Kill Switch)

1. Drawdown > 10% → Pause 24h
2. 3 consecutive losing days → Strategy review
3. Gas fees > 20% of profits → Stop, optimize
4. Any suspicious contract → Immediate blacklist

## Files

| File | Status |
|------|--------|
| `crypto_bot.py` | ✅ Built |
| `CRYPTO_BOT_MAIN.py` | ✅ Built |
| `dex_connector.py` | ✅ Built |
| `wallet_integration.py` | ✅ Built |
| `profit_protection.py` | ✅ Built |
| `telegram_alerts.py` | ✅ Built |
| `.env` | ✅ Created |
| `CRYPTO_BOT_CONFIG.yaml` | ✅ Created |
| `backtester.py` | ✅ Built |
| `TODO.md` | ✅ Created |

## Next Actions

1. **Review** `crypto_bot.py` — verify logic
2. **Test** paper mode for 24h
3. **Fund** wallet with $100 test capital
4. **Validate** all strategies with backtests
5. **Launch** live mode
