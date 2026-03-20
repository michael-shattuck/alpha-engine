# Alpha Engine

Automated DeFi yield engine on Solana. Runs 5 coordinated strategies targeting 10-25% monthly returns.

## Strategies

| # | Strategy | Mechanism | Target APY |
|---|----------|-----------|------------|
| 1 | Tight Range LP | Concentrated LP on Orca, +/-2.5% range, auto-rebalance | 150-200% |
| 2 | Jupiter Perps LP | Counterparty to perp traders on Jupiter | 40-100% |
| 3 | Fee Compounder | Auto-compound fees from all strategies | +20% boost |
| 4 | Multi-Pool | Diversified LP across top pools by APY | 60-120% |
| 5 | Volatile Pairs | High-APY pools with tight risk management | 200-400% |

## Quick Start

```bash
cd alpha_engine

# Install backend deps
pip install -r requirements.txt

# Install frontend deps
cd frontend && npm install && npm run build && cd ..

# Run in paper mode (simulated, no real money)
python3 -m server --capital 100 --mode paper

# Dashboard at http://localhost:8090
```

## Paper Trading

Paper mode uses real market data (real SOL prices, real pool APYs) but simulates all transactions. Validate the system before going live.

```bash
# Paper trade with $1000
python3 -m server --capital 1000 --mode paper
```

## Live Trading

```bash
# Requires WALLET_PRIVATE_KEY in .env
python3 -m server --capital 1000 --mode live
```

## API

Dashboard runs at http://localhost:8090

See [docs/API.md](docs/API.md) for full API reference.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design.

## Iris Management

See [docs/IRIS_OPERATIONS.md](docs/IRIS_OPERATIONS.md) for AI agent operations guide.
