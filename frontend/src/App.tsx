import { usePolling } from './hooks/usePolling'
import { fetchStatus, fetchHistory, fetchEvents, fetchMarket } from './api'
import PortfolioSummary from './components/PortfolioSummary'
import StrategyCard from './components/StrategyCard'
import PerformanceChart from './components/PerformanceChart'
import MarketPanel from './components/MarketPanel'
import EventLog from './components/EventLog'
import RiskPanel from './components/RiskPanel'
import ControlPanel from './components/ControlPanel'

function ConnectionError({ message }: { message: string }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <div className="rounded-lg border border-red-800/50 bg-gray-900 px-8 py-6 text-center">
        <div className="mb-2 text-sm font-semibold text-red-400">Connection Error</div>
        <div className="text-xs text-gray-500">{message}</div>
        <div className="mt-3 text-[10px] text-gray-600">Retrying automatically...</div>
      </div>
    </div>
  )
}

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-950">
      <div className="text-sm text-gray-500">Loading...</div>
    </div>
  )
}

export default function App() {
  const status = usePolling(fetchStatus, 5000)
  const history = usePolling(fetchHistory, 30000)
  const events = usePolling(fetchEvents, 5000)
  const market = usePolling(fetchMarket, 10000)

  if (status.loading && !status.data) return <LoadingScreen />
  if (status.error && !status.data) return <ConnectionError message={status.error} />
  if (!status.data) return <LoadingScreen />

  const portfolioStatus = status.data

  return (
    <div className="min-h-screen bg-gray-950">
      <header className="border-b border-gray-800 bg-gray-950/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
          <div className="flex items-center gap-4">
            <h1 className="text-base font-bold tracking-tight text-white">Alpha Engine</h1>
            <span className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
              portfolioStatus.mode === 'live'
                ? 'bg-green-500/20 text-green-400'
                : 'bg-blue-500/20 text-blue-400'
            }`}>
              {portfolioStatus.mode}
            </span>
          </div>
          <div className="flex items-center gap-6">
            <div className="hidden items-center gap-1 sm:flex">
              <span className={`inline-block h-2 w-2 rounded-full ${
                status.error ? 'bg-red-500' : 'bg-green-500'
              }`} />
              <span className="text-[10px] text-gray-500">
                {status.error ? 'DISCONNECTED' : 'CONNECTED'}
              </span>
            </div>
            <div className="text-right">
              <div className="font-mono text-sm font-bold font-tabular text-white">
                ${portfolioStatus.total_value.toLocaleString('en-US', { minimumFractionDigits: 2 })}
              </div>
              <div className={`font-mono text-xs font-tabular ${
                portfolioStatus.total_pnl >= 0 ? 'text-profit' : 'text-loss'
              }`}>
                {portfolioStatus.total_pnl >= 0 ? '+' : ''}
                ${portfolioStatus.total_pnl.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                {' '}({portfolioStatus.total_pnl_percent >= 0 ? '+' : ''}{portfolioStatus.total_pnl_percent.toFixed(2)}%)
              </div>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-5">
            <PortfolioSummary status={portfolioStatus} />
            {history.data && <PerformanceChart history={history.data} />}

            <div>
              <h2 className="mb-3 text-sm font-medium tracking-wide text-gray-400 uppercase">Strategies</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {Object.values(portfolioStatus.strategies).map((strategy) => (
                  <StrategyCard
                    key={strategy.id}
                    strategy={strategy}
                    onToggled={status.refresh}
                  />
                ))}
              </div>
            </div>

            {events.data && <EventLog events={events.data} />}
          </div>

          <div className="space-y-5">
            <ControlPanel status={portfolioStatus} onRefresh={status.refresh} />
            <RiskPanel status={portfolioStatus} />
            {market.data && <MarketPanel market={market.data} />}
          </div>
        </div>
      </main>
    </div>
  )
}
