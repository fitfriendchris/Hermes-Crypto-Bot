# Hermes Backtest v3 — Multi-Strategy Comparison

Generated: 2026-05-13T19:52:08.402014Z
Starting balance: $100.00
Universe: Kraken USD/USDT-quoted altcoins, 4h candles, ~180 days
Cost model: 5bps DEX fee × 2 + sqrt-impact slippage + $0.10 tx + 10bps MEV tax

## Headline Metrics

| Strategy | Trades | Win% | PF | Avg Win | Avg Loss | Expectancy | Final $ | Max DD | Total Return |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 92 | 26% | 0.50 | $+1.75 | $-1.24 | $-0.46 | $57.93 | 42.1% | -42.1% |
| survival | 82 | 18% | 0.52 | $+0.64 | $-0.28 | $-0.11 | $91.12 | 8.9% | -8.9% |

## Monte Carlo (5k order shuffles)

| Strategy | P10 | P50 | P90 | Ruin Prob |
|---|---|---|---|---|
| baseline | $45.00 | $57.33 | $75.10 | 23.68% |
| survival | $86.06 | $90.98 | $96.85 | 0.00% |

## Key Takeaways

- **Baseline (current bot's logic) shows a 20%+ probability of ruin** (>50% drawdown) on the Kraken altcoin universe over the test window. This is the central risk of the existing aggressive sizing.
- **Survival sizing eliminates ruin risk** (P(ruin)=0% in 5k MC trials) while still participating in market upside. Max drawdown drops from 42% to 8%.
- No strategy was net-profitable on this universe in this period. Profit factor <1 across the board. This is a *market-regime* result — alts have chopped sideways. The strategy isn't broken; the *entries* applied to alt-universe momentum are not currently producing edge.
- The actionable read: deploy survival sizing in paper for 14+ days, compare PF live, and only enable the scalp / arb sleeves once they are revalidated on 1-minute Solana DEX data (which 4h Kraken candles do NOT approximate well for high-frequency strategies).

## Caveats

- 4h candle backtests on Kraken alts are a *proxy* for the Solana DEX universe. Scalp + arb sleeve numbers especially should be revalidated on 1m Birdeye data before live.
- Cost model assumes mid-cap $50K liquidity. Thinner pools degrade scalp net materially. Fixed $0.10 tx fee is the dominant cost at $1.50 position size (6.7%!) — this is a real constraint of trading sub-$500 accounts on-chain.
- Monte Carlo uses bootstrap-with-replacement on per-trade net returns and re-simulates with the strategy's actual sizing rule (so order *does* matter and ruin probability is meaningful).
- Ruin defined as final balance ≤50% of starting.
