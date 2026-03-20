export interface StrategyState {
  id: string
  name: string
  enabled: boolean
  mode: string
  capital_allocated: number
  target_allocation: number
  current_value: number
  total_fees: number
  total_pnl: number
  total_pnl_percent: number
  position_count: number
  positions: unknown[]
  status: string
  last_update: number
  error: string
  metrics: Record<string, unknown>
}

export interface PortfolioStatus {
  mode: 'paper' | 'live'
  capital: number
  total_value: number
  total_pnl: number
  total_pnl_percent: number
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  circuit_breaker_active: boolean
  sol_price: number
  total_fees: number
  projected_dpy: number
  projected_mpy: number
  projected_apy: number
  strategies: Record<string, StrategyState>
  uptime_hours: number
  last_update: number
  guardian: Record<string, unknown>
  ai_reasoning: string[]
}

export interface HistoryPoint {
  timestamp: number
  total_value: number
  total_pnl: number
  total_pnl_percent: number
  strategy_values: Record<string, number>
  risk_level: string
  sol_price: number
}

export interface MarketData {
  sol_price: number
  sol_change_1h: number
  sol_change_24h: number
  volatility_1h: number
  volatility_24h: number
  pool_apys: Record<string, number>
  jlp_apy: number
  timestamp: number
}

export interface AlphaEvent {
  timestamp: number
  type: string
  strategy: string
  data: Record<string, unknown>
}

export type TimeRange = '1h' | '6h' | '24h' | '7d' | '30d'
