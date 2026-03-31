import time
import logging

from solders.pubkey import Pubkey
from server.execution.orca import OrcaExecutor, _derive_position_pda

log = logging.getLogger("fee_tracker")


class FeeTracker:
    def __init__(self, orca: OrcaExecutor):
        self.orca = orca
        self.last_fee_a: int = 0
        self.last_fee_b: int = 0
        self.last_read_time: float = 0
        self.cumulative_fee_sol: float = 0.0
        self.cumulative_fee_usdc: float = 0.0
        self.hourly_fee_rate: float = 0.0
        self.reads: list[dict] = []

    async def read_fees(self, position_mint: str) -> dict:
        mint = Pubkey.from_string(position_mint)
        data = await self.orca._fetch_position_data(mint)
        fee_a = data["fee_owed_a"]
        fee_b = data["fee_owed_b"]
        now = time.time()

        sol_fees = fee_a / 1e9
        usdc_fees = fee_b / 1e6

        if self.last_read_time > 0:
            dt_hours = (now - self.last_read_time) / 3600
            delta_sol = (fee_a - self.last_fee_a) / 1e9
            delta_usdc = (fee_b - self.last_fee_b) / 1e6

            if dt_hours > 0 and delta_sol >= 0 and delta_usdc >= 0:
                self.cumulative_fee_sol += delta_sol
                self.cumulative_fee_usdc += delta_usdc

                self.reads.append({
                    "t": now,
                    "dt_h": dt_hours,
                    "sol": delta_sol,
                    "usdc": delta_usdc,
                })
                cutoff = now - 6 * 3600
                self.reads = [r for r in self.reads if r["t"] > cutoff]

                total_sol = sum(r["sol"] for r in self.reads)
                total_usdc = sum(r["usdc"] for r in self.reads)
                total_hours = sum(r["dt_h"] for r in self.reads)
                if total_hours > 0:
                    self.hourly_fee_rate = (total_sol + total_usdc) / total_hours

        self.last_fee_a = fee_a
        self.last_fee_b = fee_b
        self.last_read_time = now

        return {
            "fee_sol": sol_fees,
            "fee_usdc": usdc_fees,
            "cumulative_sol": self.cumulative_fee_sol,
            "cumulative_usdc": self.cumulative_fee_usdc,
            "hourly_rate_usd": self.hourly_fee_rate,
        }

    def get_actual_apy(self, deposit_usd: float) -> float:
        if deposit_usd <= 0 or self.hourly_fee_rate <= 0:
            return 0
        return self.hourly_fee_rate / deposit_usd * 8760 * 100

    def reset(self):
        self.last_fee_a = 0
        self.last_fee_b = 0
        self.last_read_time = 0
        self.cumulative_fee_sol = 0.0
        self.cumulative_fee_usdc = 0.0
        self.hourly_fee_rate = 0.0
        self.reads = []
