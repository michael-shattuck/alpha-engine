import type { WalletInfo } from '../api'

function formatUsd(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

interface Props {
  wallet: WalletInfo
}

export default function WalletPanel({ wallet }: Props) {
  const mfi = wallet.marginfi

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-5">
      <h2 className="mb-4 text-sm font-medium tracking-wide text-gray-400 uppercase">Wallet</h2>

      <div className="space-y-2 mb-4">
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">SOL Balance</span>
          <span className="font-mono text-sm text-gray-200" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {wallet.sol_balance.toFixed(4)} SOL
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-xs text-gray-500">USDC Balance</span>
          <span className="font-mono text-sm text-gray-200" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(wallet.usdc_balance)}
          </span>
        </div>
        <div className="flex items-center justify-between border-t border-gray-800 pt-2">
          <span className="text-xs text-gray-500">Total (wallet)</span>
          <span className="font-mono text-sm font-medium text-white" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {formatUsd(wallet.total_usd)}
          </span>
        </div>
      </div>

      {mfi.has_position && (
        <div className="rounded border border-yellow-800/40 bg-yellow-950/20 p-3">
          <div className="mb-2 text-xs font-medium text-yellow-400">MarginFi Position</div>
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-gray-400">Deposited</span>
              <span className="font-mono text-xs text-gray-200" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {mfi.deposited_sol.toFixed(4)} SOL
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-gray-400">Borrowed</span>
              <span className="font-mono text-xs text-red-400" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatUsd(mfi.borrowed_usdc)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-gray-400">Collateral Value</span>
              <span className="font-mono text-xs text-gray-200" style={{ fontVariantNumeric: 'tabular-nums' }}>
                {formatUsd(mfi.deposited_sol * wallet.sol_price)}
              </span>
            </div>
            {mfi.borrowed_usdc > 0 && (
              <div className="flex items-center justify-between border-t border-gray-800 pt-1.5">
                <span className="text-[11px] text-gray-400">Health</span>
                <span className={`font-mono text-xs font-medium ${
                  (mfi.deposited_sol * wallet.sol_price / mfi.borrowed_usdc) > 1.5
                    ? 'text-green-400'
                    : (mfi.deposited_sol * wallet.sol_price / mfi.borrowed_usdc) > 1.2
                    ? 'text-yellow-400'
                    : 'text-red-400'
                }`} style={{ fontVariantNumeric: 'tabular-nums' }}>
                  {(mfi.deposited_sol * wallet.sol_price / mfi.borrowed_usdc).toFixed(2)}x
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {!mfi.has_position && (
        <div className="text-center text-[11px] text-gray-600 py-2">
          No active MarginFi position
        </div>
      )}
    </div>
  )
}
