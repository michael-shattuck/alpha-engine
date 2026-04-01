import time
import logging
from typing import Optional

from server.strategies.base import BaseStrategy, StrategyPosition
from server.config import JLP_POOL

log = logging.getLogger("jlp")

JLP_BASE_APY = 25.0


class JLPStrategy(BaseStrategy):
    STRATEGY_ID = "jlp"
    STRATEGY_NAME = "Jupiter LP"

    def __init__(self, mode: str = "paper"):
        super().__init__(mode=mode)
        self._jlp_apy = JLP_BASE_APY

    async def evaluate(self, market_data: dict) -> dict:
        if not self.active_positions and self.capital_allocated > 0:
            return {
                "action": "open",
                "pool": JLP_POOL,
                "deposit_usd": self.capital_allocated,
            }

        if self.active_positions:
            return {"action": "hold", "reason": "position_earning"}

        return {"action": "wait", "reason": "no_capital"}

    async def execute(self, action: dict, market_data: dict) -> Optional[StrategyPosition]:
        sol_price = market_data.get("sol_price", 0)
        if sol_price <= 0:
            return None

        if action["action"] == "open":
            deposit = action["deposit_usd"]
            position = StrategyPosition(
                id=f"{self.STRATEGY_ID}_{int(time.time())}",
                pool=JLP_POOL,
                entry_price=sol_price,
                deposit_usd=deposit,
                current_value_usd=deposit,
                in_range=True,
                metadata={"entry_apy": self._jlp_apy},
            )
            self.positions.append(position)
            self.status = "active"
            log.info(f"JLP opened: ${deposit:.2f} at {self._jlp_apy:.0f}% APY")
            return position

        return None

    async def update(self, market_data: dict):
        now = time.time()

        jlp_apy = market_data.get("jlp_apy", JLP_BASE_APY)
        self._jlp_apy = jlp_apy

        for position in self.active_positions:
            hours_elapsed = (now - position.last_update) / 3600
            if hours_elapsed <= 0:
                continue

            hourly_rate = jlp_apy / 100 / 365 / 24
            position.fees_earned_usd += hourly_rate * hours_elapsed * position.deposit_usd

            sol_price = market_data.get("sol_price", 0)
            if position.entry_price > 0 and sol_price > 0:
                price_ratio = sol_price / position.entry_price
                sol_exposure = 0.45
                nav_change = 1 + (price_ratio - 1) * sol_exposure
                position.current_value_usd = position.deposit_usd * nav_change + position.fees_earned_usd

            position.in_range = True
            position.hours_in_range += hours_elapsed
            position.last_update = now

        effective_apy = 0.0
        if self.active_positions:
            pos = self.active_positions[0]
            total_return = (pos.current_value_usd - pos.deposit_usd) / pos.deposit_usd if pos.deposit_usd > 0 else 0
            if pos.age_hours > 0:
                effective_apy = total_return / pos.age_hours * 8760 * 100

        self.last_update = now
        self.metrics = {
            "jlp_apy": jlp_apy,
            "effective_apy": effective_apy,
            "sol_exposure": 0.45,
            "total_fees": self.total_fees,
        }
