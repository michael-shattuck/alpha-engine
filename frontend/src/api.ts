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

export interface LifecycleState {
  phase: string
  position_mint: string | null
  borrowed_usd: number
  equity_usd: number
  leverage: number
  range_pct: number
  error: string
  retries: number
}

export interface OptimizerState {
  current_pool_apy: number
  volatility: number
  trend_1h: number
  actual_fee_apy: number
  return_floor: number
  optimized: {
    leverage: number
    range_pct: number
    gross_apy: number
    net_apy: number
    monthly: number
    rebalance_cost: number
    rebalances_per_day: number
    concentration: number
  }
  ranked_pools: Array<{
    pool: string
    pool_apy: number
    monthly: number
    leverage: number
    range_pct: number
  }>
}

export function fetchLifecycle(): Promise<LifecycleState> {
  return request<LifecycleState>('/api/lifecycle')
}

export function fetchOptimizer(): Promise<OptimizerState> {
  return request<OptimizerState>('/api/optimizer')
}

export interface ScalperTrade {
  id: string
  direction: string
  trade_type: string
  entry_price: number
  current_price: number
  stop_loss: number
  take_profit: number
  pnl_usd: number
  pnl_pct: number
  leverage: number
  regime_at_entry: string
  signal_confidence: number
}

export interface ScalperAsset {
  symbol: string
  price: number
  regime: string
  regime_confidence: number
  rsi_5m: number
  adx: number
  bbw: number
  velocity: number
  signal: string
  signal_confidence: number
  signal_reason: string
  active_trade: {
    direction: string
    entry_price: number
    pnl_pct: number
    stop_loss: number
    take_profit: number
  } | null
}

export interface ScalperState {
  assets: ScalperAsset[]
  active_trades: ScalperTrade[]
  daily_stats: {
    trades_today: number
    wins: number
    losses: number
    daily_pnl_usd: number
    daily_pnl_pct: number
    win_rate: number
  }
  drift_account?: {
    collateral: number
    unrealized_pnl: number
    net_value: number
    starting_capital: number
    total_pnl: number
  }
  indicators: Record<string, number | string>
  signal_performance: {
    total_signals: number
    win_rate: number
    profit_factor: number
    by_regime: Record<string, { count: number; win_rate: number; avg_pnl: number }>
  }
  regime: string
  regime_confidence: number
}

export function fetchScalper(): Promise<ScalperState> {
  return request<ScalperState>('/api/scalper')
}

export interface PortfolioData {
  sol_price: number
  wallet: {
    sol_balance: number
    sol_usd: number
    usdc_balance: number
    total_usd: number
  }
  drift: {
    collateral: number
    free_collateral: number
    unrealized_pnl: number
    positions: Array<{
      market_index: number
      direction: string
      size_tokens?: number
      size?: number
      entry_price?: number
      notional?: number
      quote_entry?: number
      pnl?: number
    }>
  }
  total_usd: number
}

export function fetchPortfolio(): Promise<PortfolioData> {
  return request<PortfolioData>('/api/portfolio')
}
