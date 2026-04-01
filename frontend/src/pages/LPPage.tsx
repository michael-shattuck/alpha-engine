import { useData } from '../DataContext'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}

export default function LPPage() {
  const { status, lifecycle, optimizer } = useData()
  const d = status.data
  if (!d) return null

  const lp = d.strategies['leveraged_lp']
  if (!lp) return <div className="text-gray-500 text-sm">LP strategy not found</div>

  const m = (lp.metrics ?? {}) as Record<string, number>
  const lc = lifecycle.data
  const opt = optimizer.data
  const positions = lp.positions ?? []
  const capital = lp.capital_allocated
  const isPaper = d.mode === 'paper'

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Leveraged LP</h2>
            {isPaper && <span className="rounded bg-yellow-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-yellow-400 border border-yellow-500/30">PAPER</span>}
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
              lp.enabled ? (lp.status === 'active' ? 'bg-green-500/15 text-green-400 border border-green-500/30' : 'bg-blue-500/15 text-blue-400 border border-blue-500/30')
                : 'bg-gray-500/15 text-gray-400 border border-gray-500/30'
            }`}>{lp.enabled ? lp.status : 'disabled'}</span>
          </div>
          {(m.leverage ?? 0) > 1 && (
            <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-purple-400 border border-purple-500/30">
              {(m.leverage ?? 1).toFixed(1)}x leverage
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-4">
          <Stat label="Capital" value={fmt(capital)} />
          <Stat label="Pool APY" value={`${(m.pool_apy ?? 0).toFixed(0)}%`} positive={(m.pool_apy ?? 0) > 0} />
          <Stat label="Leveraged APY" value={`${((m.pool_apy ?? 0) * (m.leverage ?? 1)).toFixed(0)}%`} positive />
          <Stat label="Volatility" value={`${((m.volatility ?? 0) * 100).toFixed(3)}%`} />
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Leverage" value={`${(m.leverage ?? 1).toFixed(1)}x`} />
          <Stat label="Range" value={`${((m.range_pct ?? 0.03) * 100).toFixed(1)}%`} />
          <Stat label="Health" value={(m.health_factor ?? 999) > 100 ? 'Safe' : (m.health_factor ?? 0).toFixed(2)}
            positive={(m.health_factor ?? 999) > 2} />
          <Stat label="Positions" value={`${positions.length}`} />
        </div>
      </div>

      {positions.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="text-sm font-medium tracking-wide text-gray-400 uppercase mb-3">Open Positions</h3>
          <div className="space-y-3">
            {positions.map((p: any, i: number) => (
              <div key={i} className="rounded border border-gray-800 p-3">
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 text-xs">
                  <div>
                    <span className="text-gray-500">Pool: </span>
                    <span className="text-white">SOL/USDC</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Deposited: </span>
                    <span className="font-mono text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(p.deposit_usd)}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Value: </span>
                    <span className="font-mono text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(p.current_value_usd)}</span>
                  </div>
                  <div>
                    <span className="text-gray-500">Fees: </span>
                    <span className="font-mono text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(p.fees_earned_usd)}</span>
                  </div>
                </div>
                {p.in_range !== undefined && (
                  <div className="mt-2 text-xs">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      p.in_range ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
                    }`}>{p.in_range ? 'IN RANGE' : 'OUT OF RANGE'}</span>
                    {p.il_percent !== undefined && p.il_percent !== 0 && (
                      <span className="ml-2 text-gray-500">IL: {(p.il_percent * 100).toFixed(2)}%</span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {lc && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
            <div className="text-xs text-gray-500 mb-2">Lifecycle</div>
            <div className="space-y-1 text-xs">
              <Row label="Phase" value={lc.phase ?? 'idle'} />
              {lc.error && <Row label="Error" value={lc.error} color="red" />}
              {lc.position_mint && <Row label="Position" value={lc.position_mint.slice(0, 12) + '...'} />}
            </div>
          </div>
        )}

        {opt && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
            <div className="text-xs text-gray-500 mb-2">Optimizer</div>
            <div className="space-y-1 text-xs">
              {opt.actual_fee_apy !== undefined && <Row label="Actual APY" value={`${opt.actual_fee_apy.toFixed(1)}%`} />}
              {opt.return_floor !== undefined && <Row label="Return Floor" value={`$${opt.return_floor}/mo`} />}
              {opt.optimized?.range_pct !== undefined && <Row label="Optimal Range" value={`${(opt.optimized.range_pct * 100).toFixed(1)}%`} />}
            </div>
          </div>
        )}
      </div>

      {lp.error && (
        <div className="rounded-lg border border-red-800/50 bg-red-950/30 p-4">
          <div className="text-xs font-semibold text-red-400 mb-1">Error</div>
          <div className="text-xs text-red-300 break-all">{lp.error}</div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-600">{label}</div>
      <div className={`font-mono text-sm font-medium ${
        positive === true ? 'text-green-400' : positive === false ? 'text-red-400' : 'text-white'
      }`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color === 'red' ? 'text-red-400' : 'text-gray-300'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
