import type { LifecycleState, OptimizerState } from '../api'

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  idle: { label: 'Idle', color: 'text-gray-400' },
  close_lp: { label: 'Closing LP', color: 'text-yellow-400' },
  swap_for_repay: { label: 'Swapping for Repay', color: 'text-yellow-400' },
  repay_withdraw: { label: 'Repaying Loan', color: 'text-yellow-400' },
  deposit_collateral: { label: 'Depositing Collateral', color: 'text-blue-400' },
  borrow: { label: 'Borrowing USDC', color: 'text-blue-400' },
  swap_for_lp: { label: 'Swapping for LP', color: 'text-blue-400' },
  open_lp: { label: 'Opening LP', color: 'text-blue-400' },
  active: { label: 'Active', color: 'text-green-400' },
  failed: { label: 'FAILED', color: 'text-red-400' },
}

interface Props {
  lifecycle: LifecycleState | null
  optimizer: OptimizerState | null
}

export default function EnginePanel({ lifecycle, optimizer }: Props) {
  const phase = lifecycle?.phase ?? 'idle'
  const phaseInfo = PHASE_LABELS[phase] ?? PHASE_LABELS['idle']!

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Engine</h2>

      <div className="mb-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-gray-500">Lifecycle Phase</span>
          <span className={`text-xs font-semibold ${phaseInfo.color}`}>
            {phaseInfo.label}
          </span>
        </div>
        {lifecycle && phase !== 'idle' && phase !== 'active' && (
          <div className="mb-2 rounded bg-gray-950/50 p-2">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-yellow-400 animate-pulse" />
              <span className="text-[10px] text-gray-400">
                Processing step {lifecycle.retries > 0 ? `(retry ${lifecycle.retries})` : ''}
              </span>
            </div>
          </div>
        )}
        {lifecycle?.error && phase === 'failed' && (
          <div className="rounded border border-red-800/50 bg-red-950/30 p-2">
            <div className="text-[10px] text-red-400 break-all">{lifecycle.error}</div>
          </div>
        )}
      </div>

      {optimizer && (
        <>
          <div className="mb-4 border-t border-gray-800 pt-3">
            <div className="mb-2 text-xs text-gray-500">Optimizer</div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Target Monthly</div>
                <div className={`font-mono text-sm font-bold ${
                  optimizer.optimized.monthly >= optimizer.return_floor ? 'text-green-400' : 'text-red-400'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.optimized.monthly.toFixed(1)}%
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Floor</div>
                <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.return_floor}%
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Opt. Leverage</div>
                <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.optimized.leverage.toFixed(1)}x
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Opt. Range</div>
                <div className="font-mono text-sm text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(optimizer.optimized.range_pct * 100).toFixed(1)}%
                </div>
              </div>
            </div>
          </div>

          <div className="mb-4">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Pool APY</div>
                <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.current_pool_apy.toFixed(1)}%
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Actual Fee APY</div>
                <div className={`font-mono text-xs ${
                  optimizer.actual_fee_apy > 0 ? 'text-green-400' : 'text-gray-500'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.actual_fee_apy > 0 ? `${optimizer.actual_fee_apy.toFixed(0)}%` : 'Measuring...'}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Volatility</div>
                <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(optimizer.volatility * 100).toFixed(3)}%
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Trend (1h)</div>
                <div className={`font-mono text-xs ${
                  optimizer.trend_1h > 0.001 ? 'text-green-400' : optimizer.trend_1h < -0.001 ? 'text-red-400' : 'text-gray-400'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.trend_1h >= 0 ? '+' : ''}{(optimizer.trend_1h * 100).toFixed(2)}%
                </div>
              </div>
            </div>
          </div>

          <div className="mb-3">
            <div className="grid grid-cols-2 gap-2">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Rebal Cost/yr</div>
                <div className="font-mono text-xs text-yellow-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.optimized.rebalance_cost.toFixed(0)}%
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Rebal/day</div>
                <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {optimizer.optimized.rebalances_per_day.toFixed(1)}
                </div>
              </div>
            </div>
          </div>

          {optimizer.ranked_pools.length > 0 && (
            <div className="border-t border-gray-800 pt-3">
              <div className="mb-2 text-xs text-gray-500">Pool Rankings</div>
              <div className="space-y-1">
                {optimizer.ranked_pools.map((pool, i) => (
                  <div key={pool.pool} className="flex items-center justify-between rounded bg-gray-950/50 px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <span className={`text-[10px] font-bold ${i === 0 ? 'text-green-400' : 'text-gray-500'}`}>
                        #{i + 1}
                      </span>
                      <span className="text-xs text-gray-300">{pool.pool.replace('orca_', '').replace(/_/g, '-').toUpperCase()}</span>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-[10px] text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {pool.pool_apy.toFixed(0)}% APY
                      </div>
                      <div className={`font-mono text-[10px] font-medium ${
                        pool.monthly >= 30 ? 'text-green-400' : 'text-yellow-400'
                      }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {pool.monthly.toFixed(1)}%/mo
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
