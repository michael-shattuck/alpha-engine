import type { PortfolioStatus, HistoryPoint, AlphaEvent, MarketData } from './types'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export function fetchStatus(): Promise<PortfolioStatus> {
  return request<PortfolioStatus>('/api/status')
}

export async function toggleStrategy(id: string): Promise<void> {
  const status = await fetchStatus()
  const strategy = status.strategies[id]
  const newEnabled = strategy ? !strategy.enabled : true
  await request(`/api/strategies/${id}/toggle`, {
    method: 'POST',
    body: JSON.stringify({ enabled: newEnabled }),
  })
}

export function fetchHistory(): Promise<HistoryPoint[]> {
  return request<HistoryPoint[]>('/api/history')
}

export function fetchEvents(): Promise<AlphaEvent[]> {
  return request<AlphaEvent[]>('/api/events')
}

export function fetchMarket(): Promise<MarketData> {
  return request<MarketData>('/api/market')
}

export function updateAllocation(allocations: Record<string, number>): Promise<void> {
  return request<void>('/api/config/allocation', {
    method: 'POST',
    body: JSON.stringify({ allocations }),
  })
}

export interface WalletInfo {
  sol_balance: number
  usdc_balance: number
  sol_price: number
  total_usd: number
  marginfi: {
    deposited_sol: number
    borrowed_usdc: number
    has_position: boolean
  }
}

export interface AlertEntry {
  timestamp: number
  level: string
  title: string
  message: string
}

export function fetchWallet(): Promise<WalletInfo> {
  return request<WalletInfo>('/api/wallet')
}

export function fetchAlerts(): Promise<AlertEntry[]> {
  return request<AlertEntry[]>('/api/alerts')
}
