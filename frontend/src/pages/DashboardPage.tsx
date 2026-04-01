import { Link } from 'react-router-dom'
import { useData } from '../DataContext'
import type { StrategyState } from '../types'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
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

export default function DashboardPage() {
  const { status, scalper, market } = useData()
  const d = status.data
  if (!d) return null

  const strategies = Object.values(d.strategies) as StrategyState[]
  const enabled = strategies.filter(s => s.enabled)
  const regime = scalper.data?.regime ?? 'unknown'
  const sol = d.sol_price
  const ds = scalper.data?.daily_stats
  const activeTrades = scalper.data?.active_trades ?? []
  const scalperUnrealized = activeTrades.reduce((sum: number, t: { pnl_usd?: number }) => sum + (t.pnl_usd ?? 0), 0)
  const scalperRealized = ds?.daily_pnl_usd ?? 0
  const scalperPnl = scalperRealized + scalperUnrealized
  const driftAcct = (scalper.data as any)?.drift_account

  const lp = d.strategies['leveraged_lp']
  const lpPositions = lp?.positions ?? []
  const lpFees = lpPositions.reduce((sum: number, p: any) => sum + (p?.fees_earned_usd ?? 0), 0)
  const lpValuePnl = lpPositions.reduce((sum: number, p: any) => sum + ((p?.current_value_usd ?? 0) - (p?.deposit_usd ?? 0)), 0)
  const lpPnl = lpFees + lpValuePnl

  const fundingArb = d.strategies['funding_arb']
  const fundingPositions = fundingArb?.positions ?? []
  const fundingPnl = fundingPositions.reduce((sum: number, p: any) => sum + (p?.fees_earned_usd ?? 0), 0)

  const totalRealized = scalperRealized + lpFees + fundingPnl
  const totalUnrealized = scalperUnrealized + lpValuePnl
  const totalPnl = totalRealized + totalUnrealized
  const capital = driftAcct?.starting_capital ?? 199.04

  const uptimeHrs = Math.max(d.uptime_hours || 0.5, 0.5)
  const closedCount = (ds?.wins ?? 0) + (ds?.losses ?? 0)
  const hasData = closedCount >= 2 || lpFees > 0

  const totalPerHour = hasData ? totalPnl / uptimeHrs : 0

  const projDpy = hasData ? totalPerHour * 24 / capital * 100 : 0
  const projMpy = projDpy * 30
  const projApy = projDpy * 365


  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <Label>Drift Account</Label>
          <Value>{fmt(driftAcct?.net_value ?? 0)}</Value>
          <Sub positive={(driftAcct?.total_pnl ?? 0) >= 0}>
            {driftAcct ? `${(driftAcct.total_pnl >= 0 ? '+' : '')}${fmt(driftAcct.total_pnl)} from $199.04` : 'paper mode'}
          </Sub>
        </Card>
        <Card>
          <Label>Session PnL (All)</Label>
          <Value color={totalPnl >= 0 ? 'green' : 'red'}>{fmt(totalPnl)}</Value>
          <Sub positive={totalPnl >= 0}>
            scalper: {fmt(scalperPnl)} | LP: {fmt(lpPnl)} | funding: {fmt(fundingPnl)}
          </Sub>
        </Card>
        <Card>
          <Label>SOL Price</Label>
          <Value>{fmt(sol)}</Value>
          <Sub positive={(market.data?.sol_change_1h ?? 0) >= 0}>
            {market.data?.sol_change_1h?.toFixed(2) ?? '0'}% 1h
          </Sub>
        </Card>
        <Card>
          <Label>Market Regime</Label>
          <Value color={REGIME_COLORS[regime]}>{REGIME_LABELS[regime] ?? regime}</Value>
          <Sub>{((scalper.data?.regime_confidence ?? 0) * 100).toFixed(0)}% conf | {ds?.wins ?? 0}W/{ds?.losses ?? 0}L</Sub>
        </Card>
      </div>

      <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
        <div className="grid grid-cols-3 gap-6 sm:grid-cols-6">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj DPY</div>
            <div className={`font-mono text-sm font-bold ${projDpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {hasData ? `${projDpy >= 0 ? '+' : ''}${projDpy.toFixed(2)}%` : '--'}
            </div>
            {hasData && <div className="font-mono text-[10px] text-gray-600" style={{ fontVariantNumeric: 'tabular-nums' }}>
              r:{(totalRealized / uptimeHrs * 24 / capital * 100).toFixed(1)}% u:{(totalUnrealized / uptimeHrs * 24 / capital * 100).toFixed(1)}%
            </div>}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj MPY</div>
            <div className={`font-mono text-sm font-bold ${projMpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {hasData ? `${projMpy >= 0 ? '+' : ''}${projMpy.toFixed(1)}%` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj APY</div>
            <div className={`font-mono text-sm font-bold ${projApy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {hasData ? `${projApy >= 0 ? '+' : ''}${projApy.toFixed(0)}%` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">$/Hour</div>
            <div className={`font-mono text-sm font-bold ${totalPerHour >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {hasData ? `${totalPerHour >= 0 ? '+' : ''}${fmt(totalPerHour)}` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">%/Hour</div>
            <div className={`font-mono text-sm font-bold ${totalPerHour >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {hasData ? `${totalPerHour >= 0 ? '+' : ''}${(totalPerHour / capital * 100).toFixed(3)}%` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Uptime</div>
            <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {uptimeHrs.toFixed(1)}h
            </div>
          </div>
        </div>
      </div>

      <div>
        <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">Active Strategies</h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {enabled.map(s => <StrategyCard key={s.id} strategy={s} scalperData={scalper.data} />)}
        </div>
      </div>

      {activeTrades.length > 0 && (
        <div>
          <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">Open Positions</h2>
          <div className="rounded-lg border border-gray-800 bg-gray-900 overflow-hidden">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500">
                  <th className="px-3 py-2 text-left">Asset</th>
                  <th className="px-3 py-2 text-left">Direction</th>
                  <th className="px-3 py-2 text-left">Type</th>
                  <th className="px-3 py-2 text-right">PnL</th>
                  <th className="px-3 py-2 text-right">Size</th>
                </tr>
              </thead>
              <tbody>
                {activeTrades.map((t: any, i: number) => (
                  <tr key={i} className="border-b border-gray-800/50">
                    <td className="px-3 py-2 font-medium text-white">{t.asset}</td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                        t.direction === 'long' ? 'bg-green-500/15 text-green-400' : 'bg-red-500/15 text-red-400'
                      }`}>{t.direction.toUpperCase()}</span>
                    </td>
                    <td className="px-3 py-2 text-gray-400">{t.trade_type}</td>
                    <td className={`px-3 py-2 text-right font-mono ${t.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                    </td>
                    <td className="px-3 py-2 text-right font-mono text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                      ${t.collateral_usd?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="mb-2 text-xs text-gray-500">Allocation</div>
          <div className="space-y-2">
            {enabled.map(s => (
              <div key={s.id}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-gray-400">{s.name}</span>
                  <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(s.capital_allocated)}</span>
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
          <div className={`text-sm font-semibold capitalize ${
            d.risk_level === 'low' ? 'text-green-400' :
            d.risk_level === 'medium' ? 'text-yellow-400' :
            d.risk_level === 'high' ? 'text-orange-400' : 'text-red-400'
          }`}>{d.risk_level}</div>
          {d.circuit_breaker_active && (
            <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-400 border border-red-500/30">CB ACTIVE</span>
          )}
        </Link>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">
          <div className="mb-2 text-xs text-gray-500">Mode</div>
          <div className="text-sm font-semibold text-white">{d.mode?.toUpperCase() ?? 'PAPER'}</div>
          <div className="text-[10px] text-gray-500 mt-1">Uptime: {(d.uptime_hours ?? 0).toFixed(1)}h</div>
        </div>
      </div>
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-gray-800 bg-gray-900 p-4">{children}</div>
}
function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{children}</div>
}
function Value({ children, color }: { children: React.ReactNode; color?: string }) {
  const c = color === 'green' ? 'text-green-400' : color === 'red' ? 'text-red-400' : color ?? 'text-white'
  return <div className={`font-mono text-lg font-bold ${c}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{children}</div>
}
function Sub({ children, positive }: { children: React.ReactNode; positive?: boolean }) {
  const c = positive === true ? 'text-green-400' : positive === false ? 'text-red-400' : 'text-gray-500'
  return <div className={`font-mono text-xs mt-0.5 ${c}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{children}</div>
}

function StrategyCard({ strategy: s, scalperData }: { strategy: StrategyState; scalperData?: any }) {
  const isScalper = s.id === 'volatility_scalper'
  const isLP = s.id === 'leveraged_lp'
  const isFunding = s.id === 'funding_arb'
  const m = s.metrics as Record<string, any> ?? {}
  const link = isScalper ? '/scalper' : isLP ? '/lp' : '#'

  return (
    <Link to={link} className="rounded-lg border border-gray-800 bg-gray-900 p-4 hover:border-gray-700 transition-colors block">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-200">{s.name}</span>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
            s.status === 'active' ? 'bg-green-500/15 text-green-400' :
            s.status === 'idle' ? 'bg-gray-500/15 text-gray-400' :
            'bg-yellow-500/15 text-yellow-400'
          }`}>{s.status}</span>
        </div>
      </div>
      <div className="space-y-1 text-xs">
        {isScalper && (
          <>
            <Row label="Capital" value={`$${s.capital_allocated.toFixed(0)}`} />
            <Row label="Trades" value={`${scalperData?.daily_stats?.wins ?? 0}W / ${scalperData?.daily_stats?.losses ?? 0}L`} />
            <Row label="Active" value={`${scalperData?.active_trades?.length ?? 0} positions`} />
          </>
        )}
        {isLP && (
          <>
            <Row label="Capital" value={`$${s.capital_allocated.toFixed(0)}`} />
            <Row label="Pool APY" value={`${(m.pool_apy ?? 0).toFixed(0)}%`} />
            <Row label="Leverage" value={`${(m.leverage ?? 1).toFixed(1)}x`} />
            <Row label="Volatility" value={`${((m.volatility ?? 0) * 100).toFixed(3)}%`} />
          </>
        )}
        {isFunding && (
          <>
            <Row label="Capital" value={`$${s.capital_allocated.toFixed(0)}`} />
            <Row label="Funding APY" value={`${(m.funding_apy ?? 0).toFixed(1)}%`} />
            <Row label="Direction" value={m.funding_direction ?? 'neutral'} />
          </>
        )}
      </div>
    </Link>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-500">{label}</span>
      <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
