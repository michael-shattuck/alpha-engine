# Alpha Engine Roadmap

## Current State (v3 -- March 2026)

Single-pool 3x leveraged concentrated LP on Orca SOL-USDC with dynamic range, compounding, and AI orchestration. Paper trading on Azure VM.

Backtested: $1,000 -> $29,036 in 11 months. Zero losing months. 3.4% max drawdown.

## Phase 1: Validate (Now)

**Goal:** Prove the system works in real-time before deploying real capital.

- [x] Paper trading deployed on Azure VM
- [x] Dashboard with controls at http://4.154.209.244:8090
- [x] Iris monitoring subagent active
- [ ] 48-72 hours of clean paper trading
- [ ] Verify projected returns match backtest expectations
- [ ] Verify guardian protections trigger correctly (stop-loss, deleverage, recovery)
- [ ] Verify rebalancing behavior during price movements

## Phase 2: Go Live -- Single Pool ($1k-$10k)

**Goal:** Deploy real capital on the proven single-pool strategy.

- [ ] Wire up live execution (Orca open/close/collect, Jupiter swaps, Jito bundles)
- [ ] Integrate lending protocol (Kamino or MarginFi) for real leverage
- [ ] Start with $100-500 real capital at 2x leverage
- [ ] Scale to $1k-$10k at 3x after 2 weeks of live validation
- [ ] Automate fee collection and reinvestment on-chain

**Scaling limits:** Up to ~$100k on single Orca SOL-USDC pool with negligible APY impact.

## Phase 3: Multi-Pool on Solana ($10k-$1M)

**Goal:** Scale capital across multiple pools to avoid single-pool concentration.

Architecture change: register multiple `LeveragedLPStrategy` instances with different pool addresses.

Target pools:
| Pool | DEX | Est. TVL | Est. APY |
|------|-----|----------|----------|
| SOL-USDC (0.04%) | Orca | $30M | 65-80% |
| SOL-USDC (0.01%) | Orca | $15M | 40-60% |
| SOL-ETH | Orca | $5M | 50-90% |
| SOL-mSOL | Orca | $8M | 30-50% |
| SOL-JitoSOL | Orca | $4M | 30-50% |

Capital allocation across 5 pools: $200k each = $1M total with <1% pool share on each.

- [ ] Add pool registry config
- [ ] Spawn per-pool strategy instances
- [ ] Cross-pool capital allocation in orchestrator
- [ ] Per-pool risk monitoring in guardian
- [ ] Dashboard pool selector

## Phase 4: Multi-DEX on Solana ($1M-$10M)

**Goal:** Expand to Raydium and Meteora concentrated liquidity for more capacity.

| DEX | Pool Type | Execution Adapter |
|-----|-----------|-------------------|
| Orca Whirlpools | Concentrated (tick-based) | Already built |
| Raydium CLMM | Concentrated (tick-based) | New adapter needed |
| Meteora DLMM | Dynamic concentrated | New adapter needed |

Same core logic (leverage + dynamic range + compound) with DEX-specific execution layers.

- [ ] Raydium CLMM execution adapter
- [ ] Meteora DLMM execution adapter
- [ ] Unified pool discovery across DEXes
- [ ] Cross-DEX arbitrage (same pair, different prices = free money)
- [ ] DEX-specific fee optimization

**Capacity:** ~$3M per major pool across 3 DEXes = $10M+ total.

## Phase 5: Multi-Chain ($10M+)

**Goal:** Deploy the same strategy on EVM chains with concentrated liquidity DEXes.

| Chain | DEX | Pool Type | RPC |
|-------|-----|-----------|-----|
| Solana | Orca/Raydium/Meteora | Whirlpool/CLMM/DLMM | Self-hosted (20.120.229.168) |
| Arbitrum | Uniswap v3 / Camelot | Concentrated | Self-hosted (13.91.71.124:8547) |
| Ethereum | Uniswap v3 | Concentrated | Self-hosted Erigon (13.91.71.124:8545) |
| Base | Aerodrome | Concentrated | Need to deploy node |

The core strategy is identical -- concentrated LP with leverage, dynamic range, compounding. Only the execution layer changes per chain.

- [ ] EVM execution adapter (ethers.js / web3.py)
- [ ] Uniswap v3 position management
- [ ] Chain-specific lending (Aave on Arbitrum/Ethereum)
- [ ] Cross-chain capital allocation
- [ ] Chain-specific risk parameters

**Capacity:** Effectively unlimited. Uniswap v3 ETH-USDC has $500M+ TVL.

## Phase 6: Fund Structure ($10M+)

**Goal:** Formalize as a fund for external capital.

- [ ] Legal structure (LP/GP, offshore if needed)
- [ ] Audited track record from live trading
- [ ] Multi-wallet support (already architected)
- [ ] Investor dashboard (read-only view of performance)
- [ ] Automated distributions
- [ ] Risk reporting and compliance

## Vertical Scaling Reference

How much capital each pool can absorb before returns degrade:

| Your Capital | Pool Share (on $30M TVL) | APY Impact | Expected Monthly |
|-------------|------------------------|------------|-----------------|
| $1,000 | 0.01% | None | ~35% |
| $10,000 | 0.1% | None | ~35% |
| $100,000 | 1% | Negligible | ~33% |
| $500,000 | 5% | Slight (-15%) | ~28% |
| $1,000,000 | 10% | Noticeable (-35%) | ~22% |
| $5,000,000 | 50% | Significant (-65%) | ~12% |

## Horizontal Scaling Reference

| Phase | Capital Range | Pools | Chains | Effort |
|-------|-------------|-------|--------|--------|
| 1-2 | $1k-$100k | 1 | 1 | Done |
| 3 | $100k-$1M | 3-5 | 1 | Low (config changes) |
| 4 | $1M-$10M | 10-15 | 1 | Medium (new adapters) |
| 5 | $10M+ | 20+ | 3-4 | High (multi-chain infra) |
| 6 | $10M+ | 20+ | 3-4 | Non-technical (legal/ops) |

## Revenue Projections

Assuming 25% average monthly return (conservative based on backtest):

| Starting Capital | 6 Months | 12 Months | 24 Months |
|-----------------|----------|-----------|-----------|
| $1,000 | $4,000 | $14,500 | $211,000 |
| $5,000 | $19,000 | $73,000 | $1,050,000 |
| $10,000 | $38,000 | $146,000 | $2,100,000 |
| $50,000 | $190,000 | $730,000 | $10,500,000 |

These assume compounding and no withdrawals. Real returns will vary. Scale-induced APY dilution kicks in at higher capital levels requiring horizontal scaling to maintain returns.
