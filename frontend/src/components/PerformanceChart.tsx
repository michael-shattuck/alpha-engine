import { useMemo, useState } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  Legend,
} from 'recharts'
import type { HistoryPoint, TimeRange } from '../types'

const STRATEGY_COLORS: Record<string, string> = {
  tight_range_lp: '#3b82f6',
  jlp: '#8b5cf6',
  fee_compounder: '#06b6d4',
  multi_pool: '#f59e0b',
  volatile_pairs: '#ec4899',
}

const STRATEGY_LABELS: Record<string, string> = {
  tight_range_lp: 'Tight Range LP',
  jlp: 'Jupiter Perps LP',
  fee_compounder: 'Fee Compounder',
  multi_pool: 'Multi-Pool',
  volatile_pairs: 'Volatile Pairs',
}

const TIME_RANGES: { value: TimeRange; label: string }[] = [
  { value: '1h', label: '1H' },
  { value: '6h', label: '6H' },
  { value: '24h', label: '24H' },
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
]

const RANGE_DURATIONS: Record<TimeRange, number> = {
  '1h': 3600_000,
  '6h': 21600_000,
  '24h': 86400_000,
  '7d': 604800_000,
  '30d': 2592000_000,
}

function formatUsd(value: number): string {
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}k`
  return `$${value.toFixed(0)}`
}

interface Props {
  history: HistoryPoint[]
}

export default function PerformanceChart({ history }: Props) {
  const [range, setRange] = useState<TimeRange>('24h')

  const filtered = useMemo(() => {
    const cutoff = Date.now() - RANGE_DURATIONS[range]
    return history.filter(p => p.timestamp * 1000 >= cutoff)
  }, [history, range])

  const chartData = useMemo(() => {
    return filtered.map(p => {
      const base: Record<string, unknown> = {
        time: p.timestamp * 1000,
        total: p.total_value,
      }
      for (const [key, val] of Object.entries(p.strategy_values)) {
        base[key] = val
      }
      return base
    })
  }, [filtered])

  const strategyKeys = useMemo(() => {
    if (filtered.length === 0) return []
    const keys = new Set<string>()
    for (const p of filtered) {
      for (const k of Object.keys(p.strategy_values)) {
        keys.add(k)
      }
    }
    return Array.from(keys)
  }, [filtered])

  function formatTime(ts: number): string {
    const d = new Date(ts)
    if (range === '7d' || range === '30d') {
      return `${d.getMonth() + 1}/${d.getDate()}`
    }
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  if (chartData.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
        <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Performance</h2>
        <div className="flex h-48 items-center justify-center text-sm text-gray-600">
          No history data available
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-medium tracking-wide text-gray-400 uppercase">Performance</h2>
        <div className="flex gap-1">
          {TIME_RANGES.map(tr => (
            <button
              key={tr.value}
              onClick={() => setRange(tr.value)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                range === tr.value
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {tr.label}
            </button>
          ))}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="time"
            type="number"
            domain={['dataMin', 'dataMax']}
            tickFormatter={formatTime}
            stroke="#4b5563"
            tick={{ fontSize: 11, fill: '#6b7280' }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tickFormatter={formatUsd}
            stroke="#4b5563"
            tick={{ fontSize: 11, fill: '#6b7280' }}
            tickLine={false}
            axisLine={false}
            width={55}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: '6px',
              fontSize: '12px',
              fontFamily: 'JetBrains Mono, monospace',
            }}
            labelFormatter={(val) => new Date(val as number).toLocaleString()}
            formatter={(val: number, name: string) => [
              `$${val.toFixed(2)}`,
              name === 'total' ? 'Total' : (STRATEGY_LABELS[name] ?? name),
            ]}
          />
          <Legend
            formatter={(val: string) => val === 'total' ? 'Total' : (STRATEGY_LABELS[val] ?? val)}
            wrapperStyle={{ fontSize: '11px', paddingTop: '8px' }}
          />
          <Line
            type="monotone"
            dataKey="total"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
          />
          {strategyKeys.map(key => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={STRATEGY_COLORS[key] ?? '#6b7280'}
              strokeWidth={1}
              dot={false}
              strokeDasharray="4 2"
              activeDot={{ r: 2 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
