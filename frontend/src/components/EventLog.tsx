import type { AlphaEvent } from '../types'

const TYPE_STYLES: Record<string, string> = {
  open: 'text-green-400 bg-green-500/10',
  open_multi: 'text-green-400 bg-green-500/10',
  close: 'text-blue-400 bg-blue-500/10',
  rebalance: 'text-yellow-400 bg-yellow-500/10',
  compound: 'text-purple-400 bg-purple-500/10',
  circuit_breaker: 'text-red-400 bg-red-500/10',
  emergency_close: 'text-red-400 bg-red-500/10',
  risk_scale_down: 'text-orange-400 bg-orange-500/10',
  risk_pause: 'text-orange-400 bg-orange-500/10',
  enable: 'text-green-400 bg-green-500/10',
  disable: 'text-gray-400 bg-gray-500/10',
  reallocation: 'text-blue-400 bg-blue-500/10',
}

function formatTimestamp(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

const STRATEGY_LABELS: Record<string, string> = {
  tight_range_lp: 'Tight Range',
  jlp: 'JLP',
  fee_compounder: 'Compounder',
  multi_pool: 'Multi-Pool',
  volatile_pairs: 'Volatile',
  orchestrator: 'System',
}

function eventMessage(event: AlphaEvent): string {
  const data = event.data
  switch (event.type) {
    case 'open':
    case 'open_multi':
      return `Opened position${data.capital ? ` ($${Number(data.capital).toFixed(0)})` : ''}`
    case 'close':
      return 'Closed position'
    case 'rebalance':
      return `Rebalanced: price ${data.reason ?? 'exited range'}`
    case 'compound':
      return `Compounded $${Number(data.total_fees ?? 0).toFixed(2)} in fees`
    case 'circuit_breaker':
      return `CIRCUIT BREAKER: ${(data.reasons as string[])?.join(', ') ?? 'threshold exceeded'}`
    case 'enable':
      return 'Strategy enabled'
    case 'disable':
      return 'Strategy disabled'
    case 'risk_scale_down':
      return 'Scaled down by risk manager'
    default:
      return event.type.replace(/_/g, ' ')
  }
}

interface Props {
  events: AlphaEvent[]
}

export default function EventLog({ events }: Props) {
  const sorted = [...events].reverse()

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Event Log</h2>
      <div className="max-h-80 space-y-1 overflow-y-auto pr-1" style={{ scrollbarWidth: 'thin' }}>
        {sorted.length === 0 && (
          <div className="py-8 text-center text-sm text-gray-600">No events recorded</div>
        )}
        {sorted.map((event, i) => {
          const style = TYPE_STYLES[event.type] ?? 'text-gray-400 bg-gray-500/10'
          return (
            <div key={`${event.timestamp}-${i}`} className="flex items-start gap-2.5 rounded px-2 py-1.5 hover:bg-gray-800/50">
              <span className="mt-0.5 font-mono text-[10px] text-gray-600" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatTimestamp(event.timestamp)}
              </span>
              <span className={`mt-0.5 rounded px-1.5 py-0 text-[10px] font-medium uppercase tracking-wider ${style}`}>
                {event.type.replace(/_/g, ' ')}
              </span>
              <span className="text-[10px] font-medium text-gray-500 uppercase tracking-wider min-w-[60px]">
                {STRATEGY_LABELS[event.strategy] ?? event.strategy}
              </span>
              <span className="flex-1 text-xs text-gray-300">{eventMessage(event)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
