# Alpha Engine

Automated leveraged LP yield engine on Solana. 3x leveraged concentrated liquidity with dynamic range sizing, aggressive compounding, and AI-driven risk management.

Backtested: **$1,000 -> $29,036 in 11 months** (340-day backtest, SOL -32.4%, zero losing months).

## How It Works

1. Opens concentrated LP positions on Orca SOL-USDC
2. Borrows USDC to lever up 3x (net of 12% APY borrow cost)
3. Dynamically tightens range in calm markets (+/-2%) for max fee capture
4. Widens range in volatile markets (+/-12%) to avoid rebalance bleed
5. Compounds fees back into position every few hours
6. AI orchestrator manages preemptive rebalancing, drawdown protection, and strategy activation

## Strategies

| Strategy | Allocation | Role | Status |
|----------|-----------|------|--------|
| Leveraged LP | 80% | 3x leveraged concentrated LP, dynamic range, compounding | Active |
| Volatile Pairs | 20% | High-APY pools (100%+ APY) | Active |
| Adaptive Range | 0% | Volatility-adaptive range management | Dormant (activates in high vol + recovery) |
| Funding Arb | 0% | Perp funding rate capture | Dormant (activates when funding positive) |

## Quick Start

```bash
cd alpha_engine
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..

# Paper trade (simulated, real market data)
python3 -m server --capital 1000 --mode paper

# Dashboard at http://localhost:8090
```

## Dashboard

- Portfolio value, P&L, projected DPY/MPY/APY
- Per-strategy cards with toggle controls
- Leverage slider (1x-5x)
- Exit cost estimation
- Emergency exit (per-strategy or all)
- Performance chart, risk panel, market data, event log
- AI reasoning display

## Backtesting

```bash
# 90-day backtest comparing leverage levels
python3 -m server.backtest --days 90 --capital 1000 --compare

# 365-day extended backtest
python3 -m server.backtest_extended --days 365 --capital 1000 --compare
```

## Live Trading

```bash
# Requires WALLET_PRIVATE_KEY in .env
python3 -m server --capital 1000 --mode live
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [STRATEGY.md](docs/STRATEGY.md) | Strategy design, backtest results, risk analysis |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture, components, control flow |
| [IRIS_OPERATIONS.md](docs/IRIS_OPERATIONS.md) | AI agent management guide |
| [API.md](docs/API.md) | REST API reference |
