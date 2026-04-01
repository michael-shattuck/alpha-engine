import { useData } from '../DataContext'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}
function n(v: number | undefined | null): string {
  if (v === undefined || v === null || isNaN(v)) return '--'
  return v.toFixed(1)
}

const REGIME_LABELS: Record<string, string> = {
  trending_up: 'Trending Up', trending_down: 'Trending Down',
  ranging: 'Ranging', volatile_ranging: 'Volatile Ranging',
  dead: 'Dead', unknown: 'Warming Up',
}
const REGIME_COLORS: Record<string, string> = {
  trending_up: 'text-green-400', trending_down: 'text-red-400',
  ranging: 'text-blue-400', volatile_ranging: 'text-yellow-400',
  dead: 'text-gray-500', unknown: 'text-gray-600',
}

export default function ScalperPage() {
  const { status, scalper } = useData()
  const d = status.data
  const sc = scalper.data
  if (!d) return null

  const strategy = d.strategies['volatility_scalper']
  if (!strategy) return <div className="text-gray-500 text-sm">Scalper strategy not found</div>

  const ds = sc?.daily_stats ?? { trades_today: 0, wins: 0, losses: 0, daily_pnl_usd: 0, daily_pnl_pct: 0, win_rate: 0 }
  const regime = sc?.regime ?? 'unknown'
  const activeTrades = sc?.active_trades ?? []
  const driftAcct = sc?.drift_account
  const ind = sc?.indicators ?? {}

  const unrealizedPnl = activeTrades.reduce((sum, t) => sum + (t.pnl_usd ?? 0), 0)
  const sessionPnl = ds.daily_pnl_usd + unrealizedPnl
  const capital = strategy.capital_allocated || 199

  const closedCount = ds.wins + ds.losses
  const uptimeHrs = Math.max(d.uptime_hours || 0.5, 0.5)
  const pnlPerHour = closedCount >= 2 ? sessionPnl / uptimeHrs : 0
  const projDpy = pnlPerHour * 24 / capital * 100
  const projMpy = projDpy * 30
  const projApy = projDpy * 365

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Volatility Scalper</h2>
            <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
              strategy.status === 'active' ? 'bg-green-500/15 text-green-400 border border-green-500/30' :
              strategy.status === 'watching' ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30' :
              'bg-gray-500/15 text-gray-400 border border-gray-500/30'
            }`}>{strategy.status}</span>
            <span className={`text-sm font-semibold ${REGIME_COLORS[regime] ?? 'text-gray-500'}`}>
              {REGIME_LABELS[regime] ?? regime}
            </span>
          </div>
          <div className="text-right text-xs text-gray-500">
            {((sc?.regime_confidence ?? 0) * 100).toFixed(0)}% conf | ${capital.toFixed(0)} capital
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-4">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Session PnL</div>
            <div className={`font-mono text-lg font-bold ${sessionPnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {fmt(sessionPnl)}
            </div>
            <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
              realized: {fmt(ds.daily_pnl_usd)} | open: {fmt(unrealizedPnl)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Trades</div>
            <div className="font-mono text-lg font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {activeTrades.length} / {closedCount}
            </div>
            <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {closedCount > 0 ? `${ds.wins}W/${ds.losses}L (${(ds.win_rate * 100).toFixed(0)}%)` : 'no closes yet'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj Daily</div>
            <div className={`font-mono text-lg font-bold ${projDpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {closedCount >= 2 ? `${projDpy >= 0 ? '+' : ''}${projDpy.toFixed(2)}%` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj Monthly</div>
            <div className={`font-mono text-lg font-bold ${projMpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {closedCount >= 2 ? `${projMpy >= 0 ? '+' : ''}${projMpy.toFixed(1)}%` : '--'}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-600">Proj Annual</div>
            <div className={`font-mono text-lg font-bold ${projApy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {closedCount >= 2 ? `${projApy >= 0 ? '+' : ''}${projApy.toFixed(0)}%` : '--'}
            </div>
          </div>
        </div>

        {driftAcct && (
          <div className="rounded border border-gray-800 bg-gray-950/50 p-3 text-xs">
            <span className="text-gray-500">Drift Account: </span>
            <span className="font-mono text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{fmt(driftAcct.net_value)}</span>
            <span className={`font-mono ml-2 ${driftAcct.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              ({driftAcct.total_pnl >= 0 ? '+' : ''}{fmt(driftAcct.total_pnl)} from $199.04)
            </span>
          </div>
        )}
      </div>

      {activeTrades.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Active Trades ({activeTrades.length})</h3>
          <div className="space-y-3">
            {activeTrades.map((trade) => {
              const t = trade as any
              const pf = t.entry_price > 1 ? 2 : 6
              const age = Math.max(0, (Date.now() / 1000 - t.opened_at) / 60)
              return (
                <div key={t.id} className={`rounded border p-3 ${
                  t.pnl_pct >= 0 ? 'border-green-800/30 bg-green-950/10' : 'border-red-800/30 bg-red-950/10'
                }`}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${
                        t.direction === 'long' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                      }`}>{t.direction}</span>
                      <span className="text-sm font-medium text-white">{t.asset}</span>
                      <span className="rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400">{t.trade_type}</span>
                      <span className="text-[10px] text-gray-600">{age.toFixed(0)}m</span>
                    </div>
                    <div className={`font-mono text-sm font-bold ${t.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                    </div>
                  </div>
                  <div className="flex justify-between text-[10px] text-gray-500 font-mono" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    <span>Entry: ${t.entry_price.toFixed(pf)}</span>
                    <span>Current: ${t.current_price.toFixed(pf)}</span>
                    <span className="text-red-400">SL: ${t.stop_loss.toFixed(pf)}</span>
                    <span>Size: ${t.collateral_usd?.toFixed(0)}</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {(sc?.assets ?? []).length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Asset Scanner</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-gray-600">
                  <th className="pb-2 pr-3">Asset</th>
                  <th className="pb-2 pr-3">Price</th>
                  <th className="pb-2 pr-3">Regime</th>
                  <th className="pb-2 pr-3">Signal</th>
                  <th className="pb-2">Position</th>
                </tr>
              </thead>
              <tbody>
                {sc!.assets.map(a => (
                  <tr key={a.symbol} className="border-t border-gray-800/50">
                    <td className="py-2 pr-3 font-medium text-white">{a.symbol}</td>
                    <td className="py-2 pr-3 font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                      ${a.price > 1 ? a.price.toFixed(2) : a.price > 0.01 ? a.price.toFixed(4) : a.price.toFixed(8)}
                    </td>
                    <td className="py-2 pr-3">
                      <span className={REGIME_COLORS[a.regime] ?? 'text-gray-500'}>
                        {REGIME_LABELS[a.regime] ?? a.regime}
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      {a.signal !== 'no_signal' && a.signal !== 'none' ? (
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          a.signal === 'long' ? 'bg-green-500/15 text-green-400' :
                          a.signal === 'short' ? 'bg-red-500/15 text-red-400' : 'text-gray-500'
                        }`}>{a.signal.toUpperCase()}</span>
                      ) : (
                        <span className="text-gray-600 text-[10px]">
                          {a.signal_reason?.includes('BLOCKED') ? 'BLOCKED' : a.signal_reason?.slice(0, 25) ?? '--'}
                        </span>
                      )}
                    </td>
                    <td className="py-2">
                      {a.active_trade ? (
                        <span className={`font-mono text-[10px] font-bold ${
                          a.active_trade.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'
                        }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                          {a.active_trade.direction.toUpperCase()} {a.active_trade.pnl_pct >= 0 ? '+' : ''}{a.active_trade.pnl_pct.toFixed(2)}%
                        </span>
                      ) : <span className="text-gray-600 text-[10px]">--</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Key Indicators (SOL)</h3>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          <IndRow label="RSI (5m)" value={n(ind.rsi_5m as number)} warn={(ind.rsi_5m as number) < 30 || (ind.rsi_5m as number) > 70} />
          <IndRow label="RSI (15m)" value={n(ind.rsi_15m as number)} />
          <IndRow label="ADX (1h)" value={n(ind.adx_1h as number)} />
          <IndRow label="BB Width" value={`${((ind.bb_width_1h as number) * 100)?.toFixed(2) ?? '0'}%`} />
          <IndRow label="EMA 9/21 (15m)" value={`${n(ind.ema_9_15m as number)} / ${n(ind.ema_21_15m as number)}`} />
          <IndRow label="Velocity" value={`${n(ind.velocity_5m as number)}%`} warn={Math.abs(ind.velocity_5m as number) > 0.5} />
          <IndRow label="+DI / -DI" value={`${n(ind.plus_di_1h as number)} / ${n(ind.minus_di_1h as number)}`} />
          <IndRow label="ATR (1h)" value={`$${n(ind.atr_1h as number)}`} />
        </div>
      </div>
    </div>
  )
}

function IndRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-gray-600">{label}</span>
      <span className={`font-mono ${warn ? 'text-yellow-400' : 'text-gray-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
