import { useData } from '../DataContext'
import { useState } from 'react'

function fmt(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

export default function SettingsPage() {
  const { status } = useData()
  const d = status.data
  if (!d) return null

  const strategies = Object.entries(d.strategies ?? {}) as [string, any][]

  return (
    <div className="space-y-5">
      <h1 className="text-sm font-medium tracking-wide text-gray-400 uppercase">System Settings</h1>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <StrategyControls strategies={strategies} />
        <SystemActions mode={d.mode} circuitBreaker={d.circuit_breaker_active} />
      </div>

      <AllocationPanel strategies={strategies} />
      <RiskConfig />
    </div>
  )
}

function StrategyControls({ strategies }: { strategies: [string, any][] }) {
  const [loading, setLoading] = useState<string | null>(null)

  const toggle = async (id: string, enabled: boolean) => {
    setLoading(id)
    try {
      await fetch(`/api/strategies/${id}/toggle`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      })
    } catch {}
    setLoading(null)
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-xs font-medium tracking-wide text-gray-500 uppercase mb-3">Strategy Controls</h2>
      <div className="space-y-2">
        {strategies.map(([id, s]) => (
          <div key={id} className="flex items-center justify-between rounded border border-gray-800 p-3">
            <div>
              <div className="text-sm font-medium text-white">{s.name}</div>
              <div className="text-[10px] text-gray-500">
                {fmt(s.capital_allocated)} | {s.status}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {s.enabled && s.positions?.length > 0 && (
                <ExitButton strategyId={id} />
              )}
              <button
                onClick={() => toggle(id, !s.enabled)}
                disabled={loading === id}
                className={`rounded px-3 py-1.5 text-xs font-semibold transition-colors ${
                  s.enabled
                    ? 'bg-green-500/15 text-green-400 border border-green-500/30 hover:bg-green-500/25'
                    : 'bg-gray-500/15 text-gray-400 border border-gray-500/30 hover:bg-gray-500/25'
                }`}
              >
                {loading === id ? '...' : s.enabled ? 'ON' : 'OFF'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function ExitButton({ strategyId }: { strategyId: string }) {
  const [confirming, setConfirming] = useState(false)

  const exit = async () => {
    await fetch(`/api/emergency-exit/${strategyId}`, { method: 'POST' })
    setConfirming(false)
  }

  if (confirming) {
    return (
      <div className="flex gap-1">
        <button onClick={exit} className="rounded px-2 py-1 text-[10px] bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30">Confirm</button>
        <button onClick={() => setConfirming(false)} className="rounded px-2 py-1 text-[10px] bg-gray-500/20 text-gray-400">Cancel</button>
      </div>
    )
  }

  return (
    <button onClick={() => setConfirming(true)} className="rounded px-2 py-1 text-[10px] text-red-400 border border-red-800/30 hover:bg-red-500/10">
      EXIT
    </button>
  )
}

function SystemActions({ mode, circuitBreaker }: { mode: string; circuitBreaker: boolean }) {
  const [loading, setLoading] = useState<string | null>(null)

  const action = async (endpoint: string, body?: any) => {
    setLoading(endpoint)
    try {
      await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
    } catch {}
    setLoading(null)
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-xs font-medium tracking-wide text-gray-500 uppercase mb-3">System Actions</h2>
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm text-white">Mode</div>
            <div className="text-[10px] text-gray-500">Current: {mode}</div>
          </div>
          <span className={`rounded px-3 py-1.5 text-xs font-semibold ${
            mode === 'live' ? 'bg-green-500/15 text-green-400' : 'bg-blue-500/15 text-blue-400'
          }`}>{mode.toUpperCase()}</span>
        </div>

        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm text-white">Circuit Breaker</div>
            <div className="text-[10px] text-gray-500">Emergency trading halt</div>
          </div>
          <span className={`rounded px-3 py-1.5 text-xs font-semibold ${
            circuitBreaker ? 'bg-red-500/15 text-red-400' : 'bg-gray-500/15 text-gray-400'
          }`}>{circuitBreaker ? 'ACTIVE' : 'OFF'}</span>
        </div>

        <hr className="border-gray-800" />

        <button
          onClick={() => action('/api/emergency-exit')}
          disabled={loading === '/api/emergency-exit'}
          className="w-full rounded border border-red-800/50 bg-red-950/30 px-4 py-2.5 text-sm font-semibold text-red-400 hover:bg-red-950/50 transition-colors"
        >
          {loading === '/api/emergency-exit' ? 'Closing...' : 'Emergency Exit All Positions'}
        </button>

        <button
          onClick={() => action('/api/scalper/trading_block', { blocked: true })}
          disabled={loading === '/api/scalper/trading_block'}
          className="w-full rounded border border-yellow-800/50 bg-yellow-950/30 px-4 py-2 text-xs font-semibold text-yellow-400 hover:bg-yellow-950/50 transition-colors"
        >
          Pause Scalper Trading
        </button>

        <button
          onClick={() => action('/api/scalper/trading_block', { blocked: false })}
          disabled={loading === '/api/scalper/trading_block'}
          className="w-full rounded border border-green-800/50 bg-green-950/30 px-4 py-2 text-xs font-semibold text-green-400 hover:bg-green-950/50 transition-colors"
        >
          Resume Scalper Trading
        </button>
      </div>
    </div>
  )
}

function AllocationPanel({ strategies }: { strategies: [string, any][] }) {
  const enabled = strategies.filter(([, s]) => s.capital_allocated > 0)
  const total = enabled.reduce((sum, [, s]) => sum + s.capital_allocated, 0)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-xs font-medium tracking-wide text-gray-500 uppercase mb-3">Capital Allocation</h2>
      <div className="space-y-3">
        {enabled.map(([id, s]) => {
          const pct = total > 0 ? (s.capital_allocated / total * 100) : 0
          return (
            <div key={id}>
              <div className="flex justify-between text-xs mb-1">
                <span className="text-gray-400">{s.name}</span>
                <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {fmt(s.capital_allocated)} ({pct.toFixed(0)}%)
                </span>
              </div>
              <div className="h-2 rounded-full bg-gray-800 overflow-hidden">
                <div className={`h-full rounded-full transition-all ${
                  s.enabled ? 'bg-blue-500' : 'bg-gray-600'
                }`} style={{ width: `${pct}%` }} />
              </div>
            </div>
          )
        })}
        <div className="flex justify-between text-xs border-t border-gray-800 pt-2">
          <span className="text-gray-500">Total</span>
          <span className="font-mono text-white font-medium">{fmt(total)}</span>
        </div>
      </div>
    </div>
  )
}

function RiskConfig() {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="text-xs font-medium tracking-wide text-gray-500 uppercase mb-3">Risk Parameters</h2>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 text-xs">
        <ConfigItem label="Max Drawdown" value="10%" />
        <ConfigItem label="Circuit Breaker" value="5% / 1h" />
        <ConfigItem label="Daily Loss Limit" value="5%" />
        <ConfigItem label="Max Position Stop" value="12%" />
        <ConfigItem label="Trailing Stop" value="15%" />
        <ConfigItem label="SOL Crash Halt" value="-15%" />
        <ConfigItem label="Max Leverage" value="10x" />
        <ConfigItem label="Rebalance Cap" value="12/day" />
        <ConfigItem label="Scalper SL" value="0.5%" />
        <ConfigItem label="Scalper Trail" value="0.8%" />
        <ConfigItem label="Scalper Max Hold" value="30m" />
        <ConfigItem label="Mirror Max Hold" value="6-24h" />
      </div>
    </div>
  )
}

function ConfigItem({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-gray-600">{label}</div>
      <div className="font-mono text-gray-300 mt-0.5" style={{ fontVariantNumeric: 'tabular-nums' }}>{value}</div>
    </div>
  )
}
