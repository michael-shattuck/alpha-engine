# Iris Operations Guide: Alpha Engine

## System Purpose

Alpha Engine is an automated leveraged LP system on Solana. It earns yield by providing concentrated liquidity on Orca Whirlpools with 2.5x leverage, dynamic range sizing, and aggressive fee compounding. Backtested to +47.8%/month average over 340 days.

## Quick Status

```bash
curl -s http://localhost:8090/api/status | python3 -m json.tool
```

Key fields:
- `total_pnl_percent`: Current P&L. Should be positive and growing.
- `projected_mpy`: Projected monthly return based on current rate.
- `projected_apy`: Projected annual return.
- `risk_level`: "low"/"medium" is healthy. "high"/"critical" needs action.
- `circuit_breaker_active`: If true, everything is stopped.
- `total_fees`: Cumulative fees earned.

## Service Management

```bash
sudo systemctl status alpha-engine
sudo systemctl restart alpha-engine
sudo journalctl -u alpha-engine -f
```

## Architecture

Two active strategies:
1. **Leveraged LP** (80% allocation): 2.5x leveraged concentrated LP on Orca SOL-USDC with dynamic range sizing and compounding. This is the primary alpha generator.
2. **Volatile Pairs** (20% allocation): High-APY pools (100%+ APY). Diversification and upside capture.

## How the Leveraged LP Works

1. Deposits equity into Orca SOL-USDC concentrated LP position
2. Borrows additional USDC at ~12% APY to lever up 2.5x
3. Earns LP fees at concentrated rate (2-8x base APY depending on range width)
4. Automatically adjusts range width based on volatility:
   - Vol < 0.5%: +/-2% range (tight, max fees)
   - Vol 0.5-1.5%: +/-3%
   - Vol 1.5-3%: +/-5%
   - Vol 3-6%: +/-8%
   - Vol > 6%: +/-12% (wide, protect capital)
5. Auto-deleverages (reduces to 1.5x) in high-volatility environments
6. Compounds fees back into position every time fees exceed 0.2% of equity
7. Auto-rebalances when price exits range

## Key Metrics to Monitor

### Strategy Metrics (via /api/strategies/leveraged_lp)

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| health_factor | > 2.0 | 1.2 - 2.0 | < 1.2 |
| leverage | 1.5 - 2.5 | 2.5 - 3.0 | > 3.0 |
| volatility | < 0.03 | 0.03 - 0.06 | > 0.06 |
| range_pct | 0.02 - 0.08 | 0.08 - 0.12 | stuck at 0.12 |
| net_value | > equity | declining | < 80% of equity |

### Portfolio Metrics (via /api/status)

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| projected_mpy | > 10% | 5-10% | < 5% or negative |
| risk_level | low/medium | high | critical |
| total_pnl_percent | positive | -1% to 0% | < -5% |

## AI Management Playbook

### Routine Monitoring (every 30 minutes)

```bash
STATUS=$(curl -s http://localhost:8090/api/status)
echo $STATUS | python3 -c "
import sys, json
d = json.load(sys.stdin)
risk = d['risk_level']
pnl = d['total_pnl_percent']
mpy = d['projected_mpy']
cb = d['circuit_breaker_active']
print(f'P&L: {pnl:+.2f}% | Projected: {mpy:.1f}%/mo | Risk: {risk} | CB: {cb}')
for sid, s in d['strategies'].items():
    m = s.get('metrics', {})
    print(f'  {s[\"name\"]}: health={m.get(\"health_factor\",0):.1f} lev={m.get(\"leverage\",0):.1f}x vol={m.get(\"volatility\",0):.4f} range={m.get(\"range_pct\",0):.1%}')
"
```

### Decision Matrix

| Condition | Action |
|-----------|--------|
| health_factor < 1.5 | Reduce leverage: disable leveraged_lp, wait for volatility to drop |
| volatility > 0.06 for > 2 hours | System auto-widens range. Verify it happened. |
| total_pnl_percent < -5% | Check if circuit breaker should trigger. Consider manual pause. |
| projected_mpy < 5% for > 24h | Check pool APY -- may be low-volume period. Normal during weekends. |
| projected_mpy > 50% | Verify accuracy. Could be calculation artifact from short uptime. |
| circuit_breaker triggered | All positions closed. Investigate cause. Don't restart immediately. |
| Leveraged LP status = "error" | Check error field. Usually RPC or API transient failure. Restart. |
| Leveraged LP status = "idle" | Means it deleveraged. Check health_factor history. May need manual re-enable. |
| SOL drops > 10% in 24h | System auto-deleverages and widens range. Verify. Consider pausing volatile_pairs. |
| SOL pumps > 10% in 24h | Positions may exit range upward. System auto-rebalances. Verify. |

### Adjusting Leverage

When market conditions change, Iris can adjust the base leverage:

```bash
# Check current leverage
curl -s http://localhost:8090/api/strategies/leveraged_lp | python3 -c "import sys,json; m=json.load(sys.stdin)['metrics']; print(f'Current: {m[\"leverage\"]:.1f}x, Target: {m[\"target_leverage\"]:.1f}x, Health: {m[\"health_factor\"]:.1f}')"
```

The system auto-adjusts leverage based on volatility (2.5x -> 2.0x -> 1.5x). Iris should not override this unless there's a specific reason (e.g., known upcoming event like a rate decision).

### Adjusting Allocation

```bash
# Shift more to leveraged LP (aggressive)
curl -X POST http://localhost:8090/api/config/allocation \
  -H 'Content-Type: application/json' -d '{
    "allocations": {"leveraged_lp": 0.90, "volatile_pairs": 0.10}
  }'

# Shift to conservative (reduce leverage exposure)
curl -X POST http://localhost:8090/api/config/allocation \
  -H 'Content-Type: application/json' -d '{
    "allocations": {"leveraged_lp": 0.60, "volatile_pairs": 0.40}
  }'
```

### Emergency Procedures

#### SOL Flash Crash (> 15% in 1 hour)
1. System should auto-trigger circuit breaker at 5% hourly loss
2. If it didn't, manually pause:
```bash
curl -X POST http://localhost:8090/api/strategies/leveraged_lp/toggle \
  -H 'Content-Type: application/json' -d '{"enabled": false}'
```
3. Wait for stability (at least 2 hours of < 2% hourly movement)
4. Restart: `sudo systemctl restart alpha-engine`

#### System Not Responding
```bash
# Check if process is alive
sudo systemctl status alpha-engine

# Check recent logs for errors
sudo journalctl -u alpha-engine --since "30 min ago" -p err

# Force restart
sudo systemctl restart alpha-engine
```

#### Negative P&L for > 48 Hours
1. Check SOL price trend -- sustained decline means IL is accumulating
2. Check if auto-deleverage happened (leverage should be 1.5x in high vol)
3. If P&L is < -10%, consider pausing until market stabilizes:
```bash
curl -X POST http://localhost:8090/api/strategies/leveraged_lp/toggle \
  -H 'Content-Type: application/json' -d '{"enabled": false}'
```
4. Do NOT restart immediately after pausing. Wait at least 4 hours.

## Performance Expectations

### Monthly Returns (from 340-day backtest)

| Market Condition | Expected Monthly Return |
|-----------------|------------------------|
| Flat / low vol | +15-30% |
| Bull (SOL +10%) | +20-40% |
| Mild bear (SOL -5%) | +5-15% |
| Crash (SOL -15%+) | -5% to -15% (auto-deleverage limits loss) |
| Recovery after crash | +25-50% (compounding on rebuilt equity) |

### Red Flags
- Monthly return < 0% for 2+ consecutive months: strategy may need parameter tuning
- Health factor consistently < 1.5: borrow rate may have increased, check Kamino/MarginFi
- Rebalance frequency > 10/day: volatility too high for current range settings

## Reporting Format

When reporting on Alpha Engine:
```
Alpha Engine [paper/live]:
  Capital: $X | Value: $Y | P&L: $Z (+X.X%)
  Projected: +XX.X%/month | +XXX%/year
  Risk: low | Health: X.X | Leverage: X.Xx
  SOL: $XX.XX | Volatility: X.XX% | Range: +/-X.X%
  Strategies: leveraged_lp (active), volatile_pairs (active)
  Uptime: XXh | Last event: [type] [time ago]
  Recent: [last 3 events from /api/events]
```

## Configuration Reference

| Parameter | Default | Location | Effect |
|-----------|---------|----------|--------|
| base_leverage | 2.5 | LeveragedLPStrategy init | Max leverage in calm markets |
| base_range | 0.05 | LeveragedLPStrategy init | Default range width |
| BORROW_RATE_APY | 12.0 | leveraged_lp.py | Assumed borrow cost |
| COMPOUND_THRESHOLD | 0.002 | leveraged_lp.py | Compound when fees > 0.2% of equity |
| REBALANCE_COST | 0.0008 | leveraged_lp.py | Slippage cost per rebalance |
| ORCHESTRATOR_INTERVAL | 30 | config.py | Main loop frequency (seconds) |
| PRICE_UPDATE_INTERVAL | 10 | config.py | Price fetch frequency (seconds) |
| RISK_CHECK_INTERVAL | 15 | config.py | Risk assessment frequency (seconds) |

## Ports

| Port | Service |
|------|---------|
| 8090 | Alpha Engine API + Dashboard |
| 8080 | Smart Money Dashboard (separate) |
| 8081 | Smart Money API (separate) |
| 8440 | Liquidation Bot Dashboard (separate) |

## Logs

```bash
sudo journalctl -u alpha-engine --since "1 hour ago"
sudo journalctl -u alpha-engine -p err --since "1 hour ago"
sudo journalctl -u alpha-engine -f
```

## State Files

| Path | Purpose |
|------|---------|
| ~/alpha_engine/state/portfolio.json | Portfolio state |
| ~/alpha_engine/state/history.json | P&L history snapshots |
| ~/alpha_engine/state/events.json | Event log |
