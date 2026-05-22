# BTC Hedge Strategy — 15m Chart + Polymarket Sentiment

## Edge
1. **Polymarket predictions** price in crowd sentiment before price moves
2. **15m BTC chart** catches the divergence when market disagrees with prediction
3. **Hedge against micro-cap ruin** — BTC is liquid, no rugs

## Signals

### Long BTC (hedge UP)
- Polymarket "BTC up 1% in 1h" contract < 40% probability (crowd bearish)
- 15m RSI < 35 (oversold)
- 15m price near lower Bollinger Band
- Volume spike on 15m candle

### Short BTC (hedge DOWN)
- Polymarket "BTC up 1% in 1h" contract > 65% probability (crowd bullish)
- 15m RSI > 70 (overbought)
- 15m price near upper Bollinger Band
- Volume spike on 15m candle

## Risk
- Position: 10% of balance max ($8-9 at current)
- Stop: 1.5% on BTC (tight — BTC doesn't move like micro-caps)
- Take profit: 3% (2:1 R:R)
- Time stop: 4 hours if no move

## Execution
- Perp on Drift Protocol (Solana native)
- Or: spot BTC via Jupiter (wrapped BTC on Solana)
- No leverage — pure hedge, not speculation
