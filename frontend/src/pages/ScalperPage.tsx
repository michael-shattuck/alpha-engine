import { useData } from '../DataContext'
import ScalperPanel from '../components/ScalperPanel'

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

  const ind = sc?.indicators ?? {}
  const ds = sc?.daily_stats ?? { trades_today: 0, wins: 0, losses: 0, daily_pnl_usd: 0, daily_pnl_pct: 0, win_rate: 0 }
  const perf = sc?.signal_performance ?? { total_signals: 0, win_rate: 0, profit_factor: 0, by_regime: {} }
  const regime = sc?.regime ?? 'unknown'
  const activeTrades = sc?.active_trades ?? []
  const unrealizedPnl = activeTrades.reduce((sum, t) => sum + (t.pnl_usd ?? 0), 0)
  const totalPnl = ds.daily_pnl_usd + unrealizedPnl

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-5">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Volatility Scalper</h2>
            <div className="flex items-center gap-2">
              <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                strategy.status === 'active' ? 'bg-green-500/15 text-green-400 border border-green-500/30' :
                strategy.status === 'watching' ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30' :
                'bg-gray-500/15 text-gray-400 border border-gray-500/30'
              }`}>{strategy.status}</span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-4">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Total PnL</div>
              <div className={`font-mono text-lg font-bold ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                ${totalPnl.toFixed(2)}
              </div>
              <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
                realized: ${ds.daily_pnl_usd.toFixed(2)} | open: ${unrealizedPnl.toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Active</div>
              <div className="font-mono text-lg font-bold text-blue-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {activeTrades.length}
              </div>
              <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
                of 7 assets
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Completed</div>
              <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{ds.trades_today}</div>
              <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>{ds.wins}W / {ds.losses}L</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Win Rate</div>
              <div className={`font-mono text-sm font-bold ${ds.win_rate >= 0.8 ? 'text-green-400' : ds.win_rate >= 0.6 ? 'text-yellow-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                {ds.trades_today > 0 ? `${(ds.win_rate * 100).toFixed(0)}%` : '--'}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Regime</div>
              <div className={`text-sm font-semibold ${REGIME_COLORS[regime] ?? 'text-gray-500'}`}>
                {REGIME_LABELS[regime] ?? regime}
              </div>
            </div>
          </div>
        </div>

        {(sc?.assets ?? []).length > 0 && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Tracked Assets</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-gray-600">
                    <th className="pb-2 pr-3">Asset</th>
                    <th className="pb-2 pr-3">Price</th>
                    <th className="pb-2 pr-3">Regime</th>
                    <th className="pb-2 pr-3">RSI</th>
                    <th className="pb-2 pr-3">Velocity</th>
                    <th className="pb-2 pr-3">Signal</th>
                    <th className="pb-2">Position</th>
                  </tr>
                </thead>
                <tbody>
                  {sc!.assets.map(a => (
                    <tr key={a.symbol} className="border-t border-gray-800/50">
                      <td className="py-2 pr-3 font-medium text-white">{a.symbol}</td>
                      <td className="py-2 pr-3 font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                        ${a.price > 1 ? a.price.toFixed(2) : a.price.toFixed(6)}
                      </td>
                      <td className="py-2 pr-3">
                        <span className={`${REGIME_COLORS[a.regime] ?? 'text-gray-500'}`}>
                          {REGIME_LABELS[a.regime] ?? a.regime}
                        </span>
                      </td>
                      <td className={`py-2 pr-3 font-mono ${
                        a.rsi_5m < 30 ? 'text-green-400 font-bold' :
                        a.rsi_5m > 70 ? 'text-red-400 font-bold' :
                        a.rsi_5m < 35 ? 'text-green-400' :
                        a.rsi_5m > 65 ? 'text-red-400' : 'text-gray-400'
                      }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {a.rsi_5m.toFixed(1)}
                      </td>
                      <td className={`py-2 pr-3 font-mono ${
                        a.velocity > 0.3 ? 'text-green-400' : a.velocity < -0.3 ? 'text-red-400' : 'text-gray-500'
                      }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {a.velocity >= 0 ? '+' : ''}{a.velocity.toFixed(2)}%
                      </td>
                      <td className="py-2 pr-3">
                        {a.signal !== 'no_signal' && a.signal !== 'none' ? (
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                            a.signal === 'long' ? 'bg-green-500/15 text-green-400' :
                            a.signal === 'short' ? 'bg-red-500/15 text-red-400' : 'text-gray-500'
                          }`}>
                            {a.signal.toUpperCase()} ({(a.signal_confidence * 100).toFixed(0)}%)
                          </span>
                        ) : (
                          <span className="text-gray-600 text-[10px]">{a.signal_reason.slice(0, 20)}</span>
                        )}
                      </td>
                      <td className="py-2">
                        {a.active_trade ? (
                          <span className={`font-mono text-[10px] font-bold ${
                            a.active_trade.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'
                          }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                            {a.active_trade.direction.toUpperCase()} {a.active_trade.pnl_pct >= 0 ? '+' : ''}{a.active_trade.pnl_pct.toFixed(2)}%
                          </span>
                        ) : (
                          <span className="text-gray-600 text-[10px]">--</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Active Trades</h3>
          {(sc?.active_trades ?? []).length > 0 ? (
            <div className="space-y-2">
              {sc!.active_trades.map(trade => (
                <div key={trade.id} className="rounded border border-gray-800 bg-gray-950/50 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-bold uppercase ${trade.direction === 'long' ? 'text-green-400' : 'text-red-400'}`}>{trade.direction}</span>
                      <span className="text-[10px] text-gray-500">{trade.trade_type}</span>
                      <span className="text-[10px] text-gray-600">{trade.leverage}x</span>
                    </div>
                    <span className={`font-mono text-sm font-bold ${trade.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct.toFixed(2)}%
                    </span>
                  </div>
                  <div className="grid grid-cols-4 gap-2 text-[10px]">
                    <div><span className="text-gray-600">Entry</span><br/><span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>${trade.entry_price.toFixed(2)}</span></div>
                    <div><span className="text-gray-600">Current</span><br/><span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>${trade.current_price.toFixed(2)}</span></div>
                    <div><span className="text-gray-600">SL</span><br/><span className="font-mono text-red-400" style={{ fontVariantNumeric: 'tabular-nums' }}>${trade.stop_loss.toFixed(2)}</span></div>
                    <div><span className="text-gray-600">TP</span><br/><span className="font-mono text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>${trade.take_profit.toFixed(2)}</span></div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-4 text-center text-sm text-gray-600">No active trades</div>
          )}
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Indicators</h3>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <IndRow label="RSI (5m)" value={n(ind.rsi_5m as number)} warn={(ind.rsi_5m as number) < 30 || (ind.rsi_5m as number) > 70} />
            <IndRow label="RSI (15m)" value={n(ind.rsi_15m as number)} />
            <IndRow label="ADX (1h)" value={n(ind.adx_1h as number)} />
            <IndRow label="BB Width" value={`${((ind.bb_width_1h as number) * 100)?.toFixed(2) ?? '0'}%`} />
            <IndRow label="EMA 9 (15m)" value={`$${n(ind.ema_9_15m as number)}`} />
            <IndRow label="EMA 21 (15m)" value={`$${n(ind.ema_21_15m as number)}`} />
            <IndRow label="ATR (1h)" value={`$${n(ind.atr_1h as number)}`} />
            <IndRow label="VWAP" value={`$${n(ind.vwap as number)}`} />
            <IndRow label="Velocity (5m)" value={`${n(ind.velocity_5m as number)}%`} warn={Math.abs(ind.velocity_5m as number) > 0.5} />
            <IndRow label="Accel (5m)" value={`${n(ind.acceleration_5m as number)}%`} />
            <IndRow label="+DI" value={n(ind.plus_di_1h as number)} />
            <IndRow label="-DI" value={n(ind.minus_di_1h as number)} />
          </div>
        </div>

        {perf.total_signals > 0 && (
          <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
            <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Signal Performance</h3>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Total Signals</div>
                <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{perf.total_signals}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Win Rate</div>
                <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{(perf.win_rate * 100).toFixed(0)}%</div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Profit Factor</div>
                <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>{perf.profit_factor.toFixed(2)}</div>
              </div>
            </div>
            {Object.keys(perf.by_regime).length > 0 && (
              <div className="space-y-1">
                {Object.entries(perf.by_regime).map(([r, stats]) => (
                  <div key={r} className="flex items-center justify-between text-xs">
                    <span className={REGIME_COLORS[r] ?? 'text-gray-500'}>{REGIME_LABELS[r] ?? r}</span>
                    <span className="font-mono text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                      {stats.count} trades, {(stats.win_rate * 100).toFixed(0)}% WR, {stats.avg_pnl.toFixed(2)}% avg
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="space-y-5">
        <ScalperPanel scalper={sc ?? null} />
      </div>
    </div>
  )
}

function n(v: number | undefined | null): string {
  if (v === undefined || v === null || isNaN(v)) return '--'
  return v.toFixed(1)
}

function IndRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex justify-between text-xs">
      <span className="text-gray-600">{label}</span>
      <span className={`font-mono ${warn ? 'text-yellow-400' : 'text-gray-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</span>
    </div>
  )
}
