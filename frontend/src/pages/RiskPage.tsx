import { useData } from '../DataContext'
import type { StrategyState } from '../types'
import RiskPanel from '../components/RiskPanel'
import AlertLog from '../components/AlertLog'
import EventLog from '../components/EventLog'

export default function RiskPage() {
  const { status, alerts, events, market } = useData()
  const d = status.data
  if (!d) return null

  const g = d.guardian as Record<string, unknown>

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-5">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Risk Overview</h2>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-4">
            <Stat label="Risk Level" value={String(d.risk_level)} color={
              d.risk_level === 'low' ? 'text-green-400' : d.risk_level === 'medium' ? 'text-yellow-400' :
              d.risk_level === 'high' ? 'text-orange-400' : 'text-red-400'
            } />
            <Stat label="Drawdown" value={`${((g?.drawdown_pct as number) ?? 0).toFixed(2)}%`} />
            <Stat label="Circuit Breaker" value={d.circuit_breaker_active ? 'ACTIVE' : 'Off'}
              color={d.circuit_breaker_active ? 'text-red-400' : 'text-green-400'} />
            <Stat label="Recovery Mode" value={(g?.recovery_mode as boolean) ? 'YES' : 'No'}
              color={(g?.recovery_mode as boolean) ? 'text-yellow-400' : 'text-gray-400'} />
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Total Value" value={`$${d.total_value.toFixed(0)}`} />
            <Stat label="Capital" value={`$${d.capital.toFixed(0)}`} />
            <Stat label="Total PnL" value={`$${d.total_pnl.toFixed(2)}`} color={d.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'} />
            <Stat label="Uptime" value={`${d.uptime_hours.toFixed(1)}h`} />
          </div>
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Strategy Health</h3>
          <div className="space-y-2">
            {(Object.values(d.strategies) as StrategyState[]).filter(s => s.capital_allocated > 0).map(s => (
              <div key={s.id} className="rounded border border-gray-800 bg-gray-950/50 p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-medium text-gray-300">{s.name}</span>
                  <div className="flex items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      s.enabled ? 'bg-green-500/15 text-green-400' : 'bg-gray-500/15 text-gray-500'
                    }`}>{s.enabled ? 'ON' : 'OFF'}</span>
                    <span className={`text-[10px] ${!s.error ? 'text-green-400' : 'text-red-400'}`}>
                      {s.error ? 'ERROR' : 'HEALTHY'}
                    </span>
                  </div>
                </div>
                {s.error && (
                  <div className="text-[10px] text-red-400 break-all mt-1">{s.error}</div>
                )}
                <div className="flex gap-4 mt-1 text-[10px] text-gray-500">
                  <span>Capital: ${s.capital_allocated.toFixed(0)}</span>
                  <span>Value: ${s.current_value.toFixed(0)}</span>
                  <span>Positions: {s.position_count}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {alerts.data && alerts.data.length > 0 && <AlertLog alerts={alerts.data} />}
        {events.data && <EventLog events={events.data} />}
      </div>

      <div className="space-y-5">
        <RiskPanel status={d} />

        {market.data && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Market</h3>
            <div className="space-y-2">
              <Row label="SOL Price" value={`$${market.data.sol_price.toFixed(2)}`} />
              <Row label="1h Change" value={`${market.data.sol_change_1h.toFixed(2)}%`} color={market.data.sol_change_1h >= 0 ? 'text-green-400' : 'text-red-400'} />
              <Row label="24h Change" value={`${market.data.sol_change_24h.toFixed(2)}%`} color={market.data.sol_change_24h >= 0 ? 'text-green-400' : 'text-red-400'} />
              <Row label="Vol (1h)" value={`${(market.data.volatility_1h * 100).toFixed(3)}%`} />
              <Row label="Vol (24h)" value={`${(market.data.volatility_24h * 100).toFixed(3)}%`} />
              {Object.entries(market.data.pool_apys).slice(0, 5).map(([pool, apy]) => (
                <Row key={pool} label={pool.replace('orca_', '').replace(/_/g, '/')} value={`${apy.toFixed(0)}% APY`} />
              ))}
            </div>
          </div>
        )}

        {(d.ai_reasoning ?? []).length > 0 && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">AI Reasoning</h3>
            <div className="space-y-1">
              {d.ai_reasoning.map((r, i) => (
                <div key={i} className="text-[10px] text-gray-400">{r}</div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-600">{label}</div>
      <div className={`font-mono text-sm font-bold ${color ?? 'text-white'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color ?? 'text-gray-300'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
