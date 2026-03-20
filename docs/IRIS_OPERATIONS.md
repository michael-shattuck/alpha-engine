# Iris Operations Guide: Alpha Engine

## System Purpose

Alpha Engine is an automated 3x leveraged LP system on Solana. It earns yield by providing concentrated liquidity on Orca Whirlpools with dynamic range sizing, aggressive fee compounding, and AI-driven risk management.

Backtested: $1,000 -> $29,036 in 11 months (340 days, SOL -32.4%). Zero losing months. 3.4% max drawdown.

## Quick Status

```bash
curl -s http://localhost:8090/api/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Alpha Engine [{d[\"mode\"]}]:')
print(f'  Capital: \${d[\"capital\"]:,.0f} | Value: \${d[\"total_value\"]:,.2f} | P&L: \${d[\"total_pnl\"]:+,.2f} ({d[\"total_pnl_percent\"]:+.2f}%)')
print(f'  Projected: {d[\"projected_dpy\"]:+.2f}%/day | {d[\"projected_mpy\"]:+.1f}%/month | {d[\"projected_apy\"]:+.0f}%/year')
print(f'  Fees: \${d[\"total_fees\"]:,.2f} | Risk: {d[\"risk_level\"]} | CB: {d[\"circuit_breaker_active\"]} | SOL: \${d[\"sol_price\"]:.2f}')
for sid, s in d['strategies'].items():
    m = s.get('metrics', {})
    e = 'ON' if s['enabled'] else 'DORMANT'
    print(f'  {s[\"name\"]} [{e}]: lev={m.get(\"leverage\",\"-\")} range={m.get(\"range_pct\",\"-\")} health={m.get(\"health_factor\",\"-\")} vol={m.get(\"volatility\",\"-\")}')
g = d.get('guardian', {})
if g:
    print(f'  Guardian: dd={g.get(\"drawdown_pct\",0):.1f}% recovery={g.get(\"recovery_mode\",False)} scale={g.get(\"position_scale\",0):.0%}')
for r in d.get('ai_reasoning', [])[-3:]:
    print(f'  AI: {r}')
"
```

## Service Management

```bash
sudo systemctl status alpha-engine
sudo systemctl restart alpha-engine
sudo systemctl stop alpha-engine
sudo journalctl -u alpha-engine -f
sudo journalctl -u alpha-engine --since '1 hour ago' -p err
```

## Architecture

### Active Strategies
| Strategy | Allocation | Mechanism |
|----------|-----------|-----------|
| Leveraged LP | 80% | 3x leveraged concentrated LP on Orca SOL-USDC, dynamic range, compounding |
| Volatile Pairs | 20% | High-APY pools (100%+ APY), +/-3% range |

### Dormant Strategies (auto-activate)
| Strategy | Trigger | Mechanism |
|----------|---------|-----------|
| Adaptive Range | High vol + recovery mode | Volatility-adaptive range management |
| Funding Arb | Drift funding > 15% APY | Short perps when shorts earn funding |

### Intelligence Layer
- **Guardian**: Drawdown tracking, stop-losses, recovery mode, warmup scaling
- **AI Orchestrator**: Preemptive rebalancing, strategy scoring, dormant activation

## Key Metrics

### Health Indicators

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| projected_dpy | > +1% | 0-1% | < 0% |
| projected_mpy | > +20% | 5-20% | < 5% |
| risk_level | low | medium/high | critical |
| health_factor | > 2.0 | 1.2-2.0 | < 1.2 |
| drawdown_pct | < 3% | 3-8% | > 8% |
| volatility | < 0.03 | 0.03-0.06 | > 0.06 |
| position_scale | 100% | 50-99% | < 50% |
| circuit_breaker | false | - | true |

### What the AI Reasoning Tells You
The `ai_reasoning` field in /api/status shows the AI's latest decision reasoning:
- "Position scale: X%" -- Guardian is limiting position size (warmup or drawdown)
- "Leverage capped to X.Xx" -- Recovery mode or high volatility
- "Preemptive rebalance: approaching_lower" -- AI detected momentum toward range boundary
- "Activate funding_arb: funding_apy=X%" -- Dormant strategy activating
- "High rebalance frequency" -- Consider checking if market is too choppy

## AI Management Playbook

### Routine Monitoring (every 30 minutes)

Check status. If all green, do nothing. System is self-managing.

### Decision Matrix

| Condition | Action | Urgency |
|-----------|--------|---------|
| Everything green, projected_mpy > 20% | None. System working. | Low |
| projected_mpy 5-20% | Normal during weekends/low vol. Monitor. | Low |
| projected_mpy < 5% for > 24h | Check pool APY at DeFiLlama. May need wider market. | Medium |
| risk_level = high | Guardian auto-adjusts. Verify leverage reduced. | Medium |
| risk_level = critical | Check if circuit breaker should trigger. | High |
| drawdown > 5% | Guardian scales down. Verify in logs. | Medium |
| drawdown > 8% | Recovery mode active. Leverage capped at 1.5x. Monitor. | High |
| circuit_breaker = true | ALL positions closed. Investigate cause before restarting. | Critical |
| health_factor < 1.5 | Auto-deleverage imminent. Verify. | High |
| Strategy status = error | Check error field. Usually transient. Restart service. | Medium |
| Strategy status = idle | Deleveraged or stopped. Check if manual re-enable needed. | Medium |
| SOL drops > 10% in 24h | System auto-widens range + deleverages. Verify. | High |
| SOL pumps > 10% in 24h | Positions exit range upward. Auto-rebalances. Verify. | Medium |

### Adjusting Leverage

```bash
# Set leverage (1.0 - 5.0)
curl -X POST http://localhost:8090/api/config/leverage \
  -H 'Content-Type: application/json' -d '{"leverage": 3.0}'

# Check current
curl -s http://localhost:8090/api/strategies/leveraged_lp | python3 -c "
import sys,json; m=json.load(sys.stdin)['metrics']
print(f'Leverage: {m[\"leverage\"]:.1f}x | Target: {m[\"target_leverage\"]:.1f}x | Health: {m[\"health_factor\"]:.1f}')
"
```

### Adjusting Allocation

```bash
# Aggressive (more leverage)
curl -X POST http://localhost:8090/api/config/allocation \
  -H 'Content-Type: application/json' -d '{"allocations": {"leveraged_lp": 0.90, "volatile_pairs": 0.10}}'

# Conservative (less leverage exposure)
curl -X POST http://localhost:8090/api/config/allocation \
  -H 'Content-Type: application/json' -d '{"allocations": {"leveraged_lp": 0.60, "volatile_pairs": 0.40}}'
```

### Emergency Exit

```bash
# Exit ALL positions
curl -X POST http://localhost:8090/api/emergency-exit

# Exit single strategy
curl -X POST http://localhost:8090/api/emergency-exit/leveraged_lp

# Disable a strategy (keeps positions but stops new ones)
curl -X POST http://localhost:8090/api/strategies/leveraged_lp/toggle \
  -H 'Content-Type: application/json' -d '{"enabled": false}'
```

### Checking Exit Cost

```bash
curl -s http://localhost:8090/api/exit-cost | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'Deployed: \${d[\"total_deployed\"]:,.2f} | Borrowed: \${d[\"total_borrowed\"]:,.2f}')
print(f'Exit cost: \${d[\"estimated_total_cost\"]:.2f} ({d[\"cost_percent\"]:.3f}%)')
"
```

### Checking AI Intelligence

```bash
curl -s http://localhost:8090/api/intelligence | python3 -c "
import sys,json; d=json.load(sys.stdin)
print(f'Rebalances (24h): {d[\"rebalance_frequency_24h\"]:.1f}/day')
print(f'Rebalance cost (24h): \${d[\"rebalance_cost_24h\"]:.2f}')
g = d.get('guardian', {})
print(f'Guardian: dd={g.get(\"drawdown_pct\",0):.1f}% recovery={g.get(\"recovery_mode\")} scale={g.get(\"position_scale\",0):.0%}')
for r in d.get('reasoning', []):
    print(f'  {r}')
"
```

## Emergency Procedures

### SOL Flash Crash (> 15% in 1 hour)
1. System should auto-trigger circuit breaker at 5% hourly loss
2. If it didn't: `curl -X POST http://localhost:8090/api/emergency-exit`
3. Wait at least 2 hours of < 2% hourly movement
4. Restart: `sudo systemctl restart alpha-engine`

### System Not Responding
```bash
sudo systemctl status alpha-engine
sudo journalctl -u alpha-engine --since '30 min ago' -p err
sudo systemctl restart alpha-engine
```

### Negative P&L for > 48 Hours
1. Check SOL trend -- sustained decline means IL accumulating
2. Check if auto-deleverage happened (leverage should be 1.5x)
3. If P&L < -10%: `curl -X POST http://localhost:8090/api/emergency-exit`
4. Wait at least 4 hours before restarting

### After Circuit Breaker Triggers
1. Check what caused it: `curl -s http://localhost:8090/api/events?limit=20`
2. Check SOL price stability
3. Do NOT immediately restart. Wait at least 1 hour.
4. When stable: `sudo systemctl restart alpha-engine`
5. Guardian warmup will ramp position from 20% to 100% over 6 hours

## Performance Expectations

| Market Condition | Expected Monthly | Expected Daily |
|-----------------|-----------------|----------------|
| Flat / low vol | +25-40% | +0.8-1.3% |
| Bull (SOL +10%) | +30-50% | +1.0-1.7% |
| Mild bear (SOL -5%) | +15-25% | +0.5-0.8% |
| Crash (SOL -15%+) | +5-15% | +0.2-0.5% |
| High vol choppy | +10-20% | +0.3-0.7% |

Worst month in 340-day backtest: +16.5% (during warmup).

## Reporting Format

```
Alpha Engine [paper/live]:
  Capital: $X | Value: $Y | P&L: +$Z (+X.X%)
  Projected: +X.XX%/day | +XX.X%/month | +XXX%/year
  Fees: $X.XX | Risk: low | SOL: $XX.XX
  Leveraged LP [ON]: 3.0x lev | +/-X.X% range | health=X.X | vol=X.XXXX
  Volatile Pairs [ON]: X positions | $X.XX fees
  Guardian: dd=X.X% | recovery=false | scale=100%
  AI: [latest reasoning]
  Uptime: XXh | Events: [last 3]
```

## Configuration

| Parameter | Default | API Endpoint | Effect |
|-----------|---------|-------------|--------|
| leverage | 3.0 | POST /api/config/leverage | Max leverage in calm markets |
| allocation | 80/20 | POST /api/config/allocation | Capital split between strategies |
| BORROW_RATE_APY | 12.0 | server code | Assumed borrow cost |
| COMPOUND_THRESHOLD | 0.002 | server code | Compound when fees > 0.2% of equity |
| REBALANCE_COST | 0.0008 | server code | Slippage per rebalance |
| ORCHESTRATOR_INTERVAL | 30s | config.py | Main loop frequency |
| PRICE_UPDATE_INTERVAL | 10s | config.py | Price fetch frequency |
| RISK_CHECK_INTERVAL | 15s | config.py | Intelligence cycle frequency |

## Ports

| Port | Service |
|------|---------|
| 8090 | Alpha Engine API + Dashboard |
| 8080 | Smart Money Dashboard |
| 8081 | Smart Money API |
| 8440 | Liquidation Bot Dashboard |

## State Files

| Path | Purpose |
|------|---------|
| ~/alpha_engine/state/portfolio.json | Portfolio state |
| ~/alpha_engine/state/history.json | P&L history snapshots |
| ~/alpha_engine/state/events.json | Event log |
