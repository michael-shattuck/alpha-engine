# Alpha Engine Architecture

## System Overview

Alpha Engine is an automated DeFi yield system running 5 coordinated strategies on Solana, managed by an orchestrator with risk controls, and monitored via a web dashboard.

Target: 10-25% monthly returns through diversified yield strategies.

## Components

```
alpha_engine/
├── server/
│   ├── __main__.py           # Entry point (uvicorn + FastAPI)
│   ├── config.py             # All configuration and constants
│   ├── state.py              # JSON state persistence
│   ├── orchestrator.py       # Main control loop
│   ├── web_api.py            # FastAPI REST API
│   ├── strategies/
│   │   ├── base.py           # BaseStrategy ABC
│   │   ├── tight_range_lp.py # Strategy 1: Tight range concentrated LP
│   │   ├── jlp.py            # Strategy 2: Jupiter Perps LP
│   │   ├── fee_compounder.py # Strategy 3: Auto-compound fees
│   │   ├── multi_pool.py     # Strategy 4: Multi-pool diversification
│   │   └── volatile_pairs.py # Strategy 5: High-APY volatile pairs
│   ├── risk/
│   │   ├── signals.py        # Market signal analysis
│   │   └── manager.py        # Risk limits, circuit breakers
│   └── execution/
│       ├── prices.py         # Price feeds (Jupiter, DeFiLlama)
│       ├── orca.py           # Orca Whirlpool execution
│       └── jupiter.py        # Jupiter swaps + JLP
├── frontend/                 # React dashboard
│   ├── src/
│   │   ├── App.tsx
│   │   └── components/
├── state/                    # Runtime state (gitignored)
│   ├── portfolio.json
│   ├── history.json
│   └── events.json
└── docs/
    ├── ARCHITECTURE.md       # This file
    ├── IRIS_OPERATIONS.md    # Iris management guide
    └── API.md                # REST API reference
```

## Strategy Details

### 1. Tight Range LP (35% allocation)
- Opens concentrated liquidity on Orca SOL-USDC with +/-2.5% range
- 5x more capital efficient than standard +/-10% range
- Auto-rebalances when price exits range (close, re-center, reopen)
- Expected: 150-200% APY when actively managed

### 2. Jupiter Perps LP (25% allocation)
- Deposits into Jupiter's perpetual exchange as the counterparty
- Earns from trader fees + trader losses (house edge)
- Simpler than LP -- just deposit and earn
- Expected: 40-100% APY, varies with trading activity

### 3. Fee Compounder (0% direct allocation)
- Meta-strategy: collects fees from all other strategies
- Reinvests into existing positions every 4 hours
- Turns 65% APY into 90%+ effective APY through compounding
- No capital of its own

### 4. Multi-Pool (25% allocation)
- Spreads capital across top 3-5 pools by risk-adjusted APY
- Wider +/-5% ranges (less rebalancing)
- DeFiLlama data drives pool selection
- Rebalances allocation when APY drifts >10%

### 5. Volatile Pairs (15% allocation)
- Targets pools with APY > 100% and TVL > $500k
- Tighter risk management (exit at 3% IL)
- Higher reward compensates for higher risk
- Expected: 200-400% APY on winners

## Control Flow

```
Orchestrator Loop (every 30s):
  1. Update prices (every 10s)
  2. Update all strategy positions (IL, fees, range status)
  3. Check risk (every 15s)
     - Market signals (trend, volatility, volume)
     - Drawdown check
     - Circuit breaker check
     - Concentration check
  4. Evaluate strategies (rebalance? open? close?)
  5. Execute pending actions
  6. Compound fees (every 4h)
  7. Save snapshot (every 60s)
```

## Risk Controls

| Control | Threshold | Action |
|---------|-----------|--------|
| Max Drawdown | 10% | Scale down all strategies |
| Circuit Breaker | 5% loss in 1 hour | Emergency exit all positions |
| SOL Crash | -15% in 24h | Pause volatile strategies |
| Concentration | >50% in one strategy | Rebalance allocation |
| Volatility | Extreme | Widen ranges, reduce size |

## Modes

- **paper**: All strategies simulate using real market data. No transactions sent.
- **live**: Real on-chain execution via Orca + Jupiter + Jito.

Always paper trade first. Switch to live only after validation.

## Data Sources

| Data | Source | Frequency |
|------|--------|-----------|
| SOL Price | Jupiter Quote API | Every 10s |
| Pool APYs | DeFiLlama Yields API | Every 5min |
| Pool State | Solana RPC (self-hosted) | On-demand |
| JLP Price | Jupiter Price API | Every 10s |

## Deployment

- **Server**: systemd service on Azure VM (4.154.209.244)
- **Frontend**: nginx serving built React app, proxying /api to uvicorn
- **State**: JSON files in state/ directory
- **Logs**: journalctl for systemd service logs
