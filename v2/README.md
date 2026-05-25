# Hermes Solana Bot v2

Complete rebuild based on deep research + code audit. Paper-mode first, live after 48h burn-in.

## Architecture

| Module | File | What it does |
|--------|------|-------------|
| Config | `config_loader.py` | Env-based secrets, mode-aware (paper/live/test) |
| RPC | `rpc_manager.py` | Multi-RPC failover (AllenHark → Helius → public) |
| State | `state_manager.py` | Atomic saves, versioning, backups |
| Swap | `swap_engine.py` | Jupiter v6 + Jito bundles + simulation |
| Risk | `risk_scanner.py` | 8-criteria on-chain rug detection |
| Discovery | `discovery_engine.py` | Birdeye + DexScreener feeds |
| Safety | `safety_circuits.py` | Circuit breakers, halt logic |
| Copy | `copy_engine.py` | Wallet tracking + trade mirroring |
| Tax | `tax_exporter.py` | CSV export for CPA |
| Main | `hermes_v2.py` | Orchestrator |

## Quick Start

```bash
cd ~/Hermes-Crypto-Bot/v2

# 1. Create venv
python3 -m venv venv
source venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Copy env template
cp .env.example .env
# Edit .env with real values (wallet key, RPC keys, Birdeye key)

# 4. Run paper mode
BOT_MODE=paper python src/hermes_v2.py
```

## Status

- **Lines:** 2,901 (10 modules)
- **Mode:** Paper-only until 48h burn-in passes
- **Capital:** $91
- **Kill criteria:** Hard stops at $15 loss, 3 rugs/week, 20% drawdown

## Files

```
v2/
├── .env.example          # Configuration template
├── requirements.txt      # Dependencies
├── README.md             # This file
├── src/
│   ├── config_loader.py
│   ├── rpc_manager.py
│   ├── state_manager.py
│   ├── swap_engine.py
│   ├── risk_scanner.py
│   ├── discovery_engine.py
│   ├── safety_circuits.py
│   ├── copy_engine.py
│   ├── tax_exporter.py
│   └── hermes_v2.py      # Main runner
├── config/               # Mode-specific YAML configs
├── state/                # Runtime state + backups
├── logs/                 # Log files
└── tests/                # Unit tests (TODO)
```

## Research Sources

- Solana DEX landscape: Jupiter Ultra, Raydium, Orca, Meteora, Phoenix
- MEV protection: Jito bundles + dontfront ($370-500M drained annually)
- Copy-trading: Stratium 26,704-trade analysis (59% win rate, +18% avg)
- Cost per $50 trade: $0.20 (low congestion) → $2.45 (high congestion)
- Code audit: Existing bot scored 4/10 — P0 gaps fixed in v2

## TODO

- [ ] Unit tests for all modules
- [ ] Birdeye API key integration
- [ ] Wallet monitoring (on-chain subscription)
- [ ] Telegram dashboard v2
- [ ] Launchd plist for auto-start
- [ ] 48h paper-mode burn-in
- [ ] Live deployment with $2-5 positions

Built 2026-05-24 by Hermes + Sovereign Managers.
