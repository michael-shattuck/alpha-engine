import { NavLink, Outlet } from 'react-router-dom'
import { useData } from './DataContext'
import { useState } from 'react'

const NAV_SECTIONS = [
  {
    label: 'Overview',
    items: [
      { to: '/', label: 'Dashboard', icon: 'D' },
      { to: '/portfolio', label: 'Portfolio', icon: 'P' },
      { to: '/risk', label: 'Risk', icon: 'R' },
    ],
  },
  {
    label: 'Strategies',
    items: [
      { to: '/scalper', label: 'Scalper', icon: 'S' },
      { to: '/mirror', label: 'Mirror', icon: 'M' },
      { to: '/lp', label: 'LP Strategy', icon: 'L' },
    ],
  },
  {
    label: 'System',
    items: [
      { to: '/settings', label: 'Settings', icon: 'G' },
    ],
  },
]

export default function Layout() {
  const { status } = useData()
  const d = status.data
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="min-h-screen bg-gray-950 lg:flex">
      {sidebarOpen && (
        <div className="fixed inset-0 bg-black/50 z-30 lg:hidden" onClick={() => setSidebarOpen(false)} />
      )}

      <aside className={`
        fixed h-full z-40 w-56 border-r border-gray-800 bg-gray-950 flex flex-col transition-transform
        lg:translate-x-0 lg:static lg:z-auto
        ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
      `}>
        <div className="p-4 border-b border-gray-800">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-bold tracking-tight text-white">Alpha Engine</h1>
            {d && (
              <span className={`rounded px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider ${
                d.mode === 'live' ? 'bg-green-500/20 text-green-400' : 'bg-blue-500/20 text-blue-400'
              }`}>{d.mode}</span>
            )}
          </div>
          <div className="flex items-center gap-1 mt-2">
            <span className={`inline-block h-1.5 w-1.5 rounded-full ${status.error ? 'bg-red-500' : 'bg-green-500'}`} />
            <span className="text-[9px] text-gray-500">{status.error ? 'DISCONNECTED' : 'CONNECTED'}</span>
          </div>
        </div>

        {d && (
          <div className="p-4 border-b border-gray-800">
            <div className="font-mono text-lg font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
              ${d.total_value?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
            <div className={`font-mono text-[10px] ${(d.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`} style={{ fontVariantNumeric: 'tabular-nums' }}>
              {(d.total_pnl ?? 0) >= 0 ? '+' : ''}${(d.total_pnl ?? 0).toFixed(2)} ({(d.total_pnl_percent ?? 0) >= 0 ? '+' : ''}{(d.total_pnl_percent ?? 0).toFixed(2)}%)
            </div>
            <div className={`text-[9px] mt-1 font-semibold ${
              d.risk_level === 'low' ? 'text-green-400' :
              d.risk_level === 'medium' ? 'text-yellow-400' :
              d.risk_level === 'high' ? 'text-orange-400' : 'text-red-400'
            }`}>Risk: {d.risk_level?.toUpperCase()}</div>
          </div>
        )}

        <nav className="flex-1 overflow-y-auto py-2">
          {NAV_SECTIONS.map(section => (
            <div key={section.label} className="mb-2">
              <div className="px-4 py-1 text-[9px] uppercase tracking-wider text-gray-600 font-semibold">{section.label}</div>
              {section.items.map(item => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === '/'}
                  onClick={() => setSidebarOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 px-4 py-2 text-xs font-medium transition-colors ${
                      isActive
                        ? 'bg-blue-500/10 text-blue-400 border-r-2 border-blue-400'
                        : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/30'
                    }`
                  }
                >
                  <span className="w-5 h-5 rounded bg-gray-800 flex items-center justify-center text-[10px] font-bold text-gray-400">{item.icon}</span>
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {d && (
          <div className="p-3 border-t border-gray-800 text-[9px] text-gray-600">
            <div>Uptime: {(d.uptime_hours ?? 0).toFixed(1)}h</div>
            <div>{Object.values(d.strategies ?? {}).filter((s: any) => s.enabled).length} strategies active</div>
          </div>
        )}
      </aside>

      <div className="flex-1 min-h-screen">
        <header className="lg:hidden sticky top-0 z-20 border-b border-gray-800 bg-gray-950/95 backdrop-blur-sm">
          <div className="flex items-center justify-between px-4 py-2.5">
            <button onClick={() => setSidebarOpen(true)} className="text-gray-400 hover:text-white p-1">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><rect y="3" width="20" height="2" rx="1"/><rect y="9" width="20" height="2" rx="1"/><rect y="15" width="20" height="2" rx="1"/></svg>
            </button>
            <span className="text-xs font-bold text-white">Alpha Engine</span>
            {d && (
              <div className="font-mono text-xs font-bold text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
                ${d.total_value?.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}
              </div>
            )}
          </div>
        </header>

        <main className="p-4 lg:p-5">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
