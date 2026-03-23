import { useState, useEffect } from 'react'
import type { PortfolioStatus } from '../types'

function formatUsd(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

interface ExitCost {
  total_deployed: number
  total_borrowed: number
  estimated_total_cost: number
  cost_percent: number
}

interface Props {
  status: PortfolioStatus
  onRefresh: () => void
}

export default function ControlPanel({ status, onRefresh }: Props) {
  const [leverage, setLeverage] = useState(3.0)
  const [exitCost, setExitCost] = useState<ExitCost | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const lev = Object.values(status.strategies).find(s => s.metrics?.target_leverage)?.metrics?.target_leverage
    if (typeof lev === 'number' && lev > 0) setLeverage(Math.round(lev * 10) / 10)
  }, [status])

  useEffect(() => {
    fetch('/api/exit-cost').then(r => r.json()).then(setExitCost).catch(() => {})
    const interval = setInterval(() => {
      fetch('/api/exit-cost').then(r => r.json()).then(setExitCost).catch(() => {})
    }, 30000)
    return () => clearInterval(interval)
  }, [])

  async function doAction(url: string, method = 'POST', body?: unknown) {
    setBusy(true)
    try {
      await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      onRefresh()
    } finally {
      setBusy(false)
      setConfirming(null)
    }
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Controls</h2>

      <div className="mb-5">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs text-gray-500">Leverage</span>
          <span className="font-mono text-sm font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {leverage.toFixed(1)}x
          </span>
        </div>
        <input
          type="range"
          min={1}
          max={5}
          step={0.5}
          value={leverage}
          onChange={e => setLeverage(parseFloat(e.target.value))}
          onMouseUp={() => doAction('/api/config/leverage', 'POST', { leverage })}
          onTouchEnd={() => doAction('/api/config/leverage', 'POST', { leverage })}
          className="w-full accent-blue-500"
        />
        <div className="mt-1 flex justify-between text-[10px] text-gray-600">
          <span>1x</span>
          <span>2x</span>
          <span>3x</span>
          <span>4x</span>
          <span>5x</span>
        </div>
      </div>

      {exitCost && (
        <div className="mb-5 rounded border border-gray-800 bg-gray-950/50 p-3">
          <div className="mb-2 text-xs text-gray-500">Exit Cost Estimate</div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <span className="text-gray-500">Deployed: </span>
              <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatUsd(exitCost.total_deployed)}
              </span>
            </div>
            <div>
              <span className="text-gray-500">Borrowed: </span>
              <span className="font-mono text-gray-300" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatUsd(exitCost.total_borrowed)}
              </span>
            </div>
            <div>
              <span className="text-gray-500">Exit Cost: </span>
              <span className="font-mono text-yellow-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatUsd(exitCost.estimated_total_cost)}
              </span>
            </div>
            <div>
              <span className="text-gray-500">Cost %: </span>
              <span className="font-mono text-yellow-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {exitCost.cost_percent.toFixed(3)}%
              </span>
            </div>
          </div>
        </div>
      )}

      <div className="mb-4">
        <div className="mb-2 text-xs text-gray-500">Strategy Controls</div>
        <div className="space-y-1.5">
          {Object.values(status.strategies).map(s => (
            <div key={s.id} className="flex items-center justify-between rounded bg-gray-950/50 px-3 py-2">
              <span className="text-xs text-gray-300">{s.name}</span>
              <div className="flex items-center gap-2">
                {s.enabled && s.position_count > 0 && (
                  <button
                    onClick={() => confirming === `exit-${s.id}` ? doAction(`/api/emergency-exit/${s.id}`) : setConfirming(`exit-${s.id}`)}
                    disabled={busy}
                    className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                      confirming === `exit-${s.id}`
                        ? 'bg-red-600 text-white'
                        : 'bg-red-500/10 text-red-400 hover:bg-red-500/20'
                    }`}
                  >
                    {confirming === `exit-${s.id}` ? 'CONFIRM EXIT' : 'EXIT'}
                  </button>
                )}
                <button
                  onClick={() => doAction(`/api/strategies/${s.id}/toggle`, 'POST', { enabled: !s.enabled })}
                  disabled={busy}
                  className={`rounded px-2 py-1 text-[10px] font-medium ${
                    s.enabled
                      ? 'bg-green-500/10 text-green-400'
                      : 'bg-gray-500/10 text-gray-500'
                  }`}
                >
                  {s.enabled ? 'ON' : 'OFF'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="border-t border-gray-800 pt-4">
        {confirming === 'exit-all' ? (
          <button
            onClick={() => doAction('/api/emergency-exit')}
            disabled={busy}
            className="w-full rounded bg-red-600 px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-red-500"
          >
            {busy ? 'EXITING...' : 'CONFIRM: EXIT ALL POSITIONS'}
          </button>
        ) : (
          <button
            onClick={() => setConfirming('exit-all')}
            className="w-full rounded border border-red-800/50 bg-red-500/10 px-4 py-2.5 text-sm font-medium text-red-400 transition-colors hover:bg-red-500/20"
          >
            Emergency Exit All
          </button>
        )}
        {confirming === 'exit-all' && (
          <button
            onClick={() => setConfirming(null)}
            className="mt-2 w-full rounded px-4 py-2 text-xs text-gray-500 hover:text-gray-300"
          >
            Cancel
          </button>
        )}
      </div>

      <div className="mt-3 rounded border border-gray-800 bg-gray-950/50 px-3 py-2">
        <div className="text-[10px] text-gray-600">
          Mode: <span className={status.mode === 'live' ? 'text-green-400 font-bold' : 'text-blue-400'}>{status.mode.toUpperCase()}</span>
          {status.mode === 'paper' && ' -- No real transactions. Switch to live via CLI: --mode live'}
        </div>
      </div>
    </div>
  )
}
