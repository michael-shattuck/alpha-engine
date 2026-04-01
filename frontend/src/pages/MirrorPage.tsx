import { useData } from '../DataContext'
import { useEffect, useState } from 'react'

function fmt(v: number, d = 2): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: d, maximumFractionDigits: d })
}

interface MirrorData {
  sse_connected: boolean
  sse_stats: { connects: number; disconnects: number; events_received: number; last_event_time: number; errors: number }
  wallet_cache_size: number
  active_trades: any[]
  trade_log: any[]
  signal_log: any[]
  tier_stats: Record<string, { trades: number; wins: number; pnl: number }>
  daily_stats: { trades_today: number; wins: number; losses: number; daily_pnl_usd: number; win_rate: number }
  metrics: any
}

export default function MirrorPage() {
  const { status } = useData()
  const [mirror, setMirror] = useState<MirrorData | null>(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/mirror')
        if (r.ok) setMirror(await r.json())
      } catch {}
    }
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const d = status.data
  if (!d || !mirror) return <div className="text-gray-500 text-sm">Loading...</div>

  const strategy = d.strategies['smart_money_mirror']
  const ds = mirror.daily_stats ?? { trades_today: 0, wins: 0, losses: 0, daily_pnl_usd: 0, win_rate: 0 }
  const trades = mirror.active_trades ?? []
  const signals = mirror.signal_log ?? []
  const tiers = mirror.tier_stats ?? {}

  return (
    <div className="space-y-5">
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Smart Money Mirror</h2>
            <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
              mirror.sse_connected ? 'bg-green-500/15 text-green-400 border border-green-500/30' : 'bg-red-500/15 text-red-400 border border-red-500/30'
            }`}>{mirror.sse_connected ? 'SSE Connected' : 'SSE Disconnected'}</span>
          </div>
          <div className="text-xs text-gray-500">
            {mirror.wallet_cache_size} wallets | {mirror.sse_stats?.events_received ?? 0} events
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5 mb-4">
          <Stat label="Session PnL" value={fmt(ds.daily_pnl_usd)} positive={ds.daily_pnl_usd >= 0} />
          <Stat label="Trades" value={`${ds.wins + ds.losses}`} sub={ds.wins + ds.losses > 0 ? `${ds.wins}W/${ds.losses}L (${(ds.win_rate * 100).toFixed(0)}%)` : 'none'} />
          <Stat label="Active" value={`${trades.length}`} />
          <Stat label="Capital" value={fmt(strategy?.capital_allocated ?? 0)} />
          <Stat label="Signals" value={`${signals.length}`} sub="received" />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[1, 2, 3].map(tier => {
          const t = tiers[tier] ?? { trades: 0, wins: 0, pnl: 0 }
          const names = { 1: 'Tier 1 (Max)', 2: 'Tier 2 (High)', 3: 'Tier 3 (Moderate)' }
          const leverages = { 1: '10x', 2: '7x', 3: '5x' }
          return (
            <div key={tier} className="rounded-lg border border-gray-800 bg-gray-900 p-4">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-2">{names[tier as 1|2|3]} - {leverages[tier as 1|2|3]}</div>
              <div className="grid grid-cols-3 gap-2 text-xs">
                <div>
                  <span className="text-gray-600">Trades: </span>
                  <span className="font-mono text-white">{t.trades}</span>
                </div>
                <div>
                  <span className="text-gray-600">WR: </span>
                  <span className="font-mono text-white">{t.trades > 0 ? `${(t.wins / t.trades * 100).toFixed(0)}%` : '--'}</span>
                </div>
                <div>
                  <span className="text-gray-600">PnL: </span>
                  <span className={`font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmt(t.pnl)}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {trades.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Active Positions</h3>
          <div className="space-y-3">
            {trades.map((t: any) => (
              <div key={t.id} className={`rounded border p-3 ${t.pnl_pct >= 0 ? 'border-green-800/30 bg-green-950/10' : 'border-red-800/30 bg-red-950/10'}`}>
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className={`rounded px-2 py-0.5 text-xs font-bold uppercase ${t.direction === 'long' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>{t.direction}</span>
                    <span className="text-sm font-medium text-white">{t.asset}</span>
                    <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-[10px] text-purple-400">T{t.conviction_tier} {t.leverage}x</span>
                    <span className="text-[10px] text-gray-600">{t.wallet_tier}</span>
                  </div>
                  <span className={`font-mono text-sm font-bold ${t.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>{t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct?.toFixed(2)}%</span>
                </div>
                <div className="flex justify-between text-[10px] text-gray-500 font-mono">
                  <span>Entry: ${t.entry_price?.toFixed(6)}</span>
                  <span>Size: ${t.collateral_usd?.toFixed(0)}</span>
                  <span>Conf: {t.confluence}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {signals.length > 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
          <h3 className="mb-3 text-xs font-medium tracking-wide text-gray-500 uppercase">Recent Signals ({signals.length})</h3>
          <div className="overflow-x-auto max-h-64 overflow-y-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wider text-gray-600">
                  <th className="pb-2 pr-3">Time</th>
                  <th className="pb-2 pr-3">Action</th>
                  <th className="pb-2 pr-3">Token</th>
                  <th className="pb-2 pr-3">Wallet</th>
                  <th className="pb-2 pr-3">Conf</th>
                  <th className="pb-2">Size</th>
                </tr>
              </thead>
              <tbody>
                {signals.slice(-20).reverse().map((s: any, i: number) => (
                  <tr key={i} className="border-t border-gray-800/50">
                    <td className="py-1 pr-3 text-gray-500">{new Date(s.timestamp * 1000).toLocaleTimeString()}</td>
                    <td className="py-1 pr-3">
                      <span className={`font-semibold ${s.action === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>{s.action}</span>
                    </td>
                    <td className="py-1 pr-3 text-white">{s.symbol}</td>
                    <td className="py-1 pr-3 text-gray-400">{s.wallet_tier}</td>
                    <td className="py-1 pr-3 font-mono text-gray-300">{s.confluence}</td>
                    <td className="py-1 font-mono text-gray-400">{s.size_sol?.toFixed(1)} SOL</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-600">{label}</div>
      <div className={`font-mono text-sm font-medium ${positive === true ? 'text-green-400' : positive === false ? 'text-red-400' : 'text-white'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
      {sub && <div className="text-[10px] text-gray-500">{sub}</div>}
    </div>
  )
}
