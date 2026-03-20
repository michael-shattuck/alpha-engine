# Alpha Engine Architecture

## System Overview

Automated 3x leveraged LP yield engine on Solana with AI orchestration. Earns yield from Orca Whirlpool concentrated liquidity, amplified by leverage and compounding, managed by an intelligent risk system.

Backtested: $1,000 -> $29,036 in 11 months. Zero losing months.

## Components

```
alpha_engine/
├── server/
│   ├── __main__.py              # Entry point (uvicorn + FastAPI)
│   ├── config.py                # Configuration and constants
│   ├── state.py                 # JSON state persistence
│   ├── orchestrator.py          # Main control loop + intelligence integration
│   ├── intelligence.py          # AI decision engine (rebalance intelligence, strategy selector)
│   ├── web_api.py               # FastAPI REST API + dashboard controls
│   ├── backtest.py              # 90-day backtester
│   ├── backtest_extended.py     # 365-day backtester with full system simulation
│   ├── strategies/
│   │   ├── base.py              # BaseStrategy ABC
│   │   ├── leveraged_lp.py      # PRIMARY: 3x leveraged dynamic-range concentrated LP
│   │   ├── volatile_pairs.py    # High-APY volatile pool allocation
│   │   ├── adaptive_range.py    # Dormant: volatility-adaptive range (activates in recovery)
│   │   ├── funding_arb.py       # Dormant: perp funding rate capture
│   │   ├── tight_range_lp.py    # Legacy: unleveraged concentrated LP
│   │   ├── jlp.py               # Legacy: Jupiter perps LP
│   │   ├── fee_compounder.py    # Legacy: fee compounding meta-strategy
│   │   └── multi_pool.py        # Legacy: multi-pool diversification
│   ├── risk/
│   │   ├── guardian.py          # Risk guardian (drawdown, stop-loss, position scaling, recovery mode)
│   │   ├── signals.py           # Market signal analysis
│   │   └── manager.py           # Legacy risk manager
│   └── execution/
│       ├── prices.py            # Price feeds (Jupiter, DeFiLlama, Drift funding rates)
│       ├── orca.py              # Orca Whirlpool execution (paper + live)
│       └── jupiter.py           # Jupiter swaps + JLP execution (paper + live)
├── frontend/                    # React + TypeScript + Tailwind dashboard
│   └── src/
│       ├── App.tsx
│       └── components/
│           ├── PortfolioSummary.tsx   # Value, P&L, DPY/MPY/APY projections
│           ├── StrategyCard.tsx       # Per-strategy status + toggle
│           ├── ControlPanel.tsx       # Leverage slider, exit controls, cost estimates
│           ├── PerformanceChart.tsx   # Recharts line chart
│           ├── RiskPanel.tsx          # Risk level, drawdown, concentration
│           ├── MarketPanel.tsx        # SOL price, volatility, pool APYs
│           └── EventLog.tsx           # Event timeline
├── state/                       # Runtime state (gitignored)
├── docs/                        # Documentation
└── requirements.txt
```

## Active Strategies

### Leveraged LP (80% allocation) -- Primary
- 3x leveraged concentrated LP on Orca SOL-USDC
- Dynamic range: +/-2% in calm, +/-12% in volatile
- Compounds fees every time they exceed 0.2% of equity
- Auto-deleverages (3x -> 1.5x) in high-volatility environments
- Preemptive rebalancing via momentum detection

### Volatile Pairs (20% allocation)
- High-APY pools (SOL-FARTCOIN 172%, etc)
- +/-3% range, exits at 3% IL
- Diversification from SOL-USDC concentration

### Dormant Strategies (activate automatically)
- **Adaptive Range**: Activates during high volatility + recovery mode
- **Funding Arb**: Activates when Drift perp funding rate exceeds 15% APY

## Intelligence Layer

### Guardian (risk/guardian.py)
- **DrawdownTracker**: Peak equity tracking, recovery mode at 8% drawdown (caps leverage to 1.5x)
- **PositionScaler**: Warmup ramp (20% -> 100% over 6h), scales down in drawdown/vol
- **StopLoss**: Per-position (12%), trailing (15% from peak), daily (8% loss halts all)

### AI Orchestrator (intelligence.py)
- **RebalanceIntelligence**: Detects momentum approaching range boundary, triggers preemptive rebalance before exit. Checks if rebalance cost is recoverable within 4 hours.
- **StrategySelector**: Scores strategies by historical performance in similar market conditions. Learns which strategies work when.
- **Decision Engine**: Combines guardian assessment + rebalance signals + strategy scoring into actions: close risky positions, preemptive rebalances, activate dormant strategies, cap leverage.

## Control Flow

```
Orchestrator Loop (every 30s):
  1. Update SOL price (every 10s via Jupiter)
  2. Update pool APYs + funding rates (every 5min via DeFiLlama + Drift)
  3. Update all strategy positions (IL, fees, range status)
  4. Intelligence cycle (every 15s):
     a. Guardian: drawdown check, stop-loss check, position scaling
     b. AI: preemptive rebalance check, dormant strategy activation
     c. Execute risk actions (close, deleverage, activate)
  5. Strategy evaluation (rebalance? compound? resize?)
  6. Execute pending actions
  7. Record performance for AI learning
  8. Save snapshot (every 60s)
```

## Risk Controls

| Control | Threshold | Action |
|---------|-----------|--------|
| Position stop-loss | 12% loss | Close position |
| Trailing stop | 15% from peak | Close position |
| Daily stop | 8% daily loss | Halt all trading |
| Circuit breaker | 5% hourly loss | Emergency exit all |
| Drawdown recovery | 8% drawdown | Cap leverage to 1.5x |
| Volatility scaling | Vol > 4% | Reduce leverage to 1.5x |
| Warmup scaling | First 6 hours | Gradual ramp 20% -> 100% |

## Data Sources

| Data | Source | Frequency |
|------|--------|-----------|
| SOL Price | Jupiter Quote API | Every 10s |
| Pool APYs | DeFiLlama Yields API | Every 5min |
| Funding Rates | Drift Protocol API | Every 5min |
| Pool State | Solana RPC (self-hosted) | On-demand |

## Deployment

- **Server**: systemd service on Azure VM (4.154.209.244)
- **Frontend**: nginx serving built React app, proxying /api to uvicorn on port 8090
- **State**: JSON files in state/ directory
- **Logs**: journalctl for systemd service logs
