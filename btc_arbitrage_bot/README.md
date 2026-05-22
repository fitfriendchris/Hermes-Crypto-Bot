# BTC Discrepancy Arbitrage Bot

## Architecture

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   Spot Exchange │      │   Polymarket     │      │  Secondary      │
│   (Binance WS)  │      │   CLOB API       │      │  (Kalshi/dYdX) │
└────────┬────────┘      └────────┬─────────┘      └────────┬────────┘
         │                        │                         │
         ▼                        ▼                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Candle Aggregator                             │
│              (5m / 15m / 1d + VWAP + Momentum)                       │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Opportunity Engine                                 │
│   • Sigmoid fair-value probability from spot-strike distance         │
│   • Momentum boost (5m/15m ROC)                                      │
│   • Edge = V_fair − Ask − fees                                       │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      Risk Manager                                      │
│   • Max position / daily loss limits                                 │
│   • Circuit breaker on 5% drawdown                                   │
│   • "Get Back" routine: market sell + perp hedge                     │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Execution Stub                                   │
│   • Polymarket CLOB order signing (EIP-712)                          │
│   • Perp hedge routing (Binance/dYdX)                                │
└──────────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
cd ~/Hermes-Crypto-Bot/btc_arbitrage_bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run (testnet mode by default)
python main.py
```

## Modules

| File | Purpose |
|------|---------|
| `main.py` | Event loop orchestrator |
| `config.py` | Environment-based configuration |
| `spot_feed.py` | WebSocket tick ingestion |
| `candle_aggregator.py` | Multi-TF OHLCV + VWAP builder |
| `polymarket_client.py` | CLOB market + order book fetcher |
| `opportunity_engine.py` | Fair value math + signal generation |
| `risk_manager.py` | Position limits + circuit breakers |
| `execution_stub.py` | Order routing scaffold |
| `secondary_venue.py` | Kalshi/dYdX expansion module |

## Key Design Decisions

1. **Asyncio throughout** — All I/O is non-blocking; feeds run concurrently.
2. **Immutable signals** — Once emitted, signals can't be mutated.
3. **Exponential backoff** — Every external API has retry with jitter.
4. **Testnet default** — Won't spend real money unless `ARB_USE_TESTNET=false`.

## The "Get Back" Formula

```
V_fair = sigmoid( (spot − strike) / strike * steepness )
V_fair = momentum_boost(V_fair, 5m_ROC, 15m_ROC)

edge = V_fair − yes_ask − taker_fee − slippage

if edge > 150 bps and confidence > 0.6 → EXECUTE
```
