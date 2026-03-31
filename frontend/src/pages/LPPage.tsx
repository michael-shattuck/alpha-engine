import { useData } from '../DataContext'
import PositionDetail from '../components/PositionDetail'
import ControlPanel from '../components/ControlPanel'
import EnginePanel from '../components/EnginePanel'
import WalletPanel from '../components/WalletPanel'

export default function LPPage() {
  const { status, lifecycle, optimizer, wallet } = useData()
  const d = status.data
  if (!d) return null

  const lp = d.strategies['leveraged_lp']
  if (!lp) return <div className="text-gray-500 text-sm">LP strategy not found</div>

  const m = lp.metrics as Record<string, number>

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-5">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Leveraged LP</h2>
            <div className="flex items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                lp.status === 'active' ? 'bg-green-500/15 text-green-400 border border-green-500/30' :
                'bg-gray-500/15 text-gray-400 border border-gray-500/30'
              }`}>{lp.status}</span>
              {(m?.leverage ?? 0) > 1 && (
                <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] font-semibold text-purple-400 border border-purple-500/30">
                  {(m?.leverage ?? 1).toFixed(1)}x
                </span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-4">
            <Stat label="Equity" value={`$${(m?.equity ?? lp.capital_allocated).toFixed(0)}`} />
            <Stat label="Net Value" value={`$${(m?.net_value ?? lp.current_value).toFixed(0)}`} positive={(m?.net_value ?? 0) >= (m?.equity ?? lp.capital_allocated)} />
            <Stat label="Borrowed" value={`$${(m?.borrowed ?? 0).toFixed(0)}`} />
            <Stat label="Pool APY" value={`${(m?.pool_apy ?? 0).toFixed(0)}%`} />
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Target Leverage" value={`${(m?.target_leverage ?? 1).toFixed(1)}x`} />
            <Stat label="Optimal Range" value={`${((m?.range_pct ?? 0.03) * 100).toFixed(1)}%`} />
            <Stat label="Volatility" value={`${((m?.volatility ?? 0) * 100).toFixed(3)}%`} />
            <Stat label="Health Factor" value={`${(m?.health_factor ?? 999) > 100 ? 'SAFE' : (m?.health_factor ?? 0).toFixed(2)}`}
              positive={(m?.health_factor ?? 999) > 2} />
          </div>
        </div>

        <PositionDetail status={d} />

        {lp.error && (
          <div className="rounded-lg border border-red-800/50 bg-red-950/30 p-4">
            <div className="text-xs font-semibold text-red-400 mb-1">Error</div>
            <div className="text-xs text-red-300 break-all">{lp.error}</div>
          </div>
        )}
      </div>

      <div className="space-y-5">
        <ControlPanel status={d} onRefresh={status.refresh} />
        {wallet.data && <WalletPanel wallet={wallet.data} />}
        <EnginePanel lifecycle={lifecycle.data ?? null} optimizer={optimizer.data ?? null} />
      </div>
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
