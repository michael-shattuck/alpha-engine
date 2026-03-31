import type { ScalperState } from '../api'

const REGIME_COLORS: Record<string, string> = {
  trending_up: 'text-green-400',
  trending_down: 'text-red-400',
  ranging: 'text-blue-400',
  volatile_ranging: 'text-yellow-400',
  dead: 'text-gray-500',
  unknown: 'text-gray-600',
}

const REGIME_LABELS: Record<string, string> = {
  trending_up: 'Trending Up',
  trending_down: 'Trending Down',
  ranging: 'Ranging',
  volatile_ranging: 'Volatile Ranging',
  dead: 'Dead',
  unknown: 'Unknown',
}

function formatUsd(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

interface Props {
  scalper: ScalperState | null
}

export default function ScalperPanel({ scalper }: Props) {
  if (!scalper) return null

  const ds = scalper.daily_stats
  const perf = scalper.signal_performance
  const regime = scalper.regime || 'unknown'
  const regimeColor = REGIME_COLORS[regime] ?? 'text-gray-600'
  const regimeLabel = REGIME_LABELS[regime] ?? regime

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Scalper</h2>

      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Regime</div>
          <div className={`text-sm font-semibold ${regimeColor}`}>{regimeLabel}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Confidence</div>
          <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {(scalper.regime_confidence * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-3 gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Daily PnL</div>
          <div className={`font-mono text-sm font-bold ${ds.daily_pnl_usd >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(ds.daily_pnl_usd)}
          </div>
          <div className={`font-mono text-[10px] ${ds.daily_pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {ds.daily_pnl_pct >= 0 ? '+' : ''}{ds.daily_pnl_pct.toFixed(2)}%
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Trades</div>
          <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {ds.trades_today}
          </div>
          <div className="font-mono text-[10px] text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {ds.wins}W / {ds.losses}L
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-600">Win Rate</div>
          <div className={`font-mono text-sm font-bold ${ds.win_rate >= 0.55 ? 'text-green-400' : ds.win_rate >= 0.45 ? 'text-yellow-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {(ds.win_rate * 100).toFixed(0)}%
          </div>
        </div>
      </div>

      {scalper.active_trades.length > 0 && (
        <div className="mb-4">
          <div className="mb-2 text-xs text-gray-500">Active Trades</div>
          <div className="space-y-1.5">
            {scalper.active_trades.map((trade) => (
              <div key={trade.id} className="rounded border border-gray-800 bg-gray-950/50 p-2">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className={`text-[10px] font-bold uppercase ${trade.direction === 'long' ? 'text-green-400' : 'text-red-400'}`}>
                      {trade.direction}
                    </span>
                    <span className="text-[10px] text-gray-500">{trade.trade_type}</span>
                    <span className="text-[10px] text-gray-600">{trade.leverage}x</span>
                  </div>
                  <span className={`font-mono text-xs font-bold ${trade.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {trade.pnl_pct >= 0 ? '+' : ''}{trade.pnl_pct.toFixed(2)}%
                  </span>
                </div>
                <div className="flex justify-between text-[10px] text-gray-500">
                  <span>Entry: ${trade.entry_price.toFixed(2)}</span>
                  <span>SL: ${trade.stop_loss.toFixed(2)}</span>
                  <span>TP: ${trade.take_profit.toFixed(2)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {scalper.active_trades.length === 0 && (
        <div className="mb-4 text-center text-[11px] text-gray-600 py-2">
          No active trades
        </div>
      )}

      {perf.total_signals > 0 && (
        <div className="border-t border-gray-800 pt-3">
          <div className="mb-2 text-xs text-gray-500">All-Time Performance</div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Signals</div>
              <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{perf.total_signals}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Win Rate</div>
              <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{(perf.win_rate * 100).toFixed(0)}%</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-600">Profit Factor</div>
              <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>{perf.profit_factor.toFixed(2)}</div>
            </div>
          </div>

          {Object.keys(perf.by_regime).length > 0 && (
            <div className="mt-2 space-y-1">
              {Object.entries(perf.by_regime).map(([regime, stats]) => (
                <div key={regime} className="flex items-center justify-between text-[10px]">
                  <span className={REGIME_COLORS[regime] ?? 'text-gray-500'}>{REGIME_LABELS[regime] ?? regime}</span>
                  <span className="font-mono text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {stats.count} trades, {(stats.win_rate * 100).toFixed(0)}% WR, {stats.avg_pnl.toFixed(2)}% avg
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="mt-3 border-t border-gray-800 pt-3">
        <div className="mb-1 text-xs text-gray-500">Key Indicators</div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
          {scalper.indicators && (
            <>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-600">RSI (5m)</span>
                <span className={`font-mono ${
                  (scalper.indicators.rsi_5m as number) < 30 ? 'text-green-400' :
                  (scalper.indicators.rsi_5m as number) > 70 ? 'text-red-400' : 'text-gray-400'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(scalper.indicators.rsi_5m as number)?.toFixed(1) ?? '--'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-600">ADX (1h)</span>
                <span className="font-mono text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(scalper.indicators.adx_1h as number)?.toFixed(1) ?? '--'}
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-600">BB Width</span>
                <span className="font-mono text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {((scalper.indicators.bb_width_1h as number) * 100)?.toFixed(2) ?? '--'}%
                </span>
              </div>
              <div className="flex justify-between text-[10px]">
                <span className="text-gray-600">Velocity</span>
                <span className={`font-mono ${
                  (scalper.indicators.velocity_5m as number) > 0.3 ? 'text-green-400' :
                  (scalper.indicators.velocity_5m as number) < -0.3 ? 'text-red-400' : 'text-gray-400'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(scalper.indicators.velocity_5m as number)?.toFixed(2) ?? '--'}%
                </span>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
