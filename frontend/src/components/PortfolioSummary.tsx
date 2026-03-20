import type { PortfolioStatus } from '../types'

const RISK_COLORS: Record<string, string> = {
  low: 'bg-green-500',
  medium: 'bg-yellow-500',
  high: 'bg-orange-500',
  critical: 'bg-red-500',
}

const RISK_TEXT_COLORS: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-orange-400',
  critical: 'text-red-400',
}

function formatUsd(value: number, minDecimals = 2): string {
  const abs = Math.abs(value)
  const decimals = abs < 0.01 ? 6 : abs < 1 ? 4 : minDecimals
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

function formatPercent(value: number): string {
  const abs = Math.abs(value)
  const decimals = abs < 0.01 ? 4 : abs < 1 ? 2 : 2
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(decimals)}%`
}

function formatUptime(hours: number): string {
  const days = Math.floor(hours / 24)
  const h = Math.floor(hours % 24)
  const m = Math.floor((hours % 1) * 60)
  if (days > 0) return `${days}d ${h}h`
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

interface Props {
  status: PortfolioStatus
}

export default function PortfolioSummary({ status }: Props) {
  const pnlColor = status.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'
  const riskColor = RISK_COLORS[status.risk_level] ?? 'bg-gray-500'
  const riskTextColor = RISK_TEXT_COLORS[status.risk_level] ?? 'text-gray-400'
  const drawdownPercent = status.total_pnl < 0
    ? Math.abs(status.total_pnl / status.capital) * 100
    : 0

  const walletAddress = '9bEXDoAfx3WmxS348DFQuTFjJ6ygSh3Q4cEQZWr2f7Kx'

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Portfolio</h2>
        <div className="flex items-center gap-3">
          <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase tracking-wider ${
            status.mode === 'live' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'
          }`}>
            {status.mode}
          </span>
          <span className="font-mono text-xs text-gray-500">
            {formatUptime(status.uptime_hours)}
          </span>
        </div>
      </div>

      <div className="mb-5">
        <div className="font-mono text-3xl font-bold tracking-tight" style={{ fontVariantNumeric: 'tabular-nums' }}>
          {formatUsd(status.total_value)}
        </div>
        <div className={`mt-1 font-mono text-lg font-semibold ${pnlColor}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
          {formatUsd(status.total_pnl)} ({formatPercent(status.total_pnl_percent)})
        </div>
      </div>

      <div className="mb-4 grid grid-cols-3 gap-4 rounded-lg border border-gray-800 bg-gray-950/50 p-4">
        <div>
          <div className="mb-1 text-xs text-gray-500">Projected Daily</div>
          <div className={`font-mono text-xl font-bold ${status.projected_dpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatPercent(status.projected_dpy)}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs text-gray-500">Projected Monthly</div>
          <div className={`font-mono text-xl font-bold ${status.projected_mpy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatPercent(status.projected_mpy)}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs text-gray-500">Projected Annual</div>
          <div className={`font-mono text-xl font-bold ${status.projected_apy >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatPercent(status.projected_apy)}
          </div>
        </div>
      </div>

      <div className="mb-4 grid grid-cols-4 gap-4">
        <div>
          <div className="mb-1 text-xs text-gray-500">SOL Price</div>
          <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(status.sol_price)}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs text-gray-500">Fees Earned</div>
          <div className="font-mono text-sm font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(status.total_fees)}
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs text-gray-500">Risk Level</div>
          <div className="flex items-center gap-2">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${riskColor}`} />
            <span className={`text-sm font-medium capitalize ${riskTextColor}`}>
              {status.risk_level}
            </span>
          </div>
        </div>
        <div>
          <div className="mb-1 text-xs text-gray-500">Drawdown</div>
          <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
            <span className={drawdownPercent > 5 ? 'text-red-400' : 'text-gray-300'}>
              {drawdownPercent.toFixed(2)}%
            </span>
          </div>
        </div>
      </div>

      <div className="rounded border border-gray-800 bg-gray-950/50 p-3">
        <div className="mb-2 text-[10px] uppercase tracking-wider text-gray-500">Verify On-Chain</div>
        <div className="flex flex-wrap gap-3 text-xs">
          <a href={`https://solscan.io/account/${walletAddress}`} target="_blank" rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 underline">
            Solscan Wallet
          </a>
          <a href={`https://solana.fm/address/${walletAddress}`} target="_blank" rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 underline">
            Solana FM
          </a>
        </div>
      </div>

      {status.circuit_breaker_active && (
        <div className="mt-4 rounded border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-400">
          Circuit breaker flagged -- monitoring only, no auto-close
        </div>
      )}
    </div>
  )
}
