# Alpha Engine API Reference

Base URL: `http://localhost:8090` (local) or `http://4.154.209.244:8090` (deployed)

## Status & Monitoring

### GET /api/status
Overall portfolio status with projections.

Response:
```json
{
  "mode": "paper",
  "capital": 1000.0,
  "total_value": 1050.00,
  "total_pnl": 50.00,
  "total_pnl_percent": 5.0,
  "total_fees": 45.00,
  "projected_dpy": 1.77,
  "projected_mpy": 53.7,
  "projected_apy": 644,
  "risk_level": "low",
  "circuit_breaker_active": false,
  "sol_price": 89.50,
  "strategies": { ... },
  "uptime_hours": 24.5,
  "last_update": 1710864000.0,
  "guardian": { ... },
  "ai_reasoning": ["Position scale: 100%", "..."]
}
```

### GET /api/strategies
All strategy states (object keyed by strategy ID).

### GET /api/strategies/{id}
Single strategy state.

### GET /api/history?limit=1000
P&L history snapshots (one per minute).

### GET /api/events?limit=200
Event log (open, close, rebalance, compound, emergency_halt, preemptive_rebalance, etc).

### GET /api/market
Current market data (SOL price, volatility, pool APYs, funding rate).

### GET /api/pools
Top pools by APY.

### GET /api/intelligence
AI decision engine state.

Response:
```json
{
  "reasoning": ["..."],
  "recent_decisions": [{"timestamp": ..., "actions": [...], "reasoning": [...]}],
  "guardian": {"risk_level": "low", "drawdown_pct": 0.5, "recovery_mode": false, ...},
  "rebalance_frequency_24h": 2.5,
  "rebalance_cost_24h": 1.50
}
```

## Controls

### POST /api/strategies/{id}/toggle
Enable or disable a strategy.

Body: `{"enabled": false}`

### POST /api/config/allocation
Update capital allocation.

Body: `{"allocations": {"leveraged_lp": 0.80, "volatile_pairs": 0.20}}`

### POST /api/config/leverage
Set leverage (1.0 - 5.0). Applied to all leveraged strategies.

Body: `{"leverage": 3.0}`

### POST /api/emergency-exit
Close ALL positions across ALL strategies. Two-click confirmation recommended on dashboard.

### POST /api/emergency-exit/{strategy_id}
Close all positions for a specific strategy and disable it.

### GET /api/exit-cost
Estimate cost of exiting all positions.

Response:
```json
{
  "total_deployed": 2400.00,
  "total_borrowed": 1600.00,
  "estimated_swap_slippage": 2.40,
  "estimated_tx_fees": 0.01,
  "estimated_total_cost": 2.41,
  "cost_percent": 0.100
}
```

## Event Types

| Type | Description |
|------|-------------|
| open | Position opened |
| close | Position closed |
| rebalance | Price exited range, repositioned |
| preemptive_rebalance | AI detected momentum, repositioned before exit |
| compound | Fees reinvested into position |
| deleverage | Reduced leverage due to risk |
| emergency_halt | All positions closed (circuit breaker or manual) |
| emergency_exit | Manual exit via API/dashboard |
| activate | Dormant strategy activated by AI |
| enable / disable | Strategy toggled |
| leverage_change | Leverage adjusted via API |
| reallocation | Capital allocation changed |
| risk_close | Position closed by guardian (stop-loss/trailing stop) |
