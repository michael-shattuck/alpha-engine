# Alpha Engine API Reference

Base URL: `http://localhost:8090` (local) or `http://4.154.209.244:8090` (deployed)

## Endpoints

### GET /api/status
Overall portfolio status.

Response:
```json
{
  "mode": "paper",
  "capital": 100.0,
  "total_value": 102.50,
  "total_pnl": 2.50,
  "total_pnl_percent": 2.5,
  "risk_level": "low",
  "circuit_breaker_active": false,
  "sol_price": 89.50,
  "strategies": { ... },
  "uptime_hours": 24.5,
  "last_update": 1710864000.0
}
```

### GET /api/strategies
All strategy states.

Response:
```json
{
  "tight_range_lp": {
    "id": "tight_range_lp",
    "name": "Tight Range LP",
    "enabled": true,
    "mode": "paper",
    "capital_allocated": 35.0,
    "current_value": 36.20,
    "total_fees": 1.50,
    "total_pnl": 1.20,
    "total_pnl_percent": 3.43,
    "position_count": 1,
    "positions": [...],
    "status": "active",
    "metrics": { ... }
  },
  ...
}
```

### GET /api/strategies/{id}
Single strategy state.

### POST /api/strategies/{id}/toggle
Enable or disable a strategy.

Body:
```json
{ "enabled": false }
```

### POST /api/config/allocation
Update capital allocation across strategies.

Body:
```json
{
  "allocations": {
    "tight_range_lp": 0.40,
    "jlp": 0.20,
    "multi_pool": 0.25,
    "volatile_pairs": 0.15
  }
}
```

### GET /api/history?limit=1000
P&L history snapshots (one per minute).

Response: Array of:
```json
{
  "timestamp": 1710864000.0,
  "total_value": 102.50,
  "total_pnl": 2.50,
  "total_pnl_percent": 2.5,
  "strategy_values": {
    "tight_range_lp": 36.20,
    "jlp": 25.80,
    ...
  },
  "risk_level": "low",
  "sol_price": 89.50
}
```

### GET /api/events?limit=200
Recent events.

Response: Array of:
```json
{
  "timestamp": 1710864000.0,
  "type": "rebalance",
  "strategy": "tight_range_lp",
  "data": { ... }
}
```

Event types: open, close, rebalance, compound, enable, disable, risk_scale_down, risk_pause, circuit_breaker, emergency_close, reallocation

### GET /api/market
Current market data.

Response:
```json
{
  "sol_price": 89.50,
  "sol_change_1h": -0.5,
  "sol_change_24h": -2.3,
  "volatility_1h": 0.012,
  "volatility_24h": 0.035,
  "pool_apys": {
    "orca_sol_usdc": 71.6,
    ...
  },
  "jlp_apy": 45.2,
  "timestamp": 1710864000.0
}
```

### GET /api/pools
Top pools by APY.

Response: Array of:
```json
{ "pool": "orca_sol_usdc", "apy": 71.6 }
```
