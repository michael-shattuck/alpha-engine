import { createContext, useContext } from 'react'
import { usePolling } from './hooks/usePolling'
import {
  fetchStatus, fetchHistory, fetchEvents, fetchMarket,
  fetchWallet, fetchAlerts, fetchLifecycle, fetchOptimizer, fetchScalper,
} from './api'
import type { PortfolioStatus } from './types'
import type { WalletInfo, AlertEntry, LifecycleState, OptimizerState, ScalperState } from './api'
import type { HistoryPoint, AlphaEvent, MarketData } from './types'

interface PollResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  refresh: () => void
}

interface DataContextType {
  status: PollResult<PortfolioStatus>
  history: PollResult<HistoryPoint[]>
  events: PollResult<AlphaEvent[]>
  market: PollResult<MarketData>
  wallet: PollResult<WalletInfo>
  alerts: PollResult<AlertEntry[]>
  lifecycle: PollResult<LifecycleState>
  optimizer: PollResult<OptimizerState>
  scalper: PollResult<ScalperState>
}

const Ctx = createContext<DataContextType | null>(null)

export function DataProvider({ children }: { children: React.ReactNode }) {
  const status = usePolling(fetchStatus, 5000)
  const history = usePolling(fetchHistory, 30000)
  const events = usePolling(fetchEvents, 5000)
  const market = usePolling(fetchMarket, 10000)
  const wallet = usePolling(fetchWallet, 15000)
  const alerts = usePolling(fetchAlerts, 10000)
  const lifecycle = usePolling(fetchLifecycle, 5000)
  const optimizer = usePolling(fetchOptimizer, 15000)
  const scalper = usePolling(fetchScalper, 5000)

  return (
    <Ctx.Provider value={{ status, history, events, market, wallet, alerts, lifecycle, optimizer, scalper }}>
      {children}
    </Ctx.Provider>
  )
}

export function useData() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useData must be inside DataProvider')
  return ctx
}
