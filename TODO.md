# Crypto Bot Project TODO
# Keep this updated as you ship features. Check items with git commits.
# Created: 2026-05-11 by Hermes

# ── DONE ──
[x] Build unified runner (crypto_bot.py) — paper trading loop, discovery, monitoring
[x] Integrate DEX discovery (DexScreener, Pump.fun, Birdeye, Jupiter)
[x] Smart stop losses (fixed %, volatility-adjusted, breakeven trail at 2R)
[x] Tiered take profits (25% at 2R, 5R, 10R, 50R + trailing stop)
[x] Position sizing with risk limits and safety circuits
[x] State persistence (bot_state.json survives restarts)
[x] Telegram alerts (entries, exits, daily report)
[x] Profit protection module (cold storage auto-transfer)
[x] Wallet integration skeleton (Phantom, MetaMask)
[x] Add Exodus wallet support (50+ chains, priority wallet)
[x] Safety circuits (max drawdown 25%, daily loss -$25, 5-loss cooldown, max 8 positions)

# ── IN PROGRESS ──
[ ] Test paper trading loop end-to-end (run for 1+ hours, verify state saves)

# ── NEXT UP ──
[ ] Wire live DEX execution via Jupiter API on Solana
[ ] Implement real whale wallet tracking (Solscan, Etherscan, BscScan APIs)
[ ] Full on-chain risk filter (honeypot.is, mint function, LP lock check)
[ ] Add Birdeye API key tier for faster rate limits
[ ] Build simple HTML dashboard (equity curve, open positions, trade history)
[ ] Add momentum stop (exit if volume drops 50%)
[ ] Add dev-wallet sell detector (exit if dev sells >5%)
[ ] Integrate Arkham Intelligence for smart money labels
[ ] Run backtest on historical DEX data
[ ] Dockerize for VPS deployment

# ── LOW PRIORITY / NICE TO HAVE ──
[ ] Seed phrase BIP39 derivation for Exodus (currently private-key only)
[ ] Multi-wallet split (use Exodus for SOL, Phantom for backup)
[ ] Cross-chain bridge support (Solana ↔ Ethereum via Wormhole)
[ ] Add CoinGecko API for additional price feeds
[ ] Implement TWAP orders for large positions
[ ] Mobile push notifications via Pushover/Signal
[ ] Auto-compound logic (reinvest profits vs sweep to cold wallet)
[ ] Social signal integration (Twitter/X viral detection via API)
[ ] CEX connector (Binance spot for BTC/ETH majors)

# ── BLOCKED ──
[ ] Live SOL swaps via Jupiter — blocked: need real RPC + wallet integration
[ ] Real whale tracking — blocked: need API keys (Etherscan free tier has rate limits)

# ── RELEASE CHECKLIST ──
[ ] Run 48h paper trading without crashes
[ ] Verify state save/load across restarts
[ ] Verify Telegram alerts fire on all events
[ ] Verify safety circuits trigger correctly
[ ] Review all TODO comments in code
[ ] Clean log files before live testing
[ ] Set up remote server (VPS or cloud VM)
