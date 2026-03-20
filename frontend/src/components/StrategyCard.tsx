import { useState } from 'react'
import type { StrategyState } from '../types'
import { toggleStrategy } from '../api'

function formatUsd(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

function formatPercent(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

const STATUS_STYLES: Record<string, string> = {
  active: 'bg-green-500/15 text-green-400 border-green-500/30',
  idle: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  error: 'bg-red-500/15 text-red-400 border-red-500/30',
}

function statusStyle(status: string): string {
  return STATUS_STYLES[status.toLowerCase()] ?? 'bg-gray-500/15 text-gray-400 border-gray-500/30'
}

interface Props {
  strategy: StrategyState
  onToggled: () => void
}

export default function StrategyCard({ strategy, onToggled }: Props) {
  const [toggling, setToggling] = useState(false)
  const pnlColor = strategy.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'
  const allocationPct = strategy.target_allocation * 100

  async function handleToggle() {
    setToggling(true)
    try {
      await toggleStrategy(strategy.id)
      onToggled()
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className={`rounded-lg border bg-gray-900 p-4 transition-colors ${
      strategy.enabled ? 'border-gray-800' : 'border-gray-800/50 opacity-60'
    }`}>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <h3 className="text-sm font-semibold text-gray-200">{strategy.name}</h3>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider ${statusStyle(strategy.status)}`}>
            {strategy.status}
          </span>
        </div>
        <button
          onClick={handleToggle}
          disabled={toggling}
          className={`relative h-5 w-9 rounded-full transition-colors ${
            strategy.enabled ? 'bg-green-600' : 'bg-gray-700'
          } ${toggling ? 'cursor-wait opacity-50' : 'cursor-pointer'}`}
          aria-label={strategy.enabled ? 'Disable strategy' : 'Enable strategy'}
        >
          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white transition-transform ${
            strategy.enabled ? 'left-[18px]' : 'left-0.5'
          }`} />
        </button>
      </div>

      <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-2">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Value</div>
          <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(strategy.current_value)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">P&L</div>
          <div className={`font-mono text-sm font-medium ${pnlColor}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(strategy.total_pnl)} ({formatPercent(strategy.total_pnl_percent)})
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Fees Earned</div>
          <div className="font-mono text-sm font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(strategy.total_fees)}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Positions</div>
          <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {strategy.position_count}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-gray-800 pt-2.5">
        <div className="text-[10px] uppercase tracking-wider text-gray-500">
          Allocation
        </div>
        <div className="flex items-center gap-2">
          <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-800">
            <div
              className="h-full rounded-full bg-blue-500 transition-all"
              style={{ width: `${Math.min(allocationPct, 100)}%` }}
            />
          </div>
          <span className="font-mono text-xs font-medium text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {allocationPct.toFixed(0)}%
          </span>
        </div>
      </div>

      {strategy.error && (
        <div className="mt-2.5 rounded border border-red-800/50 bg-red-950/30 px-2.5 py-1.5 text-xs text-red-400">
          {strategy.error}
        </div>
      )}
    </div>
  )
}
