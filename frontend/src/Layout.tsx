import { NavLink, Outlet } from 'react-router-dom'
import { useData } from './DataContext'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard' },
  { to: '/lp', label: 'LP Strategy' },
  { to: '/scalper', label: 'Scalper' },
  { to: '/mirror', label: 'Mirror' },
  { to: '/portfolio', label: 'Portfolio' },
  { to: '/risk', label: 'Risk' },
]

export default function Layout() {
  const { status } = useData()
  const d = status.data

  return (
    <div className="min-h-screen bg-gray-950">
      <header className="border-b border-gray-800 bg-gray-950/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-4">
            <h1 className="text-base font-bold tracking-tight text-white">Alpha Engine</h1>
            {d && (
              <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                d.mode === 'live' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'
              }`}>
                {d.mode}
              </span>
            )}
          </div>

          <nav className="flex items-center gap-1">
            {NAV_ITEMS.map(item => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  `rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                    isActive
                      ? 'bg-blue-500/15 text-blue-400'
                      : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/50'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          <div className="flex items-center gap-4">
            <div className="hidden items-center gap-1 sm:flex">
              <span className={`inline-block h-2 w-2 rounded-full ${
                status.error ? 'bg-red-500' : 'bg-green-500'
              }`} />
              <span className="text-[10px] text-gray-500">
                {status.error ? 'DISCONNECTED' : 'CONNECTED'}
              </span>
            </div>
            {d && (
              <div className="text-right">
                <div className="font-mono text-sm font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                  ${d.total_value.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                </div>
                <div className={`font-mono text-[10px] ${d.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {d.total_pnl >= 0 ? '+' : ''}${d.total_pnl.toFixed(2)} ({d.total_pnl_percent >= 0 ? '+' : ''}{d.total_pnl_percent.toFixed(2)}%)
                </div>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-5 sm:px-6">
        <Outlet />
      </main>
    </div>
  )
}
