import { useData } from '../DataContext'
import type { StrategyState } from '../types'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}

export default function RiskPage() {
  const { status, alerts, events, market } = useData()
  const d = status.data
  if (!d) return null

  const g = (d.guardian ?? {}) as Record<string, any>
  const strategies = Object.values(d.strategies ?? {}) as StrategyState[]
  const enabled = strategies.filter(s => s.capital_allocated > 0)
  const ddPct = (g.drawdown_pct as number) ?? 0

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <Label>Risk Level</Label>
          <div className={`font-mono text-lg font-bold uppercase ${
            d.risk_level === 'low' ? 'text-green-400' : d.risk_level === 'medium' ? 'text-yellow-400' :
            d.risk_level === 'high' ? 'text-orange-400' : 'text-red-400'
          }`}>{d.risk_level}</div>
        </Card>
        <Card>
          <Label>Drawdown</Label>
          <div className={`font-mono text-lg font-bold ${ddPct > 5 ? 'text-red-400' : ddPct > 2 ? 'text-yellow-400' : 'text-green-400'}`}>
            {ddPct.toFixed(2)}%
          </div>
          <div className="h-1.5 rounded-full bg-gray-800 mt-2 overflow-hidden">
            <div className={`h-full rounded-full ${ddPct > 5 ? 'bg-red-500' : ddPct > 2 ? 'bg-yellow-500' : 'bg-green-500'}`} style={{ width: `${Math.min(ddPct / 10 * 100, 100)}%` }} />
          </div>
          <div className="flex justify-between text-[9px] text-gray-600 mt-0.5"><span>0%</span><span>10% max</span></div>
        </Card>
        <Card>
          <Label>Circuit Breaker</Label>
          <div className={`font-mono text-lg font-bold ${d.circuit_breaker_active ? 'text-red-400' : 'text-green-400'}`}>
            {d.circuit_breaker_active ? 'ACTIVE' : 'OFF'}
          </div>
        </Card>
        <Card>
          <Label>Recovery Mode</Label>
          <div className={`font-mono text-lg font-bold ${g.recovery_mode ? 'text-yellow-400' : 'text-gray-400'}`}>
            {g.recovery_mode ? 'YES' : 'No'}
          </div>
          {g.recovery_mode && <div className="text-[10px] text-yellow-400 mt-1">Leverage capped at 1.5x</div>}
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Strategy Health</h3>
          <div className="space-y-2">
            {enabled.map(s => (
              <div key={s.id} className={`rounded border p-3 ${s.error ? 'border-red-800/30 bg-red-950/10' : 'border-gray-800 bg-gray-950/50'}`}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-white">{s.name}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      s.enabled ? 'bg-green-500/15 text-green-400' : 'bg-gray-500/15 text-gray-500'
                    }`}>{s.enabled ? 'ON' : 'OFF'}</span>
                  </div>
                  <span className={`text-[10px] font-semibold ${!s.error ? 'text-green-400' : 'text-red-400'}`}>
                    {s.error ? 'ERROR' : 'HEALTHY'}
                  </span>
                </div>
                <div className="flex gap-4 mt-1.5 text-[10px] text-gray-500 font-mono" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  <span>Capital: {fmt(s.capital_allocated, 0)}</span>
                  <span>Value: {fmt(s.current_value, 0)}</span>
                  <span>Positions: {s.position_count}</span>
                </div>
                {s.error && <div className="text-[10px] text-red-400 break-all mt-1.5">{s.error}</div>}
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Market Conditions</h3>
          {market.data ? (
            <div className="space-y-2 text-xs">
              <Row label="SOL Price" value={fmt(market.data.sol_price)} />
              <Row label="1h Change" value={`${market.data.sol_change_1h?.toFixed(2) ?? 0}%`} color={market.data.sol_change_1h >= 0 ? 'text-green-400' : 'text-red-400'} />
              <Row label="24h Change" value={`${market.data.sol_change_24h?.toFixed(2) ?? 0}%`} color={market.data.sol_change_24h >= 0 ? 'text-green-400' : 'text-red-400'} />
              <Row label="Volatility (1h)" value={`${((market.data.volatility_1h ?? 0) * 100).toFixed(3)}%`} />
              <Row label="Volatility (24h)" value={`${((market.data.volatility_24h ?? 0) * 100).toFixed(3)}%`} />
            </div>
          ) : <div className="text-gray-600 text-xs">Loading...</div>}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {alerts.data && alerts.data.length > 0 && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Recent Alerts ({alerts.data.length})</h3>
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {alerts.data.slice(0, 20).map((a: any, i: number) => (
                <div key={i} className={`text-[10px] rounded px-2 py-1 ${
                  a.level === 'critical' ? 'bg-red-950/30 text-red-400' :
                  a.level === 'warning' ? 'bg-yellow-950/30 text-yellow-400' :
                  'bg-gray-800/30 text-gray-400'
                }`}>
                  <span className="font-semibold uppercase">{a.level}</span>: {a.message}
                </div>
              ))}
            </div>
          </div>
        )}

        {events.data && events.data.length > 0 && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Recent Events ({events.data.length})</h3>
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {events.data.slice(0, 20).map((e: any, i: number) => (
                <div key={i} className="text-[10px] text-gray-400 rounded bg-gray-800/30 px-2 py-1">
                  <span className="text-gray-500">{new Date((e.timestamp ?? 0) * 1000).toLocaleTimeString()}</span>{' '}
                  <span className="text-gray-300">{e.type}</span>: {e.strategy ?? ''} {typeof e.data === 'string' ? e.data : JSON.stringify(e.data ?? '').slice(0, 80)}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {(d.ai_reasoning ?? []).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">AI Reasoning</h3>
          <div className="space-y-1">
            {d.ai_reasoning.map((r: string, i: number) => (
              <div key={i} className="text-[10px] text-gray-400">{r}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">{children}</div>
}
function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{children}</div>
}
function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color ?? 'text-gray-300'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
