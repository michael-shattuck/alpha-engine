import type { PortfolioStatus } from '../types'

function formatUsd(value: number): string {
  const abs = Math.abs(value)
  const decimals = abs < 0.01 ? 6 : abs < 1 ? 4 : 2
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: decimals, maximumFractionDigits: decimals })
}

interface Props {
  status: PortfolioStatus
}

export default function PositionDetail({ status }: Props) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const positions: any[] = Object.values(status.strategies)
    .flatMap(s => ((s.positions ?? []) as any[]).map((p: any) => ({ ...p, strategyName: s.name })))
    .filter((p: any) => p.metadata)

  if (positions.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">LP Positions</h2>
        <div className="py-4 text-center text-sm text-gray-600">No active positions</div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">LP Positions</h2>
      <div className="space-y-4">
        {positions.map((pos, i) => {
          const meta = pos.metadata as Record<string, unknown>
          const mint = meta?.position_mint as string
          const inRange = pos.in_range as boolean
          const lower = pos.lower_price as number
          const upper = pos.upper_price as number
          const entry = pos.entry_price as number
          const value = pos.current_value_usd as number
          const deposit = pos.deposit_usd as number
          const fees = pos.fees_earned_usd as number
          const solAmount = pos.sol_amount as number
          const usdcAmount = pos.usdc_amount as number
          const rangePct = meta?.range_pct as number

          return (
            <div key={i} className="rounded border border-gray-800 bg-gray-950/50 p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className="text-sm font-medium text-gray-200">{pos.strategyName as string}</span>
                <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                  inRange
                    ? 'bg-green-500/15 text-green-400 border border-green-500/30'
                    : 'bg-red-500/15 text-red-400 border border-red-500/30'
                }`}>
                  {inRange ? 'In Range' : 'Out of Range'}
                </span>
              </div>

              <div className="mb-3 grid grid-cols-2 gap-3">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">Deposited</div>
                  <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatUsd(deposit)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">Current Value</div>
                  <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatUsd(value)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">Fees Earned</div>
                  <div className="font-mono text-sm font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatUsd(fees)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">Entry Price</div>
                  <div className="font-mono text-sm font-medium" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {formatUsd(entry)}
                  </div>
                </div>
              </div>

              <div className="mb-3">
                <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Price Range (+/-{rangePct ? (rangePct * 100).toFixed(1) : '?'}%)</div>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>${lower?.toFixed(2)}</span>
                  <div className="flex-1 h-2 bg-gray-800 rounded-full overflow-hidden relative">
                    {lower && upper && status.sol_price && (
                      <div
                        className={`absolute top-0 h-full w-1.5 rounded-full ${inRange ? 'bg-green-500' : 'bg-red-500'}`}
                        style={{
                          left: `${Math.max(0, Math.min(100, ((status.sol_price - lower) / (upper - lower)) * 100))}%`,
                        }}
                      />
                    )}
                  </div>
                  <span className="font-mono text-xs text-gray-400" style={{ fontVariantNumeric: 'tabular-nums' }}>${upper?.toFixed(2)}</span>
                </div>
              </div>

              <div className="mb-3 grid grid-cols-2 gap-3">
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">SOL in Position</div>
                  <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {solAmount?.toFixed(6)} SOL
                  </div>
                </div>
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">USDC in Position</div>
                  <div className="font-mono text-xs text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {usdcAmount?.toFixed(6)} USDC
                  </div>
                </div>
              </div>

              {mint && (
                <div className="flex items-center justify-between border-t border-gray-800 pt-2">
                  <span className="text-[10px] text-gray-600">Position NFT</span>
                  <a
                    href={`https://solscan.io/token/${mint}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-[10px] text-blue-400 hover:text-blue-300 underline"
                  >
                    {mint.slice(0, 12)}...{mint.slice(-6)}
                  </a>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
