import type { AlertEntry } from '../api'

const LEVEL_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  info: { bg: 'bg-blue-500/10', text: 'text-blue-400', border: 'border-blue-500/20' },
  warning: { bg: 'bg-yellow-500/10', text: 'text-yellow-400', border: 'border-yellow-500/20' },
  error: { bg: 'bg-red-500/10', text: 'text-red-400', border: 'border-red-500/20' },
  critical: { bg: 'bg-red-500/20', text: 'text-red-300', border: 'border-red-500/40' },
}

function timeAgo(ts: number): string {
  const secs = Math.floor(Date.now() / 1000 - ts)
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

interface Props {
  alerts: AlertEntry[]
}

export default function AlertLog({ alerts }: Props) {
  const recent = [...alerts].reverse().slice(0, 20)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">Alerts</h2>
      <div className="space-y-1.5 max-h-64 overflow-y-auto">
        {recent.map((alert, i) => {
          const style = LEVEL_STYLES[alert.level] ?? LEVEL_STYLES['info']!
          return (
            <div key={i} className={`rounded border px-3 py-2 ${style.bg} ${style.border}`}>
              <div className="flex items-center justify-between mb-0.5">
                <span className={`text-xs font-medium ${style.text}`}>{alert.title}</span>
                <span className="text-[10px] text-gray-600">{timeAgo(alert.timestamp)}</span>
              </div>
              <div className="text-[11px] text-gray-400 break-all">{alert.message}</div>
            </div>
          )
        })}
        {recent.length === 0 && (
          <div className="py-3 text-center text-xs text-gray-600">No alerts yet</div>
        )}
      </div>
    </div>
  )
}
