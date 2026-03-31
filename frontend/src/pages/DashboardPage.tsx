import { Link } from 'react-router-dom'
import { useData } from '../DataContext'
import PerformanceChart from '../components/PerformanceChart'
import type { StrategyState } from '../types'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}

function pct(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

export default function DashboardPage() {
  const { status, history, wallet, market, scalper } = useData()
  const d = status.data
  if (!d) return null

  const lp = d.strategies['leveraged_lp']
  const sc = d.strategies['volatility_scalper']
  const regime = scalper.data?.regime ?? 'unknown'
  const sol = d.sol_price

  const activeTrades = scalper.data?.active_trades ?? []
  const unrealizedPnl = activeTrades.reduce((sum: number, t: { pnl_usd?: number }) => sum + (t.pnl_usd ?? 0), 0)
  const realizedPnl = scalper.data?.daily_stats?.daily_pnl_usd ?? 0
  const scalperTotalPnl = realizedPnl + unrealizedPnl

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <StatCard label="Portfolio" value={fmt(d.total_value)} sub={pct(d.total_pnl_percent)} positive={d.total_pnl >= 0} />
        <StatCard label="Scalper PnL" value={fmt(scalperTotalPnl)} sub={`${activeTrades.length} active | ${scalper.data?.daily_stats?.trades_today ?? 0} done`} positive={scalperTotalPnl >= 0} />
        <StatCard label="SOL Price" value={fmt(sol)} sub={`${market.data?.sol_change_1h?.toFixed(2) ?? '0'}% 1h`} positive={(market.data?.sol_change_1h ?? 0) >= 0} />
        <StatCard label="Projected MPY" value={`${d.projected_mpy.toFixed(1)}%`} sub={`${d.projected_dpy.toFixed(2)}%/day`} positive={d.projected_mpy >= 0} />
        <StatCard label="Regime" value={REGIME_LABELS[regime] ?? regime} sub={`${((scalper.data?.regime_confidence ?? 0) * 100).toFixed(0)}% confidence`} color={REGIME_COLORS[regime]} />
      </div>

      {history.data && <PerformanceChart history={history.data} />}

      <div>
        <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">Strategies</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {lp && <StrategyLink to="/lp" strategy={lp} />}
          {sc && <StrategyLink to="/scalper" strategy={sc} scalperData={scalper.data} />}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="mb-2 text-xs text-gray-500">Wallet</div>
          {wallet.data && (
            <div className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-gray-500">SOL</span>
                <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{wallet.data.sol_balance.toFixed(4)}</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-gray-500">USDC</span>
                <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(wallet.data.usdc_balance)}</span>
              </div>
              <div className="flex justify-between text-xs border-t border-gray-800 pt-1">
                <span className="text-gray-500">Total</span>
                <span className="font-mono text-white font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(wallet.data.total_usd)}</span>
              </div>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="mb-2 text-xs text-gray-500">Allocation</div>
          <div className="space-y-2">
            {(Object.values(d.strategies) as StrategyState[]).filter(s => s.capital_allocated > 0).map(s => (
              <div key={s.id}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-400">{s.name}</span>
                  <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{(s.target_allocation * 100).toFixed(0)}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-gray-800 overflow-hidden">
                  <div className="h-full rounded-full bg-blue-500" style={{ width: `${s.target_allocation * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <Link to="/risk" className="rounded-lg border border-gray-800 bg-gray-900 p-4 hover:border-gray-700 transition-colors">
          <div className="mb-2 text-xs text-gray-500">Risk</div>
          <div className="flex items-center justify-between">
            <span className={`text-sm font-semibold capitalize ${
              d.risk_level === 'low' ? 'text-green-400' :
              d.risk_level === 'medium' ? 'text-yellow-400' :
              d.risk_level === 'high' ? 'text-orange-400' : 'text-red-400'
            }`}>{d.risk_level}</span>
            {d.circuit_breaker_active && (
              <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-400 border border-red-500/30">CB ACTIVE</span>
            )}
          </div>
          <div className="mt-1 text-[10px] text-gray-600">Click for details</div>
        </Link>
      </div>
    </div>
  )
}

const REGIME_LABELS: Record<string, string> = {
  trending_up: 'Trending Up', trending_down: 'Trending Down',
  ranging: 'Ranging', volatile_ranging: 'Vol. Ranging',
  dead: 'Dead', unknown: 'Warming Up',
}
const REGIME_COLORS: Record<string, string> = {
  trending_up: 'text-green-400', trending_down: 'text-red-400',
  ranging: 'text-blue-400', volatile_ranging: 'text-yellow-400',
  dead: 'text-gray-500', unknown: 'text-gray-600',
}

function StatCard({ label, value, sub, positive, color }: {
  label: string; value: string; sub?: string; positive?: boolean; color?: string
}) {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
      <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{label}</div>
      <div className={`font-mono text-lg font-bold ${color ?? 'text-white'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {sub && <div className={`font-mono text-xs mt-0.5 ${color ?? (positive ? 'text-green-400' : 'text-red-400')}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{sub}</div>}
    </div>
  )
}

function StrategyLink({ to, strategy: s, scalperData }: {
  to: string; strategy: StrategyState; sol?: number; scalperData?: { daily_stats: { trades_today: number; wins: number; losses: number; daily_pnl_pct: number }; regime: string } | null
}) {
  const isLP = s.id === 'leveraged_lp'
  const lev = (s.metrics?.leverage as number) ?? 1

  return (
    <Link to={to} className="rounded-lg border border-gray-800 bg-gray-900 p-4 hover:border-gray-700 transition-colors block">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-200">{s.name}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
            s.status === 'active' ? 'bg-green-500/15 text-green-400' :
            s.status === 'idle' || s.status === 'watching' ? 'bg-gray-500/15 text-gray-400' :
            'bg-yellow-500/15 text-yellow-400'
          }`}>{s.status}</span>
        </div>
        {s.enabled && <span className="text-[10px] text-gray-600">Click for details</span>}
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Value</div>
          <div className="font-mono text-sm font-medium text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            ${s.current_value.toFixed(0)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">PnL</div>
          <div className={`font-mono text-sm font-medium ${s.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {s.total_pnl >= 0 ? '+' : ''}{s.total_pnl_percent.toFixed(2)}%
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">
            {isLP ? 'Leverage' : 'Trades'}
          </div>
          <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {isLP ? `${lev.toFixed(1)}x` : `${scalperData?.daily_stats?.trades_today ?? 0}`}
          </div>
        </div>
      </div>
    </Link>
  )
}
