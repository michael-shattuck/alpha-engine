import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { DataProvider, useData } from './DataContext'
import Layout from './Layout'
import DashboardPage from './pages/DashboardPage'
import LPPage from './pages/LPPage'
import ScalperPage from './pages/ScalperPage'
import RiskPage from './pages/RiskPage'
import PortfolioPage from './pages/PortfolioPage'

function AppGuard({ children }: { children: React.ReactNode }) {
  const { status } = useData()

  if (status.loading && !status.data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950">
        <div className="text-sm text-gray-500">Loading...</div>
      </div>
    )
  }

  if (status.error && !status.data) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-950">
        <div className="rounded-lg border border-red-800/50 bg-gray-900 px-8 py-6 text-center">
          <div className="mb-2 text-sm font-semibold text-red-400">Connection Error</div>
          <div className="text-xs text-gray-500">{status.error}</div>
          <div className="mt-3 text-[10px] text-gray-600">Retrying automatically...</div>
        </div>
      </div>
    )
  }

  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <DataProvider>
        <AppGuard>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<DashboardPage />} />
              <Route path="lp" element={<LPPage />} />
              <Route path="scalper" element={<ScalperPage />} />
              <Route path="portfolio" element={<PortfolioPage />} />
              <Route path="risk" element={<RiskPage />} />
            </Route>
          </Routes>
        </AppGuard>
      </DataProvider>
    </BrowserRouter>
  )
}
