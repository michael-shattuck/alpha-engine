import { useData } from '../DataContext'
import { usePolling } from '../hooks/usePolling'
import { fetchPortfolio } from '../api'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}

const MARKET_NAMES: Record<number, string> = {
  0: 'SOL', 1: 'BTC', 2: 'ETH', 9: 'SUI', 18: 'PYTH', 20: 'JTO', 21: 'SEI', 24: 'JUP', 27: 'W',
}

export default function PortfolioPage() {
  const { status } = useData()
  const portfolio = usePolling(fetchPortfolio, 10000)
  const p = portfolio.data

  if (!p) return <div className="text-gray-500 text-sm">Loading portfolio...</div>

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Total Portfolio</div>
          <div className="font-mono text-2xl font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {fmt(p.total_usd)}
          </div>
          <div className="font-mono text-xs text-gray-500 mt-1" style={{ fontVariantNumeric: 'tabular-nums' }}>
            SOL @ {fmt(p.sol_price)}
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Wallet</div>
          <div className="font-mono text-2xl font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {fmt(p.wallet.total_usd)}
          </div>
          <div className="font-mono text-xs text-gray-500 mt-1" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {((p.wallet.total_usd / p.total_usd) * 100).toFixed(0)}% of total
          </div>
        </div>
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Drift Account</div>
          <div className="font-mono text-2xl font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {fmt(p.drift.collateral)}
          </div>
          <div className="font-mono text-xs text-gray-500 mt-1" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {((p.drift.collateral / p.total_usd) * 100).toFixed(0)}% of total
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Wallet Balances</h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between rounded bg-gray-950/50 p-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-purple-500/20 flex items-center justify-center text-xs font-bold text-purple-400">S</div>
                <div>
                  <div className="text-sm font-medium text-white">SOL</div>
                  <div className="text-[10px] text-gray-500">Solana</div>
                </div>
              </div>
              <div className="text-right">
                <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {p.wallet.sol_balance.toFixed(4)} SOL
                </div>
                <div className="font-mono text-xs text-gray-500" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(p.wallet.sol_usd)}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between rounded bg-gray-950/50 p-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center text-xs font-bold text-green-400">$</div>
                <div>
                  <div className="text-sm font-medium text-white">USDC</div>
                  <div className="text-[10px] text-gray-500">USD Coin</div>
                </div>
              </div>
              <div className="text-right">
                <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(p.wallet.usdc_balance)}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between border-t border-gray-800 pt-3">
              <span className="text-xs text-gray-500">Wallet Total</span>
              <span className="font-mono text-sm font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {fmt(p.wallet.total_usd)}
              </span>
            </div>
          </div>
        </div>

        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Drift Account</h2>
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-3">
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Collateral</div>
                <div className="font-mono text-sm font-medium text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(p.drift.collateral)}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Free</div>
                <div className="font-mono text-sm font-medium text-green-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(p.drift.free_collateral)}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase tracking-wider text-gray-600">Unrealized PnL</div>
                <div className={`font-mono text-sm font-medium ${p.drift.unrealized_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {p.drift.unrealized_pnl >= 0 ? '+' : ''}{fmt(p.drift.unrealized_pnl)}
                </div>
              </div>
            </div>

            {p.drift.positions.length > 0 ? (
              <div>
                <div className="mb-2 text-xs text-gray-500">Open Positions</div>
                <div className="space-y-2">
                  {p.drift.positions.map((pos, i) => (
                    <div key={i} className="flex items-center justify-between rounded bg-gray-950/50 p-2">
                      <div className="flex items-center gap-2">
                        <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
                          pos.direction === 'long' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'
                        }`}>{pos.direction}</span>
                        <span className="text-xs font-medium text-white">
                          {MARKET_NAMES[pos.market_index] ?? `#${pos.market_index}`}
                        </span>
                        <span className="text-[10px] text-gray-500">{(pos.size_tokens ?? pos.size ?? 0).toFixed(4)}</span>
                        <span className="text-[10px] text-gray-600">${(pos.notional ?? 0).toFixed(2)}</span>
                      </div>
                      <div className={`font-mono text-xs font-bold ${(pos.pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                        {(pos.pnl ?? 0) >= 0 ? '+' : ''}{fmt(pos.pnl ?? 0)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="text-center text-[11px] text-gray-600 py-2">No open Drift positions</div>
            )}

            <div className="flex items-center justify-between border-t border-gray-800 pt-3">
              <span className="text-xs text-gray-500">Drift Total</span>
              <span className="font-mono text-sm font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {fmt(p.drift.collateral + p.drift.unrealized_pnl)}
              </span>
            </div>
          </div>
        </div>
      </div>

      {status.data && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Strategy Allocation</h2>
          <div className="space-y-2">
            {Object.values(status.data.strategies).filter((s: { capital_allocated: number }) => s.capital_allocated > 0).map((s: { id: string; name: string; capital_allocated: number; current_value: number; status: string; total_pnl_percent: number }) => (
              <div key={s.id} className="flex items-center justify-between rounded bg-gray-950/50 p-3">
                <div>
                  <span className="text-sm font-medium text-white">{s.name}</span>
                  <span className={`ml-2 rounded px-1.5 py-0.5 text-[10px] ${
                    s.status === 'active' ? 'bg-green-500/15 text-green-400' : 'bg-gray-500/15 text-gray-400'
                  }`}>{s.status}</span>
                </div>
                <div className="text-right">
                  <div className="font-mono text-sm text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {fmt(s.current_value)} / {fmt(s.capital_allocated)}
                  </div>
                  <div className={`font-mono text-[10px] ${s.total_pnl_percent >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                    {s.total_pnl_percent >= 0 ? '+' : ''}{s.total_pnl_percent.toFixed(2)}%
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
