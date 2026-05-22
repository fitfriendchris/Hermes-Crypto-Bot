# Live Promotion Gate

Before flipping `LIVE_MODE=true`, the upgraded bot must clear a 14-day paper soak
and meet all promotion criteria. Skipping the gate has wiped previous bot iterations;
treat it as load-bearing.

## Required Soak

- **Duration:** 14 calendar days minimum from the date of the survival-config restart.
- **Mode:** `LIVE_MODE=false` (paper). Bot already runs under `com.hermes.crypto-bot` launchd plist.
- **Capital:** Starting paper balance = $100 (matches the live target). Do not increase to "show better numbers" — small-account economics are the point.

## Promotion Criteria (all must hold over the soak window)

| Metric | Threshold | Rationale |
|---|---|---|
| Closed trades | ≥ 100 | Statistical minimum for PF to be meaningful |
| Profit factor (net) | > 1.30 | Below this, expected value is negative after costs |
| Max drawdown | < 8% | Survival sizing target; anything worse means signals are degrading |
| Honeypot losses | 0 | Jupiter sell-sim should catch all of them |
| Creator-flag bypasses | 0 | Hard veto must hold |
| Circuit-breaker trips | ≤ 1 | More than 1/14d means thresholds are too tight or strategy is bleeding |
| Telegram alerts firing | yes | If alerts are silent the bot likely halted; investigate before promoting |

## Promotion Procedure

If all criteria hold:

1. Stop the bot: `launchctl unload ~/Library/LaunchAgents/com.hermes.crypto-bot.plist`
2. Confirm wallet has $100 starting capital (USDC + a small SOL buffer for gas).
3. Set `LIVE_MODE=true` in `.env`.
4. Verify wallet keys are in `.env`: `PHANTOM_PRIVATE_KEY` (or equivalent).
5. Restart: `launchctl load ~/Library/LaunchAgents/com.hermes.crypto-bot.plist`.
6. Watch the first 24 hours closely. If circuit breaker fires, revert to paper immediately.

## Cap on Live Bankroll

- **$100 hard cap for first 30 days of live.**
- Raise to $250 only after PF > 1.4 over 30 days of live trading.
- Raise to $500 only after PF > 1.4 over 60 days. Hard ceiling: $500 until further analysis.
- Profit sweeper continues to send 50% of realized profits to cold storage (`HERMES_profit_sweeper.py`).

## Sleeve-Enable Order

Don't turn everything on at once. Enable sleeves one at a time so each can be evaluated independently:

| Day | Action |
|---|---|
| 0 | Survival sizing only (existing momentum scanner + new sizing). Sleeves A & B off. |
| 0-14 | Paper soak. Watch metrics. |
| 14 | If criteria met, flip live but keep dex_arb / scalp / copy_trader off. |
| 14-44 | 30 days live with momentum-survival only. |
| 44 | If PF > 1.4 live, enable scalp_meanrev sleeve. Keep dex_arb / copy off. |
| 44-74 | 30 days live with momentum + scalp. |
| 74 | If PF > 1.4 still holds, enable dex_arb sleeve. Then 30 days more. |
| 104 | Last sleeve: enable copy_trader (after whale_discovery scoreboard has been populated for ≥14 days and you've manually reviewed the wallets it qualified). |

## Continuous Shadow

After going live, **keep a paper-mode shadow running on a second machine or process** under a different state path. This is the cheapest way to detect strategy decay — if the live bot underperforms the paper shadow by >5% over 30 days, something is leaking through to live (execution slippage, MEV, etc.) and you investigate before adding capital.

## When to Roll Back to Paper

Any of these triggers an immediate revert to `LIVE_MODE=false`:

- Daily circuit-breaker fires twice in a 7-day window.
- One trade loses >5% of bankroll (size or stop is broken).
- Wallet balance disagrees with bot's `state/HERMES_CRYPTO_STATE.json` by >2%.
- Any "honeypot_no_sell_route" flag bypass in the live log.
- Any whale wallet on the scoreboard is found to have <90 days verified history (whale_discovery bug).
