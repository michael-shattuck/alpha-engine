import type { MarketData } from '../types'

function formatUsd(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

function formatPercent(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function volatilityLevel(v: number): string {
  if (v < 0.02) return 'low'
  if (v < 0.05) return 'medium'
  if (v < 0.10) return 'high'
  return 'extreme'
}

function trendSignal(change1h: number, change24h: number): string {
  const weighted = change1h * 0.6 + change24h * 0.4
  if (weighted > 1) return 'bullish'
  if (weighted < -1) return 'bearish'
  return 'neutral'
}

const TREND_STYLES: Record<string, string> = {
  bullish: 'text-green-400',
  bearish: 'text-red-400',
  neutral: 'text-gray-400',
}

const VOL_STYLES: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-orange-400',
  extreme: 'text-red-400',
}

interface Props {
  market: MarketData
}

export default function MarketPanel({ market }: Props) {
  const priceChangeColor = market.sol_change_24h >= 0 ? 'text-green-400' : 'text-red-400'
  const volLevel = volatilityLevel(market.volatility_24h)
  const trend = trendSignal(market.sol_change_1h, market.sol_change_24h)

  const poolEntries = Object.entries(market.pool_apys)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Market</h2>

      <div className="mb-4 space-y-3">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-gray-500">SOL/USD</span>
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-lg font-semibold" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {formatUsd(market.sol_price)}
            </span>
            <span className={`font-mono text-xs font-medium ${priceChangeColor}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {formatPercent(market.sol_change_24h)}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">Volatility</span>
          <span className={`text-sm font-medium capitalize ${VOL_STYLES[volLevel] ?? 'text-gray-400'}`}>
            {volLevel} ({(market.volatility_24h * 100).toFixed(1)}%)
          </span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">Trend</span>
          <span className={`text-sm font-medium capitalize ${TREND_STYLES[trend] ?? 'text-gray-400'}`}>
            {trend}
          </span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">JLP APY</span>
          <span className="font-mono text-sm font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {market.jlp_apy.toFixed(1)}%
          </span>
        </div>
      </div>

      {poolEntries.length > 0 && (
        <div className="border-t border-gray-800 pt-3">
          <div className="mb-2 text-[10px] uppercase tracking-wider text-gray-500">Top Pool APYs</div>
          <div className="space-y-1.5">
            {poolEntries.map(([pool, apy]) => (
              <div key={pool} className="flex items-center justify-between">
                <span className="max-w-[60%] truncate text-xs text-gray-400">
                  {pool.replace('orca_', '').replace(/_/g, '-').toUpperCase()}
                </span>
                <span className="font-mono text-xs font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {apy.toFixed(1)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
