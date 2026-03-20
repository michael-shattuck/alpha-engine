import type { PortfolioStatus } from '../types'

const RISK_LEVELS = ['low', 'medium', 'high', 'critical'] as const

const RISK_COLORS: Record<string, { bar: string; text: string; glow: string }> = {
  low: { bar: 'bg-green-500', text: 'text-green-400', glow: 'shadow-green-500/20' },
  medium: { bar: 'bg-yellow-500', text: 'text-yellow-400', glow: 'shadow-yellow-500/20' },
  high: { bar: 'bg-orange-500', text: 'text-orange-400', glow: 'shadow-orange-500/20' },
  critical: { bar: 'bg-red-500', text: 'text-red-400', glow: 'shadow-red-500/20' },
}

interface Props {
  status: PortfolioStatus
}

export default function RiskPanel({ status }: Props) {
  const riskIndex = RISK_LEVELS.indexOf(status.risk_level as typeof RISK_LEVELS[number])
  const colors = RISK_COLORS[status.risk_level] ?? RISK_COLORS['low']!

  const strategies = Object.values(status.strategies)
  const drawdownPercent = status.total_pnl < 0
    ? Math.min(Math.abs(status.total_pnl / status.capital) * 100, 100)
    : 0

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Risk</h2>

      <div className="mb-5">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-gray-500">Risk Level</span>
          <span className={`text-sm font-semibold capitalize ${colors.text}`}>
            {status.risk_level}
          </span>
        </div>
        <div className="flex gap-1">
          {RISK_LEVELS.map((level, i) => (
            <div
              key={level}
              className={`h-2 flex-1 rounded-sm transition-all ${
                i <= riskIndex
                  ? `${colors.bar} ${colors.glow} shadow-sm`
                  : 'bg-gray-800'
              }`}
            />
          ))}
        </div>
      </div>

      <div className="mb-5">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-gray-500">Drawdown</span>
          <span className="font-mono text-xs font-medium text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {drawdownPercent.toFixed(2)}%
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-gray-800">
          <div
            className={`h-full rounded-full transition-all ${
              drawdownPercent > 10 ? 'bg-red-500' : drawdownPercent > 5 ? 'bg-orange-500' : 'bg-yellow-500'
            }`}
            style={{ width: `${drawdownPercent}%` }}
          />
        </div>
        <div className="mt-1 flex justify-between text-[10px] text-gray-600">
          <span>0%</span>
          <span>5%</span>
          <span>10%</span>
        </div>
      </div>

      <div className="mb-4">
        <div className="mb-2 text-xs text-gray-500">Strategy Concentration</div>
        <div className="space-y-1.5">
          {strategies
            .filter(s => s.enabled)
            .sort((a, b) => b.target_allocation - a.target_allocation)
            .map((s) => (
              <div key={s.id} className="flex items-center gap-2">
                <span className="w-24 truncate text-xs text-gray-400">{s.name}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-800">
                  <div
                    className="h-full rounded-full bg-blue-500 transition-all"
                    style={{ width: `${s.target_allocation * 100}%` }}
                  />
                </div>
                <span className="font-mono text-[10px] text-gray-500 w-8 text-right" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(s.target_allocation * 100).toFixed(0)}%
                </span>
              </div>
            ))}
        </div>
      </div>

      {status.circuit_breaker_active && (
        <div className="rounded border border-red-800 bg-red-950/50 px-3 py-2">
          <div className="text-xs font-semibold text-red-400">Circuit Breaker Active</div>
          <div className="mt-0.5 text-[10px] text-red-500">
            All trading halted. Manual review required.
          </div>
        </div>
      )}

      {!status.circuit_breaker_active && status.risk_level === 'high' && (
        <div className="rounded border border-orange-800/50 bg-orange-950/30 px-3 py-2">
          <div className="text-xs font-semibold text-orange-400">Elevated Risk</div>
          <div className="mt-0.5 text-[10px] text-orange-500">
            Consider reducing exposure or tightening ranges.
          </div>
        </div>
      )}

      {!status.circuit_breaker_active && status.risk_level === 'critical' && (
        <div className="rounded border border-red-800/50 bg-red-950/30 px-3 py-2">
          <div className="text-xs font-semibold text-red-400">Critical Risk</div>
          <div className="mt-0.5 text-[10px] text-red-500">
            Near circuit breaker threshold. Immediate attention required.
          </div>
        </div>
      )}
    </div>
  )
}
